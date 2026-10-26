"""Persist Nexon's public hero statistics, shared by the website and bot."""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import math
import os
from pathlib import Path
import re
import time

import httpx

SOURCE_URL = "https://overwatch.nexon.com/hero/rate"
REFRESH_SECONDS = 21600
ACTIVE_SECONDS = 86400
RETAIN_SECONDS = 7 * 86400
MAX_VARIANTS = 128
MAX_CACHE_BYTES = 32 * 1024 * 1024
DEFAULT_FILTERS = {
    "role": "all",
    "rq": "2",
    "rank": "master",
    "map": "all",
    "input": "pc",
    "region": "korea",
}
FILTER_KEYS = {
    "role": "roles",
    "rq": "rulesetQueues",
    "rank": "ranks",
    "map": "maps",
    "input": "inputs",
    "region": "regions",
}
CATALOG = json.loads(
    Path(__file__).with_name("hero_rates_catalog.json").read_text(encoding="utf-8")
)
logger = logging.getLogger("ow_api_server.hero_rates")


def parse_public_statistics(html: str) -> dict:
    """Read the public page's SSR data without executing downloaded JavaScript."""
    match = re.search(
        r'<script\b[^>]*\bid="__NUXT_DATA__"[^>]*>(.*?)</script>', html, re.S
    )
    if not match:
        raise ValueError("原站页面未包含统计数据")
    values = json.loads(match[1])
    if not isinstance(values, list):
        raise ValueError("原站统计数据结构异常")

    def unpack(index, depth=0):
        if depth > 30 or not isinstance(index, int) or index >= len(values):
            raise ValueError("原站统计数据结构异常")
        if index < 0:
            return None
        value = values[index]
        if isinstance(value, dict):
            return {key: unpack(item, depth + 1) for key, item in value.items()}
        if isinstance(value, list):
            if value and value[0] in (
                "ShallowReactive",
                "Reactive",
                "Ref",
                "ShallowRef",
            ):
                return unpack(value[1], depth + 1)
            return [unpack(item, depth + 1) for item in value]
        return value

    # Locate the named SSR result rather than depending on fixed array offsets.
    roots = {
        value["hero-rate-list"]
        for value in values
        if isinstance(value, dict)
        and isinstance(value.get("hero-rate-list"), int)
        and value["hero-rate-list"] >= 0
    }
    if len(roots) != 1:
        raise ValueError("原站未返回唯一的英雄统计表")
    result = unpack(next(iter(roots)))
    if (
        not isinstance(result, dict)
        or result.get("status") != "success"
        or not isinstance(result.get("data", {}).get("list"), list)
    ):
        raise ValueError("原站英雄统计暂时不可用")
    return result["data"]


class HeroRatesCache:
    """Refresh successful snapshots every six hours without fetching on each view."""

    def __init__(
        self, directory: Path | None = None, *, transport=None, clock=time.time
    ):
        self.directory = directory or Path(
            os.getenv("OW_HERO_RATES_CACHE_DIR")
            or Path(__file__).resolve().parents[1] / "data/hero_rates"
        )
        self.clock = clock
        self.client = httpx.AsyncClient(
            timeout=25, follow_redirects=True, trust_env=False, transport=transport
        )
        self.network = asyncio.Semaphore(1)
        self.refreshing: dict[str, asyncio.Task] = {}
        self.retry_after: dict[str, float] = {}
        self.worker = None

    @staticmethod
    def validate(filters: dict) -> dict:
        selected = {**DEFAULT_FILTERS, **filters}
        for key, category in FILTER_KEYS.items():
            allowed = {
                item["value"]
                for item in CATALOG["filters"][category]
                if item.get("parentValue") != "battlefield"
            }
            if selected[key] not in allowed:
                raise ValueError("不支持的筛选条件：" + key)
        if selected["region"] == "korea" and selected["input"] != "pc":
            raise ValueError("原站韩服统计仅支持 PC，请选择 PC 或其他地区。")
        if selected["rq"] == "0":
            selected["rank"] = "all"
        return selected

    @staticmethod
    def cache_key(filters: dict) -> str:
        # All roles share one upstream snapshot; position changes stay local.
        return hashlib.sha256(
            json.dumps(
                {key: value for key, value in filters.items() if key != "role"},
                sort_keys=True,
            ).encode()
        ).hexdigest()

    def read(self, key: str) -> dict | None:
        try:
            if (self.directory / (key + ".json")).stat().st_size > 1024 * 1024:
                return None
            snapshot = json.loads(
                (self.directory / (key + ".json")).read_text(encoding="utf-8")
            )
            if isinstance(snapshot["heroes"], list) and isinstance(
                snapshot["fetched_at"], (int, float)
            ):
                return snapshot
        except (OSError, ValueError, KeyError, TypeError):
            pass
        return None

    def accessed_at(self, key, fallback=0):
        try:
            return (self.directory / (key + ".used")).stat().st_mtime
        except OSError:
            return fallback

    def cleanup(self, protected=()):
        """Cold variants expire; background refresh does not renew user activity."""
        now = self.clock()
        retained = []
        for path in self.directory.glob("*.json"):
            if not re.fullmatch(r"[0-9a-f]{64}", path.stem):
                continue
            try:
                info = path.stat()
                access = self.accessed_at(path.stem, info.st_mtime)
                retained.append((access, info.st_size, path))
            except OSError:
                continue
        total, count = sum(row[1] for row in retained), len(retained)
        pinned = {self.cache_key(DEFAULT_FILTERS), *protected, *self.refreshing}
        for access, size, path in sorted(retained):
            if path.stem in pinned:
                continue
            if now - access < RETAIN_SECONDS and count <= MAX_VARIANTS and total <= MAX_CACHE_BYTES:
                continue
            path.unlink(missing_ok=True)
            path.with_suffix(".used").unlink(missing_ok=True)
            total, count = total - size, count - 1
        for path in self.directory.glob("*.used"):
            if re.fullmatch(r"[0-9a-f]{64}", path.stem) and not path.with_suffix(".json").exists():
                path.unlink(missing_ok=True)
        for path in self.directory.glob("*.tmp"):
            if re.fullmatch(r"[0-9a-f]{64}", path.stem) and path.stem not in self.refreshing:
                path.unlink(missing_ok=True)
        self.retry_after = {key: value for key, value in self.retry_after.items() if value > now}

    def schedule(self, selected: dict) -> asyncio.Task | None:
        key = self.cache_key(selected)
        if key in self.refreshing:
            return self.refreshing[key]
        if self.clock() < self.retry_after.get(key, 0) or len(self.refreshing) >= 16:
            return None
        task = asyncio.create_task(self.refresh(selected))
        self.refreshing[key] = task
        task.add_done_callback(lambda _: self.refreshing.pop(key, None))
        return task

    async def refresh(self, selected: dict) -> None:
        key = self.cache_key(selected)
        async with self.network:
            snapshot = self.read(key)
            if snapshot and self.clock() - snapshot["fetched_at"] < REFRESH_SECONDS:
                return
            try:
                params = {**selected, "role": "all"}
                response = await self.client.get(SOURCE_URL, params=params)
                response.raise_for_status()
                source = parse_public_statistics(response.text)
                heroes, seen = [], set()
                for row in source["list"]:
                    slug = row["heroId"]
                    if (
                        slug in seen
                        or not re.fullmatch(r"[a-z0-9-]{1,50}", slug)
                        or row["role"] not in {"tank", "damage", "support"}
                    ):
                        raise ValueError("原站英雄标识或位置异常")
                    seen.add(slug)
                    metrics = {}
                    for field in ("pickRate", "winRate", "banRate"):
                        value = row.get(field)
                        if value is not None and (
                            not isinstance(value, (int, float))
                            or not math.isfinite(value)
                            or not 0 <= value <= 100
                        ):
                            raise ValueError("原站统计比例异常")
                        metrics[field] = value
                    heroes.append(
                        {
                            "heroId": slug,
                            "name": CATALOG["hero_names"].get(slug, slug),
                            "role": row["role"],
                            "subrole": row.get("subrole"),
                            "colorCode": row.get("colorCode"),
                            "thumbnailUrl": row.get("thumbnailUrl"),
                            **metrics,
                        }
                    )
                snapshot = {
                    "fetched_at": self.clock(),
                    "filters": params,
                    "heroes": heroes,
                    "source_url": str(response.url),
                }
                self.directory.mkdir(parents=True, exist_ok=True)
                path = self.directory / (key + ".json")
                temporary = path.with_suffix(".tmp")
                try:
                    temporary.write_text(json.dumps(snapshot, ensure_ascii=False), encoding="utf-8")
                    temporary.replace(path)
                finally:
                    temporary.unlink(missing_ok=True)
                self.retry_after.pop(key, None)
            except (httpx.HTTPError, ValueError, KeyError, TypeError, OSError) as error:
                if len(self.retry_after) >= MAX_VARIANTS:
                    self.retry_after.pop(min(self.retry_after, key=self.retry_after.get), None)
                self.retry_after[key] = self.clock() + 300
                logger.warning(
                    "Hero statistics refresh failed (%s); retaining the previous snapshot",
                    type(error).__name__,
                )

    async def get(self, filters: dict | None = None) -> dict:
        selected = self.validate(filters or {})
        key = self.cache_key(selected)
        snapshot = self.read(key)
        if snapshot is None or self.clock() - snapshot["fetched_at"] >= REFRESH_SECONDS:
            task = self.schedule(selected)
            if snapshot is None and task is not None:
                await asyncio.shield(task)
                snapshot = self.read(key)
        if snapshot is None:
            raise RuntimeError(
                "该筛选组合尚未缓存成功，请稍后重试；没有使用其他条件的数据代替。"
            )
        try:
            accessed = self.directory / (key + ".used")
            accessed.touch()
            os.utime(accessed, (self.clock(), self.clock()))
            self.cleanup(protected=(key,))
        except OSError:
            logger.warning("Hero cache maintenance failed; response remains available")
        role = selected["role"]
        parent = next(
            item.get("parentValue")
            for item in CATALOG["filters"]["roles"]
            if item["value"] == role
        )
        heroes = [
            row
            for row in snapshot["heroes"]
            if role == "all" or row.get("subrole" if parent else "role") == role
        ]
        heroes = sorted(
            heroes,
            key=lambda row: (
                -(row["winRate"] if row["winRate"] is not None else -1),
                -(row["pickRate"] or 0),
                row["name"],
            ),
        )
        labels = {
            key: next(
                item["name"]
                for item in CATALOG["filters"][category]
                if item["value"] == selected[key]
            )
            for key, category in FILTER_KEYS.items()
        }
        return {
            "report_type": "hero_rates",
            "heroes": heroes,
            "filters": selected,
            "filter_labels": labels,
            "fetched_at": snapshot["fetched_at"],
            "refresh_after": snapshot["fetched_at"] + REFRESH_SECONDS,
            "stale": self.clock() - snapshot["fetched_at"] >= REFRESH_SECONDS,
            "source_url": snapshot["source_url"],
            "sort": "winRate",
            "order": "desc",
        }

    async def start(self):
        self.worker = asyncio.create_task(self.run())

    async def run(self):
        while True:
            try:
                self.cleanup()
            except OSError:
                logger.warning("Hero cache cleanup failed; will retry")
            selected = [DEFAULT_FILTERS]
            for path in self.directory.glob("*.json"):
                try:
                    snapshot = json.loads(path.read_text(encoding="utf-8"))
                    if (self.clock() - self.accessed_at(path.stem, snapshot["fetched_at"]) < ACTIVE_SECONDS
                            and self.clock() - snapshot["fetched_at"] >= REFRESH_SECONDS):
                        selected.append(self.validate(snapshot["filters"]))
                except (OSError, ValueError, KeyError, TypeError):
                    continue
            for filters in selected:
                task = self.schedule(filters)
                if task is not None:
                    await task
            await asyncio.sleep(300)

    async def close(self):
        tasks = [*self.refreshing.values(), *([self.worker] if self.worker else [])]
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
        await self.client.aclose()
