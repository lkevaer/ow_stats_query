from __future__ import annotations

import asyncio
from copy import deepcopy
from datetime import datetime
import json
import logging
import os
import re
from pathlib import Path
import sqlite3
import time
from typing import Any
import uuid

from ..query_audit import record_successful_ids
from ..metrics import REGISTRY
from ..work_queue import work_queue
from .ocr import normalize_tags, recognize_image
from .player_cache import CLEANUP_SECONDS, FRESH_SECONDS, RETAIN_SECONDS, PlayerDataCache, PlayerSnapshot
from .circuit import UpstreamCircuit
from .scoring import VERSION, RELATIVE_NOTICE, SCOPE_NOTICE, aggregate_player, grouped_summary, rank_players, score_match

MODE_MAP = {"leisure": "leisure", "competitive": "sport"}
MODE_NAMES = {"leisure": "休闲", "competitive": "竞技"}
MATCH_LIMIT = 12
logger = logging.getLogger("ow_api_server.batch_rating")


class QueryFailure(Exception):
    pass


class BatchRatingService:
    def __init__(self, state):
        self.state = state
        self.jobs: dict[str, dict] = {}
        self.running: dict[str, asyncio.Task] = {}
        self.network = asyncio.Semaphore(2)
        self.batch_slots = asyncio.Semaphore(1)
        self.report_dir = Path(os.getenv("OW_RATING_REPORT_DIR") or Path(__file__).resolve().parents[2] / "data" / "batch_rating_reports")
        self.current_season = int(os.getenv("OW_RATING_CURRENT_SEASON", "25"))
        self.player_cache = PlayerDataCache()
        self.queue = work_queue(state)
        self._player_tasks: dict[str, asyncio.Task] = {}
        self._detail_tasks: dict[str, asyncio.Task] = {}
        self.circuit = UpstreamCircuit()
        self._maintenance_task: asyncio.Task | None = None

    async def start_maintenance(self):
        if self._maintenance_task is None:
            await self._maintenance_once()
            self._maintenance_task = asyncio.create_task(self._maintain(), name="ow-rating-cache-cleanup")

    async def _maintenance_once(self):
        operations = [self.player_cache.cleanup, self._cleanup_reports]
        if getattr(self.state, "initialized", False):
            from ..db.task_db import cleanup_expired_tasks
            operations.append(lambda: cleanup_expired_tasks(3600))
        for operation in operations:
            try:
                await asyncio.to_thread(operation)
            except (OSError, sqlite3.Error):
                logger.warning("Rating cache cleanup failed; will retry on the next pass", exc_info=True)
        self._cleanup()

    async def _maintain(self):
        while True:
            await asyncio.sleep(CLEANUP_SECONDS)
            await self._maintenance_once()

    async def close(self):
        tasks = list(set(self.running.values()) | set(self._player_tasks.values()) | set(self._detail_tasks.values()))
        if self._maintenance_task is not None:
            tasks.append(self._maintenance_task)
        for task in tasks:
            task.cancel()
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)
        self._player_tasks.clear()
        self._detail_tasks.clear()
        self._maintenance_task = None

    def _cleanup_reports(self):
        now = time.time()
        retained = []
        if self.report_dir.exists():
            for path in self.report_dir.iterdir():
                try:
                    if not path.is_file():
                        continue
                    if re.fullmatch(r"[0-9a-f]{32}\.(?:json|tmp)", path.name) and now - path.stat().st_mtime >= RETAIN_SECONDS:
                        path.unlink(missing_ok=True)
                    elif re.fullmatch(r"[0-9a-f]{32}\.json", path.name):
                        info = path.stat()
                        retained.append((info.st_mtime, info.st_size, path))
                except FileNotFoundError:
                    pass  # A simultaneous reader may already have removed it.
        total = sum(size for _, size, _ in retained)
        for _, size, path in sorted(retained):
            if total <= 256 * 1024 * 1024:
                break
            path.unlink(missing_ok=True)
            total -= size

    @staticmethod
    def _report_deadline(job):
        timestamps = [float(job["updated_at"])]
        for player in (job.get("result") or {}).get("players", []):
            stamp = (player.get("data_cache") or {}).get("fetched_at")
            if isinstance(stamp, (int, float)):
                timestamps.append(stamp)
        return min(timestamps) + RETAIN_SECONDS

    def _cleanup(self):
        now = time.time()
        for key in list(self.jobs):
            job = self.jobs[key]
            if job["status"] in {"done", "error"} and (now - job["updated_at"] > 3600 or now >= self._report_deadline(job)):
                del self.jobs[key]
        completed = sorted((job["updated_at"], key) for key, job in self.jobs.items() if job["status"] in {"done", "error"})
        for _, key in completed[:-8]:
            self.jobs.pop(key, None)

    async def prepare(self, battletags: list[str], image_base64: str | None, *, full_match: bool = False) -> dict:
        if battletags:
            return {"battletags": normalize_tags(battletags, max_players=12 if full_match else 10), "source": "battletags"}
        if not image_base64:
            raise ValueError("请上传截图，或提供 battletags 数组")
        extracted = await self.queue.call(lambda: recognize_image(image_base64))
        return {**extracted, "source": "image"}

    def start(self, mode: str, prepared: dict) -> dict:
        self._cleanup()
        if sum(job["status"] in {"queued", "running"} for job in self.jobs.values()) >= 4:
            raise QueryFailure("已有多批查询排队，请等待完成后再提交")
        key = uuid.uuid4().hex
        now = time.time()
        job = {"task_id": key, "status": "queued", "mode": mode, "stage": "等待查询", "progress": 0,
               "created_at": now, "updated_at": now, "battletags": prepared["battletags"], "completed_players": 0,
               "total_players": len(prepared["battletags"]), "result": None}
        task = self.queue.submit(lambda: self._run_job(job, prepared))
        self.jobs[key] = job
        self.running[key] = task
        task.add_done_callback(lambda _: self.running.pop(key, None))
        return self.public_job(job)

    def public_job(self, job):
        return {**{k: v for k, v in job.items() if not k.startswith("_")}, "queue": self.queue.status()}

    def get_job(self, key: str) -> dict | None:
        self._cleanup()
        job = self.jobs.get(key)
        if job is None and re.fullmatch(r"[0-9a-f]{32}", key):
            path = self.report_dir / (key + ".json")
            try:
                saved = json.loads(path.read_text(encoding="utf-8"))
                if saved.get("task_id") == key and saved.get("status") == "done":
                    if time.time() < self._report_deadline(saved):
                        job = saved
                    else:
                        path.unlink(missing_ok=True)
            except (OSError, ValueError, KeyError, TypeError):
                pass
        return self.public_job(job) if job else None

    def _save_report(self, job):
        snapshot = self.public_job(job)
        snapshot["report_persisted"] = True
        snapshot["result"] = dict(snapshot["result"])
        recognized = snapshot["result"].get("recognized") or {}
        snapshot["result"]["recognized"] = {k: recognized[k] for k in ("source", "battletags") if k in recognized}
        self.report_dir.mkdir(parents=True, exist_ok=True)
        destination = self.report_dir / (job["task_id"] + ".json")
        temporary = destination.with_suffix(".tmp")
        temporary.write_text(json.dumps(snapshot, ensure_ascii=False), encoding="utf-8")
        temporary.replace(destination)
        # Reports made from stale data must not extend that data's retention window.
        stamp = self._report_deadline(snapshot) - RETAIN_SECONDS
        os.utime(destination, (stamp, stamp))
        self._cleanup_reports()

    async def _run_job(self, job, prepared):
        try:
            async with self.batch_slots:
                job.update(status="running", stage="读取英雄位置配置", updated_at=time.time())
                result = await self.query(job["mode"], prepared, progress=lambda stage, pct, completed: job.update(
                    stage=stage, progress=round(pct, 1), completed_players=completed, updated_at=time.time()))
                job.update(status="done", stage="查询完成", progress=100, result=result, updated_at=time.time())
                try:
                    await asyncio.to_thread(self._save_report, job)
                    job["report_persisted"] = True
                except OSError:
                    job["report_persisted"] = False
                    logger.warning("Completed report could not be persisted; result is available in memory", exc_info=True)
        except asyncio.CancelledError:
            job.update(status="error", error="查询已取消或超时，请稍后重试", updated_at=time.time())
            pending = list(self._player_tasks.values()) + list(self._detail_tasks.values())
            for task in pending:
                task.cancel()
            if pending:
                await asyncio.gather(*pending, return_exceptions=True)
            raise
        except Exception as exc:
            job.update(status="error", error=str(exc) if isinstance(exc, (ValueError, QueryFailure)) else f"查询失败（{type(exc).__name__}）", updated_at=time.time())

    async def _request(self, method, *args, **kwargs):
        operation = getattr(method, "__name__", None) or getattr(method, "_mock_name", "request") or "request"
        operation = operation if operation in {"search", "query_match_list", "query_match_info"} else "other"
        async with self.network:
            if self.circuit.remaining(operation):
                REGISTRY.counter("ow_upstream_requests_total", {"operation": operation, "result": "circuit_open"}).inc()
                raise QueryFailure(f"上游暂时不可用，约{int(self.circuit.remaining(operation)) + 1}秒后重试")
            try:
                result = await asyncio.wait_for(method(*args, **kwargs), timeout=45)
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                REGISTRY.counter("ow_upstream_requests_total", {"operation": operation, "result": "network_error"}).inc()
                self.circuit.failure(operation)
                raise QueryFailure(f"网易大神请求失败（{type(exc).__name__}），未用空数据代替") from exc
        payload = result if isinstance(result, dict) else getattr(getattr(result, "result", None), "payload", {})
        if isinstance(payload, dict) and (payload.get("code", 0) != 0 or payload.get("success") is False):
            REGISTRY.counter("ow_upstream_requests_total", {"operation": operation, "result": "denied"}).inc()
            code = payload.get("code")
            if str(code) in {"401", "429"}:
                self.circuit.failure(operation, immediate=True)
            elif str(code).startswith("5"):
                self.circuit.failure(operation)
            if str(code) == "401":
                raise QueryFailure("上游账号授权暂时不可用，请联系管理员")
            if str(code) == "429":
                raise QueryFailure("上游查询繁忙，请稍后重试")
            raise QueryFailure(f"网易大神拒绝查询（code={code}），请检查账号授权和战绩可见性")
        self.circuit.success(operation)
        REGISTRY.counter("ow_upstream_requests_total", {"operation": operation, "result": "success"}).inc()
        return result

    @staticmethod
    def _belongs(match, mode):
        actual = str(match.get("gameMode") or "").lower()
        if "fight" in actual or "stadium" in actual:
            return False
        if not actual:
            return True  # The endpoint itself is requested with a fixed gameMode.
        return actual.startswith("leisure") if mode == "leisure" else actual.startswith("sport") or actual.startswith("competitive")

    async def _recent(self, customer_token: str, mode: str) -> tuple[list[dict], list[Any]]:
        from overstats.src.modules.dashen_match.requests import extract_match_entries
        client, found, scanned = self.state.api_client, {}, []
        # None is the authoritative live season, not the old core's hardcoded season 22.
        # Walk earlier seasons only when needed, never add matches from the other queue.
        seasons = [None] + list(range(self.current_season - 1, 0, -1))
        for season in seasons:
            scanned.append("current" if season is None else season)
            for page in range(1, 101):
                payload = await self._request(client.query_match_list, customer_token, MODE_MAP[mode], page=page, season=season)
                entries = extract_match_entries(payload, "matchList", "recentMatchList")
                added = 0
                for match in entries:
                    key = str(match.get("matchId") or "")
                    if not key or key in found or not self._belongs(match, mode):
                        continue
                    found[key] = {**match, "gameMode": match.get("gameMode") or MODE_MAP[mode]}
                    added += 1
                if len(found) >= MATCH_LIMIT:
                    ordered = sorted(found.values(), key=lambda x: float(x.get("beginTs") or 0), reverse=True)
                    return ordered[:MATCH_LIMIT], scanned
                if not entries or not added:
                    break
        ordered = sorted(found.values(), key=lambda x: float(x.get("beginTs") or 0), reverse=True)
        return ordered[:MATCH_LIMIT], scanned

    async def _one_player(self, battletag: str, mode: str, heroes: dict, maps: dict, ranks: list[dict], progress):
        key = self.player_cache.key(battletag, mode, self.current_season, MATCH_LIMIT)
        task = self._player_tasks.get(key)
        if task is None:
            task = asyncio.create_task(self._load_player(key, battletag, mode, heroes, maps, ranks, progress))
            self._player_tasks[key] = task

            def finished(done):
                if self._player_tasks.get(key) is done:
                    self._player_tasks.pop(key, None)
                if not done.cancelled():
                    done.exception()  # Retrieve errors even if the only waiter was cancelled.

            task.add_done_callback(finished)
        else:
            REGISTRY.counter("ow_rating_player_cache_total", {"result": "coalesced"}).inc()
            progress(f"复用 {battletag} 正在进行的查询")
        # A cancelled HTTP request must not cancel another request's shared fetch.
        return deepcopy(await asyncio.shield(task))

    async def has_fresh_data(self, mode, battletags):
        try:
            for tag in battletags:
                key = self.player_cache.key(tag, mode, self.current_season, MATCH_LIMIT)
                if key in self._player_tasks:
                    continue
                cached = await asyncio.to_thread(self.player_cache.get, key)
                if cached is None or not cached.fresh(time.time()):
                    return False
            return True
        except (OSError, sqlite3.Error):
            return False

    @staticmethod
    def _cache_metadata(snapshot, source, error=None):
        result = {"source": source, "fetched_at": snapshot.fetched_at,
                  "age_seconds": round(max(0, time.time() - snapshot.fetched_at), 1),
                  "fresh_seconds": FRESH_SECONDS, "expires_at": snapshot.fetched_at + RETAIN_SECONDS}
        if error:
            result["refresh_error"] = error
        return result

    def _rate_snapshot(self, battletag, snapshot, heroes, maps, ranks):
        data = deepcopy(snapshot.data)
        identity = data["identity"]
        rated, errors = [], list(data.get("errors") or [])
        for item in data["matches"]:
            source = item["source"]
            try:
                rated.append(score_match(item["detail"], source, customer_token=identity["customer_token"],
                                         bnet_id=identity["bnet_id"], battletag=identity["full_id"],
                                         heroes=heroes, maps=maps, ranks=ranks))
            except Exception as exc:
                errors.append({"match_id": str(source.get("matchId") or ""),
                               "error": str(exc) if isinstance(exc, ValueError) else f"详情无法评分（{type(exc).__name__}）"})
        rated.sort(key=lambda x: float(x.get("begin_ts") or 0), reverse=True)
        player = aggregate_player(battletag, rated, fetched=data["fetched"], errors=errors)
        player.update(resolved_battletag=identity["full_id"], seasons_scanned=data["seasons_scanned"])
        if not data["fetched"]:
            player["sample_note"] = "没有查到该模式的公开对局，未评级；未混入其他模式"
        return player

    async def _load_player(self, key, battletag, mode, heroes, maps, ranks, progress):
        cached = None
        try:
            cached = await asyncio.to_thread(self.player_cache.get, key)
        except (OSError, sqlite3.Error):
            logger.warning("Could not read player cache; fetching from upstream", exc_info=True)
        if cached is not None:
            try:
                previous = self._rate_snapshot(battletag, cached, heroes, maps, ranks)
                if previous["errors"]:
                    cached = None
                elif cached.fresh(time.time()):
                    REGISTRY.counter("ow_rating_player_cache_total", {"result": "hit"}).inc()
                    progress(f"使用 {battletag} 的5分钟内战绩缓存")
                    previous["data_cache"] = self._cache_metadata(cached, "cache")
                    return previous
            except (KeyError, TypeError, ValueError):
                cached = None

        REGISTRY.counter("ow_rating_player_cache_total", {"result": "miss"}).inc()
        try:
            snapshot = await self._fetch_player(battletag, mode, progress, heroes, maps, ranks)
            player = self._rate_snapshot(battletag, snapshot, heroes, maps, ranks)
            if player["errors"]:
                error = f"{len(player['errors'])}场对局详情获取或评分失败"
                if cached is not None and cached.usable(time.time()):
                    REGISTRY.counter("ow_rating_player_cache_total", {"result": "stale"}).inc()
                    previous["data_cache"] = self._cache_metadata(cached, "stale_cache", error)
                    return previous
            else:
                try:
                    await asyncio.to_thread(self.player_cache.put, key, snapshot)
                except (OSError, sqlite3.Error, ValueError):
                    logger.warning("Could not update player cache; previous snapshot retained", exc_info=True)
            player["data_cache"] = self._cache_metadata(snapshot, "upstream")
            return player
        except (QueryFailure, ValueError) as exc:
            if cached is not None and cached.usable(time.time()):
                REGISTRY.counter("ow_rating_player_cache_total", {"result": "stale"}).inc()
                previous["data_cache"] = self._cache_metadata(cached, "stale_cache", str(exc))
                return previous
            return {**aggregate_player(battletag, [], fetched=0, errors=[]), "sample_note": str(exc)}

    async def _fetch_player(self, battletag, mode, progress, heroes, maps, ranks):
        progress(f"正在解析 {battletag}")
        search = await self._request(self.state.bnet_search.search, battletag, render=False)
        identity = search.result
        customer_token = str(identity.customer_token or "")
        if not customer_token:
            raise QueryFailure("未找到该 BattleTag，或账号未公开战绩")
        progress(f"查询 {battletag} 的近12场{MODE_NAMES[mode]}对局")
        matches, scanned = await self._recent(customer_token, mode)
        details, errors = [], []

        async def one_detail(source):
            try:
                data = await self._match_detail(identity, source, mode, heroes, maps, ranks)
                details.append({"source": source, "detail": data})
            except (QueryFailure, ValueError) as exc:
                errors.append({"match_id": str(source.get("matchId") or ""), "error": str(exc)})
            except Exception as exc:
                errors.append({"match_id": str(source.get("matchId") or ""), "error": f"详情无法读取（{type(exc).__name__}）"})
            progress(f"{battletag}：已处理 {len(details) + len(errors)}/{len(matches)} 场详情")

        await asyncio.gather(*(one_detail(match) for match in matches))
        return PlayerSnapshot({"identity": {"customer_token": customer_token, "bnet_id": str(identity.bnet_id or ""),
                                           "full_id": str(identity.full_id or battletag)},
                               "matches": details, "fetched": len(matches), "seasons_scanned": scanned,
                               "errors": errors}, time.time())

    async def _match_detail(self, identity, source, mode, heroes, maps, ranks):
        from .scoring import _identity_matches
        from overstats.src.modules.dashen_match.render import _extract_match_detail_data
        import hashlib
        key = hashlib.sha256(json.dumps([1, mode, str(source["matchId"])]).encode()).hexdigest()
        who = {"customer_token": str(identity.customer_token or ""), "bnet_id": str(identity.bnet_id or ""),
               "battletag": str(identity.full_id or "")}

        def contains(data):
            return any(_identity_matches(row, **who) for name in ("teammateList", "enemyList") for row in data.get(name, []) if isinstance(row, dict))

        try:
            cached = await asyncio.to_thread(self.player_cache.get_match, key)
            if cached and contains(cached.data):
                REGISTRY.counter("ow_match_detail_cache_total", {"result": "hit"}).inc()
                return deepcopy(cached.data)
        except (OSError, sqlite3.Error):
            logger.warning("Could not read match cache", exc_info=True)

        pending = self._detail_tasks.get(key)
        if pending is not None:
            REGISTRY.counter("ow_match_detail_cache_total", {"result": "coalesced"}).inc()
            data = await asyncio.shield(pending)
            if contains(data) and "matchRet" in data:
                return deepcopy(data)

        async def fetch():
            REGISTRY.counter("ow_match_detail_cache_total", {"result": "miss"}).inc()
            payload = await self._request(self.state.api_client.query_match_info, who["customer_token"], str(source["matchId"]))
            data = _extract_match_detail_data(payload)
            # Store an explicit result in the original teammate-list perspective.
            # A source-only result belongs to its queried player and must be oriented first.
            if "matchRet" not in data and str(source.get("matchRet")) in {"-1", "0", "1", "2"}:
                side = 0 if any(_identity_matches(row, **who) for row in data.get("teammateList", [])) else 1
                outcome = int(source["matchRet"])
                data["matchRet"] = (-1 if outcome == 1 else 0 if outcome == 0 else 1) if side else outcome
            if contains(data) and "matchRet" in data:
                try:
                    validated = score_match(data, source, **who, heroes=heroes, maps=maps, ranks=ranks)
                    roster = validated.get("match_details") or {}
                    if roster.get("total_players") in {10, 12} and roster.get("rated_players") == roster["total_players"]:
                        await asyncio.to_thread(self.player_cache.put_match, key, PlayerSnapshot(data, time.time()))
                except (ValueError, OSError, sqlite3.Error):
                    pass  # Incomplete data remains visible to its caller, never a reusable full match.
            return data

        task = asyncio.create_task(fetch())
        self._detail_tasks[key] = task

        def finished(done):
            if self._detail_tasks.get(key) is done:
                self._detail_tasks.pop(key, None)
            if not done.cancelled():
                done.exception()

        task.add_done_callback(finished)
        return deepcopy(await asyncio.shield(task))

    async def query(self, mode: str, prepared: dict, progress=None):
        if mode not in MODE_MAP:
            raise ValueError("不支持的查询模式")
        if self.state.api_client is None or self.state.bnet_search is None:
            raise QueryFailure("请先配置网易大神账号凭据后再查询")
        from overstats.src.modules.query_tool import load_query_tool
        config = await asyncio.to_thread(load_query_tool)
        heroes = {str(h["heroGuid"]): h for h in config.get("heroList", []) if h.get("heroGuid")}
        maps = {str(m.get("guid") or m.get("mapGuid")): m for m in config.get("mapList", []) if m.get("guid") or m.get("mapGuid")}
        ranks = [r for r in config.get("rankList",[]) if isinstance(r,dict)]
        for hero in heroes.values():
            if str(hero.get("name", "")).lower() in {"血律", "doctrine", "黑影", "sombra"}:
                hero["roleType"] = "healer"
        if not heroes:
            raise QueryFailure("英雄位置配置为空，无法进行角色评分")
        tags = prepared["battletags"]
        results, completed, stages = [], 0, {}
        player_slots = asyncio.Semaphore(2)

        async def one_player(tag):
            nonlocal completed
            async with player_slots:
                def update(stage):
                    stages[tag] = stage
                    if progress:
                        progress(stage, 5 + 90 * completed / len(tags), completed)
                result = await self._one_player(tag, mode, heroes, maps, ranks, update)
                results.append(result)
                completed += 1
                if progress:
                    progress(f"已完成 {completed}/{len(tags)} 位玩家", 5 + 90 * completed / len(tags), completed)

        await asyncio.gather(*(one_player(tag) for tag in tags))
        ranked = rank_players(results)
        await record_successful_ids(p.get("resolved_battletag") or p["battletag"] for p in ranked if p["score"] is not None)
        return {"mode": mode, "mode_label": MODE_NAMES[mode], "matches_per_player": MATCH_LIMIT,
                "generated_at": datetime.now().astimezone().isoformat(), "players": ranked, "summary_text": grouped_summary(ranked),
                "recognized": prepared, "scoring_version": VERSION, "relative_notice": RELATIVE_NOTICE, "scope_notice":SCOPE_NOTICE, "hero_registry_count": len(heroes),
                "summary": {"requested_players": len(tags), "rated_players": sum(p["score"] is not None for p in results),
                            "complete_players": sum(p["rated_matches"] == 12 for p in results), "rated_matches": sum(p["rated_matches"] for p in results)}}
