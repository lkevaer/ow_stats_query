from __future__ import annotations

import asyncio
import json
import logging
import re
import time
from typing import Any, Awaitable, Callable, Dict, List, Optional

from fastapi import APIRouter, Body, Depends, HTTPException, Request
from fastapi.responses import StreamingResponse

from ..app_context import AppState
from ..query_audit import record_successful_ids
from .utils import get_app_state, require_account_ready, resolve_target, ResolvedTarget

logger = logging.getLogger("ow_api_server.stream")
router = APIRouter(prefix="/api", tags=["stream"])

# ---------------------------------------------------------------------------
# SSE helper
# ---------------------------------------------------------------------------
def _sse_progress(pct: float, stage: str) -> bytes:
    payload = json.dumps(
        {"type": "progress", "pct": round(float(pct), 1), "stage": str(stage)},
        ensure_ascii=False,
    )
    return ("event: progress\ndata: " + payload + "\n\n").encode("utf-8")


def _sse_done(result: Any) -> bytes:
    try:
        raw = json.dumps(result, ensure_ascii=False, default=str)
    except Exception:
        raw = json.dumps(
            {"success": False, "error": "结果无法序列化为 JSON", "status_code": 500},
            ensure_ascii=False,
        )
    return ("event: done\ndata: " + raw + "\n\n").encode("utf-8")


def _sse_error(message: str, status_code: int = 500) -> bytes:
    payload = json.dumps(
        {"type": "error", "error": str(message), "status_code": int(status_code)},
        ensure_ascii=False,
    )
    return ("event: error\ndata: " + payload + "\n\n").encode("utf-8")

# ---------------------------------------------------------------------------
# Stage → percentage lookup  (用于 UI 展示，进度条并非真实执行耗时，但会按阶段推进)
# ---------------------------------------------------------------------------
_STAGE_PCT: Dict[str, float] = {
    "启动查询": 5.0,
    "解析 BattleTag": 10.0,
    "查询玩家资料 (1/2)": 30.0,
    "查询玩家资料 (2/2)": 45.0,
    "查询战绩列表 (1/N)": 35.0,
    "渲染战绩图片": 70.0,
    "查询对局详情": 40.0,
    "渲染对局详情图片": 75.0,
    "生成 AI 锐评": 60.0,
    "渲染段位历史图片": 75.0,
    "渲染强度图片": 80.0,
    "渲染总结图片": 80.0,
    "查询英雄热度": 40.0,
    "查询英雄威能": 40.0,
    "查询商店": 50.0,
    "查询补丁": 50.0,
    "搜索玩家": 40.0,
    "查询同玩": 40.0,
    "生成响应": 95.0,
    "完成": 100.0,
}


# ---------------------------------------------------------------------------
# 统一入口：将原始 endpoint + params 分派到某个模块函数
# 每个处理函数都是一个 async 协程，接受 (params, state, on_progress)，返回最终响应 dict
# ---------------------------------------------------------------------------
ProgressCb = Callable[[str], Awaitable[None]]


async def _ensure_progress(state: AppState, stage: str, send: Callable[[bytes], Awaitable[None]]) -> None:
    # 同时刷新 heartbeat（间隔性的 "仍在工作" 事件），避免浏览器长时间无刷新感到卡住
    await send(_sse_progress(_STAGE_PCT.get(stage, 50.0), stage))


async def _query_profile(
    params: Dict[str, Any],
    state: AppState,
    resolved: ResolvedTarget,
    send: Callable[[bytes], Awaitable[None]],
) -> Dict[str, Any]:
    from ..response_utils import error_response, image_response, text_response
    from overstats.src.modules.dashen_profile.requests import DashenProfileQuery

    if state.profile is None:
        return error_response("玩家资料查询功能未启用", 503)

    prefer_img = (state.config.get("output") or {}).get("prefer_image_for_profile", True)

    await send(_sse_progress(15.0, "解析 BattleTag"))
    query = DashenProfileQuery(
        customer_token=resolved.customer_token or None,
        bnet_id=resolved.effective_bnet_id,
    )
    await send(_sse_progress(35.0, "查询玩家资料 (1/2)"))
    result = await state.profile.query_profile(query, render=prefer_img)
    await send(_sse_progress(75.0, "渲染战绩图片"))

    display_name = (
        result.resolved_bnet.full_id
        if getattr(result, "resolved_bnet", None)
        else resolved.display_name
    )
    if prefer_img and result.image:
        return image_response(result.image, fallback_text="玩家：{0}".format(display_name))
    return text_response("玩家：{0}".format(display_name))


async def _query_matches(
    params: Dict[str, Any],
    state: AppState,
    resolved: ResolvedTarget,
    send: Callable[[bytes], Awaitable[None]],
) -> Dict[str, Any]:
    from ..response_utils import error_response, image_response
    from overstats.src.modules.dashen_match.requests import DashenMatchQuery

    if state.match is None:
        return error_response("战绩查询功能未启用", 503)
    prefer_img = (state.config.get("output") or {}).get("prefer_image_for_match", True)

    limit = int(params.get("limit", 5))
    if not 1 <= limit <= 30:
        raise HTTPException(status_code=422, detail="场数必须在 1 到 30 之间")
    await send(_sse_progress(15.0, "解析 BattleTag"))
    query = DashenMatchQuery(
        customer_token=resolved.customer_token or None,
        bnet_id=resolved.effective_bnet_id,
        target_count=limit,
    )
    await send(_sse_progress(40.0, "查询战绩列表 (1/N)"))
    result = await state.match.query_match_list(query, render=prefer_img)
    await send(_sse_progress(80.0, "渲染战绩图片"))

    display_name = (
        result.resolved_bnet.full_id
        if getattr(result, "resolved_bnet", None)
        else resolved.display_name
    )
    if prefer_img and result.image:
        return image_response(result.image, fallback_text="最近战绩：{0}".format(display_name))
    matches = getattr(result, "matches", []) or []
    lines = ["玩家：{0}".format(display_name), "最近 {0} 场".format(len(matches))]
    for m in matches:
        if isinstance(m, dict):
            win = "胜" if m.get("matchRet") == 1 else "败"
            hero = m.get("heroGuid", "?")
            lines.append("  {0} | 英雄: {1}".format(win, hero))
    from ..response_utils import text_response
    return text_response("\n".join(lines))


async def _query_match_detail(
    params: Dict[str, Any],
    state: AppState,
    resolved: ResolvedTarget,
    send: Callable[[bytes], Awaitable[None]],
) -> Dict[str, Any]:
    from ..response_utils import error_response, image_response, text_response
    from overstats.src.modules.dashen_match.requests import DashenMatchQuery

    if state.match is None:
        return error_response("对局详情查询功能未启用", 503)
    prefer_img = (state.config.get("output") or {}).get("prefer_image_for_match_detail", True)
    try:
        index = int(params.get("index", 1))
    except (TypeError, ValueError):
        index = 1
    if index < 1:
        index = 1

    await send(_sse_progress(15.0, "解析 BattleTag"))
    query = DashenMatchQuery(
        customer_token=resolved.customer_token or None,
        bnet_id=resolved.effective_bnet_id,
    )
    await send(_sse_progress(35.0, "查询对局详情"))
    result = await state.match.query_match_detail_by_index(query, index - 1, render=prefer_img)
    await send(_sse_progress(80.0, "渲染对局详情图片"))

    if prefer_img and result.image:
        return image_response(result.image, fallback_text="对局详情 #{0}".format(index))
    return text_response("对局详情 #{0}".format(index))


async def _query_analyze(
    params: Dict[str, Any],
    state: AppState,
    resolved: ResolvedTarget,
    send: Callable[[bytes], Awaitable[None]],
) -> Dict[str, Any]:
    from ..response_utils import error_response
    from overstats.src.modules.dashen_match.requests import DashenMatchQuery

    if state.match is None:
        return error_response("对局锐评功能未启用", 503)
    try:
        index = int(params.get("index", 1))
    except (TypeError, ValueError):
        index = 1
    if index < 1:
        index = 1

    await send(_sse_progress(15.0, "解析 BattleTag"))
    query = DashenMatchQuery(
        customer_token=resolved.customer_token or None,
        bnet_id=resolved.effective_bnet_id,
    )
    await send(_sse_progress(35.0, "查询对局详情"))
    result = await state.match.query_match_detail_replies(
        query=query,
        index=index - 1,
        show_all_heroes=True,
        analyze=state.llm_client is not None and state.llm_client.enabled,
    )
    await send(_sse_progress(70.0, "生成 AI 锐评"))

    replies = getattr(result, "replies", []) or []
    items = []
    for reply in replies:
        if not isinstance(reply, dict):
            continue
        if reply.get("type") == "image":
            items.append(
                {
                    "type": "image",
                    "content": reply.get("base64") or "",
                    "media_type": reply.get("media_type") or "image/png",
                }
            )
        elif reply.get("type") == "text":
            items.append(
                {
                    "type": "text",
                    "content": str(reply.get("data") or ""),
                    "media_type": "text/plain",
                }
            )
    if not items:
        return error_response("未生成任何结果", 500)
    return {
        "success": True,
        "data": {"type": "multi", "items": items, "ai_enabled": bool(state.llm_client and state.llm_client.enabled)},
    }


async def _query_rank_history(
    params: Dict[str, Any],
    state: AppState,
    resolved: ResolvedTarget,
    send: Callable[[bytes], Awaitable[None]],
) -> Dict[str, Any]:
    from ..response_utils import error_response, image_response, text_response
    from overstats.src.modules.dashen_rank_history.requests import DashenRankHistoryQuery

    if state.rank_history is None:
        return error_response("段位历史查询功能未启用", 503)
    prefer_img = (state.config.get("output") or {}).get("prefer_image_for_rank_history", True)

    await send(_sse_progress(15.0, "解析 BattleTag"))
    query = DashenRankHistoryQuery(
        customer_token=resolved.customer_token or None,
        bnet_id=resolved.effective_bnet_id,
    )
    await send(_sse_progress(45.0, "查询玩家资料 (2/2)"))
    result = await state.rank_history.query_rank_history(query, render=prefer_img)
    await send(_sse_progress(80.0, "渲染段位历史图片"))

    display_name = (
        result.resolved_bnet.full_id
        if getattr(result, "resolved_bnet", None)
        else resolved.display_name
    )
    if prefer_img and result.image:
        return image_response(result.image, fallback_text="段位历史：{0}".format(display_name))
    seasons = getattr(result, "seasons", []) or []
    lines = ["段位历史：{0}".format(display_name)]
    for s in seasons:
        if not isinstance(s, dict):
            continue
        season_num = s.get("season", "?")
        comp = s.get("competitive") or {}
        roles = comp.get("roles") or []
        if not roles:
            lines.append("  赛季 {0}: 无竞技数据".format(season_num))
            continue
        for role in roles:
            role_label = {"tank": "坦克", "dps": "输出", "healer": "辅助", "open": "开放"}.get(
                role.get("role_type") or "",
            )
            cur = role.get("current") or {}
            peak = role.get("peak") or {}
            lines.append(
                "  赛季 {0} {1}: 当前{2}/{3} 最高{4}/{5} 场次{6} 胜率{7:.1f}%".format(
                    season_num,
                    role_label,
                    cur.get("rank_score", 0),
                    cur.get("rank_sub_tier", 0),
                    peak.get("rank_score", 0),
                    peak.get("rank_sub_tier", 0),
                    role.get("match_sum", 0),
                    role.get("win_rate", 0),
                )
            )
    return text_response("\n".join(lines))


async def _query_strength(
    params: Dict[str, Any],
    state: AppState,
    resolved: ResolvedTarget,
    send: Callable[[bytes], Awaitable[None]],
    *,
    mode: str,
) -> Dict[str, Any]:
    from ..response_utils import error_response, image_response, text_response
    from overstats.src.modules.dashen_quick_strength.requests import DashenQuickStrengthQuery
    from overstats.src.modules.dashen_competitive_strength.requests import DashenCompetitiveStrengthQuery

    try:
        limit = int(params.get("limit", 5))
    except (TypeError, ValueError):
        limit = 5
    if limit < 1:
        limit = 1

    if mode == "quick":
        if state.quick_strength is None:
            return error_response("快速强度分析功能未启用", 503)
        prefer_img = (state.config.get("output") or {}).get("prefer_image_for_strength", True)
        await send(_sse_progress(15.0, "解析 BattleTag"))
        query = DashenQuickStrengthQuery(
            customer_token=resolved.customer_token or None,
            bnet_id=resolved.effective_bnet_id,
            limit=limit,
        )
        await send(_sse_progress(40.0, "查询战绩列表 (1/N)"))
        result = await state.quick_strength.query_quick_strength(query, render=prefer_img)
    elif mode == "competitive":
        if state.competitive_strength is None:
            return error_response("竞技强度分析功能未启用", 503)
        prefer_img = (state.config.get("output") or {}).get("prefer_image_for_strength", True)
        await send(_sse_progress(15.0, "解析 BattleTag"))
        query = DashenCompetitiveStrengthQuery(
            customer_token=resolved.customer_token or None,
            bnet_id=resolved.effective_bnet_id,
            limit=limit,
        )
        method = None
        for name in ("query_competitive_strength", "query_strength", "query"):
            candidate = getattr(state.competitive_strength, name, None)
            if callable(candidate):
                method = candidate
                break
        if method is None:
            raise HTTPException(status_code=500, detail="竞技强度模块接口不匹配")
        await send(_sse_progress(40.0, "查询战绩列表 (1/N)"))
        result = await method(query, render=prefer_img)
    else:
        return error_response("未知的强度分析模式", 400)

    await send(_sse_progress(80.0, "渲染强度图片"))
    if prefer_img and getattr(result, "image", None):
        return image_response(result.image, fallback_text="强度分析")
    summary = getattr(result, "summary", None)
    return text_response(
        "强度分析 - 平均分：{0} 段位：{1}".format(
            getattr(summary, "overall_avg_score", "?"),
            getattr(summary, "overall_avg_rank", "?"),
        )
    )


async def _query_summary(
    params: Dict[str, Any],
    state: AppState,
    resolved: ResolvedTarget,
    send: Callable[[bytes], Awaitable[None]],
) -> Dict[str, Any]:
    from ..response_utils import error_response
    from overstats.src.modules.dashen_summary.requests import DashenSummaryQuery

    if state.summary is None:
        return error_response("对局总结功能未启用", 503)

    scope = str(params.get("scope", "today") or "today")
    await send(_sse_progress(15.0, "解析 BattleTag"))
    query = DashenSummaryQuery(
        customer_token=resolved.customer_token or None,
        bnet_id=resolved.effective_bnet_id,
        scope=scope,
    )
    await send(_sse_progress(45.0, "查询战绩列表 (1/N)"))
    result = await state.summary.query_summary(query)
    await send(_sse_progress(85.0, "渲染总结图片"))

    items = []
    replies = getattr(result, "replies", []) or []
    for reply in replies:
        if not isinstance(reply, dict):
            continue
        if reply.get("type") == "image":
            items.append(
                {
                    "type": "image",
                    "content": reply.get("base64") or "",
                    "media_type": reply.get("media_type") or "image/png",
                }
            )
        elif reply.get("type") == "text":
            items.append(
                {
                    "type": "text",
                    "content": str(reply.get("data") or ""),
                    "media_type": "text/plain",
                }
            )
    if not items:
        return error_response("未生成任何结果", 500)
    return {"success": True, "data": {"type": "multi", "items": items}}


# ---------------------------------------------------------------------------
# 无 BattleTag 绑定需求的端点（不 require_account_ready，但仍沿用进度事件）
# ---------------------------------------------------------------------------
async def _query_hero_pick_rate(params, state, send):
    from .hero import get_hero_pick_rate
    await send(_sse_progress(25, "查询英雄热度"))
    return await get_hero_pick_rate(mode=str(params.get("mode") or "competitive"), rank=str(params.get("rank") or "all"), state=state)


async def _query_hero_perk(params, state, send):
    from .hero import get_hero_perk
    from ..response_utils import error_response
    hero = str(params.get("hero") or "").strip()
    if not hero:
        return error_response("请提供英雄名", 400)
    await send(_sse_progress(25, "查询英雄威能"))
    return await get_hero_perk(hero=hero, state=state)


async def _query_hero_wiki(params, state, send):
    from .hero import get_hero_wiki
    from ..response_utils import error_response
    hero = str(params.get("hero") or "").strip()
    if not hero:
        return error_response("请提供英雄名", 400)
    await send(_sse_progress(25, "查询英雄维基"))
    return await get_hero_wiki(hero=hero, question=str(params.get("question") or ""), state=state)


async def _query_shop(params, state, send):
    from .shop import get_shop
    await send(_sse_progress(25, "查询商店"))
    return await get_shop(state=state)


async def _query_patch_notes(params, state, send):
    from .patch import get_patch_notes
    await send(_sse_progress(25, "查询补丁"))
    return await get_patch_notes(kind=str(params.get("kind") or "latest"), state=state)


async def _query_search_player(params, state, send):
    from .search import search_player
    from ..response_utils import error_response
    keyword = str(params.get("keyword") or "").strip()
    if not keyword:
        return error_response("请提供搜索关键字", 400)
    await send(_sse_progress(25, "搜索玩家"))
    return await search_player(keyword=keyword, state=state)


async def _query_sameplay_or_detail(
    params: Dict[str, Any],
    state: AppState,
    resolved: ResolvedTarget,
    send: Callable[[bytes], Awaitable[None]],
    *,
    endpoint: str,
) -> Dict[str, Any]:
    from ..response_utils import error_response, text_response

    if state.sameplay is None:
        return error_response("同玩查询功能未启用", 503)
    if endpoint == "/api/sameplay":
        try:
            limit = int(params.get("limit", 10))
        except (TypeError, ValueError):
            limit = 10
        player2 = str(params.get("player2", "") or "").strip()
        if not player2:
            return error_response("请提供 player2", 400)
        await send(_sse_progress(25.0, "查询同玩"))
        try:
            # state.sameplay.query_sameplay(bnet_id1, bnet_id2, limit)
            result = state.sameplay.query_sameplay(
                resolved.effective_bnet_id, player2, limit
            )  # type: ignore[attr-defined]
        except Exception as exc:
            return error_response("查询失败：{0}".format(exc), 500)
    else:
        try:
            index = int(params.get("index", 1))
        except (TypeError, ValueError):
            index = 1
        player2 = str(params.get("player2", "") or "").strip()
        if not player2:
            return error_response("请提供 player2", 400)
        await send(_sse_progress(25.0, "查询同玩详情"))
        try:
            result = state.sameplay.query_sameplay_detail(
                resolved.effective_bnet_id, player2, index - 1
            )  # type: ignore[attr-defined]
        except Exception as exc:
            return error_response("查询失败：{0}".format(exc), 500)

    await send(_sse_progress(75.0, "生成响应"))
    text_content = str(getattr(result, "text", "") or "同玩查询结果")
    return text_response(text_content)


# ---------------------------------------------------------------------------
# endpoint 路由表
# ---------------------------------------------------------------------------
_ENDPOINT_DISPATCH: Dict[str, Any] = {
    "/api/profile": _query_profile,
    "/api/matches": _query_matches,
    "/api/match-detail": _query_match_detail,
    "/api/analyze": _query_analyze,
    "/api/rank-history": _query_rank_history,
    "/api/quick-strength": lambda p, s, r, send: _query_strength(p, s, r, send, mode="quick"),
    "/api/competitive-strength": lambda p, s, r, send: _query_strength(p, s, r, send, mode="competitive"),
    "/api/summary": _query_summary,
    "/api/hero-pick-rate": lambda p, s, r, send: _query_hero_pick_rate(p, s, send),
    "/api/hero-perk": lambda p, s, r, send: _query_hero_perk(p, s, send),
    "/api/hero-wiki": lambda p, s, r, send: _query_hero_wiki(p, s, send),
    "/api/shop": lambda p, s, r, send: _query_shop(p, s, send),
    "/api/patch-notes": lambda p, s, r, send: _query_patch_notes(p, s, send),
    "/api/search-player": lambda p, s, r, send: _query_search_player(p, s, send),
    "/api/sameplay": lambda p, s, r, send: _query_sameplay_or_detail(p, s, r, send, endpoint="/api/sameplay"),
    "/api/sameplay-detail": lambda p, s, r, send: _query_sameplay_or_detail(p, s, r, send, endpoint="/api/sameplay-detail"),
}


_ENDPOINTS_NEED_ACCOUNT = {
    "/api/profile",
    "/api/matches",
    "/api/match-detail",
    "/api/analyze",
    "/api/rank-history",
    "/api/quick-strength",
    "/api/competitive-strength",
    "/api/summary",
    "/api/sameplay",
    "/api/sameplay-detail",
}


# ---------------------------------------------------------------------------
# Streaming endpoint
# ---------------------------------------------------------------------------
class StreamRequest:
    def __init__(self, endpoint: str = "", params: Optional[Dict[str, Any]] = None):
        self.endpoint = endpoint or ""
        self.params = params if isinstance(params, dict) else {}


async def _stream_handler(
    request: Request,
    body: Dict[str, Any],
) -> StreamingResponse:
    endpoint = str(body.get("endpoint") or "").strip()
    params_raw = body.get("params") or {}
    if not isinstance(params_raw, dict):
        params_raw = {}
    # 规范化 params：所有值都转成字符串/数字/None
    params: Dict[str, Any] = {}
    for k, v in params_raw.items():
        if v is None:
            continue
        params[str(k)] = v

    state: Optional[AppState] = getattr(request.app.state, "ow_app", None)
    if state is None:
        raise HTTPException(status_code=503, detail="应用尚未初始化完成")

    handler = _ENDPOINT_DISPATCH.get(endpoint)
    if handler is None:
        raise HTTPException(status_code=400, detail="未知的 endpoint: {0}".format(endpoint))

    need_account = endpoint in _ENDPOINTS_NEED_ACCOUNT

    # 为需要账号的端点做 BattleTag 解析（复用 utils.resolve_target 的行为）
    # 这里我们直接根据 params 构造一个 ResolvedTarget；如果没有 battletag，
    # 则 fallback 到 token 绑定信息，等同于普通 endpoint 的行为。
    async def _do_stream():
        queue: asyncio.Queue[Optional[bytes]] = asyncio.Queue()

        async def _send(chunk: bytes) -> None:
            logger.info("Stream: sending %d bytes", len(chunk))
            await queue.put(chunk)

        async def _runner():
            try:
                await _send(_sse_progress(2.0, "启动查询"))
                # 解析 BattleTag（如需）——沿用 utils.resolve_target 的逻辑
                resolved: "ResolvedTarget"
                if need_account:
                    require_account_ready(state)
                    resolved = resolve_target(token=str(params.get("token") or ""), battletag=str(params.get("battletag") or ""), state=state)
                else:
                    resolved = ResolvedTarget(token="", battletag="", bnet_id="", customer_token="", display_name="", source="inline")

                # 心跳：若业务步骤耗时较长，定时补发 "仍在处理" 事件
                async def _heartbeat():
                    pct = 5.0
                    while True:
                        await asyncio.sleep(1.8)
                        pct = min(88.0, pct + 2.5)
                        await _send(_sse_progress(pct, "正在处理，请稍候…"))

                hb_task = asyncio.create_task(_heartbeat())
                try:
                    logger.info("Stream: starting handler for %s", endpoint)
                    result = await handler(params, state, resolved, _send)
                    if need_account and isinstance(result, dict) and result.get("success") is True:
                        await record_successful_ids([resolved.battletag, params.get("player2") if endpoint.startswith("/api/sameplay") else None])
                    logger.info("Stream: handler completed, result keys: %s", list(result.keys()) if isinstance(result, dict) else type(result))
                finally:
                    hb_task.cancel()
                    try:
                        await hb_task
                    except asyncio.CancelledError:
                        pass

                logger.info("Stream: sending progress 95%")
                await _send(_sse_progress(95.0, "生成响应"))
                logger.info("Stream: sending done event")
                await _send(_sse_done(result))
            except HTTPException as exc:
                await _send(_sse_error(str(exc.detail) or "查询失败", exc.status_code))
            except Exception as exc:
                logger.exception("流式查询异常: %s", exc)
                await _send(_sse_error("查询失败：{0}".format(exc), 500))
            finally:
                await queue.put(None)

        runner_task = asyncio.create_task(_runner())

        async def _gen():
            while True:
                item = await queue.get()
                if item is None:
                    logger.info("Stream: gen() received None, exiting")
                    break
                logger.info("Stream: gen() yielding %d bytes", len(item))
                yield item
            await runner_task

        return StreamingResponse(
            _gen(),
            media_type="text/event-stream; charset=utf-8",
            headers={
                "Cache-Control": "no-cache, no-transform",
                "Connection": "keep-alive",
                "X-Accel-Buffering": "no",
            },
        )

    return await _do_stream()


# ---------------------------------------------------------------------------
# 公开端点
# ---------------------------------------------------------------------------
@router.post("/stream")
async def stream_endpoint(request: Request, body: Dict[str, Any] = Body(...)):
    return await _stream_handler(request, body)


@router.get("/stream/help", include_in_schema=False)
async def stream_help(request: Request):
    return {
        "success": True,
        "supported_endpoints": sorted(_ENDPOINT_DISPATCH.keys()),
        "example": {"endpoint": "/api/matches", "params": {"battletag": "Test#1234", "limit": 5}},
    }
