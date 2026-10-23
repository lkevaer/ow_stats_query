from __future__ import annotations

import asyncio
from datetime import datetime
import json
import os
from pathlib import Path
import time
from typing import Any
import uuid

from ..query_audit import record_successful_ids
from .ocr import normalize_tags, recognize_image
from .scoring import VERSION, RELATIVE_NOTICE, SCOPE_NOTICE, aggregate_player, grouped_summary, rank_players, score_match

MODE_MAP = {"leisure": "leisure", "competitive": "sport"}
MODE_NAMES = {"leisure": "休闲", "competitive": "竞技"}
MATCH_LIMIT = 12


class QueryFailure(Exception):
    pass


class BatchRatingService:
    def __init__(self, state):
        self.state = state
        self.jobs: dict[str, dict] = {}
        self.running: dict[str, asyncio.Task] = {}
        self.network = asyncio.Semaphore(2)
        self.batch_slots = asyncio.Semaphore(1)
        self.current_season = int(os.getenv("OW_RATING_CURRENT_SEASON", "25"))

    async def close(self):
        tasks = list(self.running.values())
        for task in tasks:
            task.cancel()
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)

    def _cleanup(self):
        now = time.time()
        for key in list(self.jobs):
            job = self.jobs[key]
            if job["status"] in {"done", "error"} and now - job["updated_at"] > 3600:
                del self.jobs[key]

    async def prepare(self, battletags: list[str], image_base64: str | None) -> dict:
        if battletags:
            return {"battletags": normalize_tags(battletags), "source": "battletags"}
        if not image_base64:
            raise ValueError("请上传截图，或提供 battletags 数组")
        extracted = await recognize_image(image_base64)
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
        self.jobs[key] = job
        task = asyncio.create_task(self._run_job(job, prepared))
        self.running[key] = task
        task.add_done_callback(lambda _: self.running.pop(key, None))
        return self.public_job(job)

    @staticmethod
    def public_job(job):
        return {k: v for k, v in job.items() if not k.startswith("_")}

    def get_job(self, key: str) -> dict | None:
        self._cleanup()
        job = self.jobs.get(key)
        return self.public_job(job) if job else None

    async def _run_job(self, job, prepared):
        try:
            async with self.batch_slots:
                job.update(status="running", stage="读取英雄位置配置", updated_at=time.time())
                result = await self.query(job["mode"], prepared, progress=lambda stage, pct, completed: job.update(
                    stage=stage, progress=round(pct, 1), completed_players=completed, updated_at=time.time()))
                job.update(status="done", stage="查询完成", progress=100, result=result, updated_at=time.time())
        except asyncio.CancelledError:
            job.update(status="error", error="服务停止，查询已取消", updated_at=time.time())
            raise
        except Exception as exc:
            job.update(status="error", error=str(exc) if isinstance(exc, (ValueError, QueryFailure)) else f"查询失败（{type(exc).__name__}）", updated_at=time.time())

    async def _request(self, method, *args, **kwargs):
        async with self.network:
            try:
                result = await asyncio.wait_for(method(*args, **kwargs), timeout=45)
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                raise QueryFailure(f"网易大神请求失败（{type(exc).__name__}），未用空数据代替") from exc
        if isinstance(result, dict) and (result.get("code", 0) != 0 or result.get("success") is False):
            raise QueryFailure(f"网易大神拒绝查询（code={result.get('code')}），请检查账号授权和战绩可见性")
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
        try:
            progress(f"正在解析 {battletag}")
            search = await self._request(self.state.bnet_search.search, battletag, render=False)
            identity = search.result
            customer_token = str(identity.customer_token or "")
            if not customer_token:
                raise QueryFailure("未找到该 BattleTag，或账号未公开战绩")
            progress(f"查询 {battletag} 的近12场{MODE_NAMES[mode]}对局")
            matches, scanned = await self._recent(customer_token, mode)
            rated, errors = [], []
            from overstats.src.modules.dashen_match.render import _extract_match_detail_data

            async def one_detail(source):
                try:
                    payload = await self._request(self.state.api_client.query_match_info, customer_token, str(source["matchId"]))
                    data = _extract_match_detail_data(payload)
                    scored = score_match(data, source, customer_token=customer_token, bnet_id=str(identity.bnet_id or ""),
                                         battletag=str(identity.full_id or battletag), heroes=heroes, maps=maps, ranks=ranks)
                    rated.append(scored)
                except (QueryFailure, ValueError) as exc:
                    errors.append({"match_id": str(source.get("matchId") or ""), "error": str(exc)})
                except Exception as exc:
                    errors.append({"match_id": str(source.get("matchId") or ""), "error": f"详情无法评分（{type(exc).__name__}）"})
                progress(f"{battletag}：已处理 {len(rated) + len(errors)}/{len(matches)} 场详情")

            await asyncio.gather(*(one_detail(match) for match in matches))
            rated.sort(key=lambda x: float(x.get("begin_ts") or 0), reverse=True)
            player = aggregate_player(battletag, rated, fetched=len(matches), errors=errors)
            player.update(resolved_battletag=identity.full_id or battletag, seasons_scanned=scanned)
            if not matches:
                player["sample_note"] = "没有查到该模式的公开对局，未评级；未混入其他模式"
            return player
        except (QueryFailure, ValueError) as exc:
            return {**aggregate_player(battletag, [], fetched=0, errors=[]), "sample_note": str(exc)}

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
