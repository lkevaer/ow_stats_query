from __future__ import annotations

from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query

from ..app_context import AppState
from ..response_utils import error_response, image_response, text_response
from .utils import (
    ResolvedTarget,
    get_app_state,
    require_account_ready,
    resolve_target,
)

router = APIRouter(prefix="/api", tags=["sameplay"])


def _target_base(target: ResolvedTarget) -> str:
    if target.customer_token:
        return "ct:" + target.customer_token
    return "bnet:" + (target.effective_bnet_id or target.battletag or "")


@router.get("/sameplay")
async def get_sameplay(
    player2: str = Query(..., description="第二个玩家的 BattleTag"),
    target: ResolvedTarget = Depends(resolve_target),
    limit: int = Query(10, ge=1, le=40),
    state: AppState = Depends(require_account_ready),
):
    if state.sameplay is None:
        return error_response("同玩查询功能未启用", 503)
    tag2 = player2.strip()
    if not tag2 or "#" not in tag2:
        return error_response("player2 格式不正确（需要包含 #）", 400)

    try:
        method = None
        for name in ("query_sameplay_matches", "query_sameplay", "query", "get_sameplay"):
            candidate = getattr(state.sameplay, name, None)
            if callable(candidate):
                method = candidate
                break
        if method is None:
            raise HTTPException(status_code=500, detail="同玩模块接口不匹配")
        try:
            result = await method(
                bnet_id_1=target.effective_bnet_id,
                bnet_id_2=tag2,
                customer_token_1=target.customer_token,
                limit=limit,
                render=False,
            )
        except TypeError:
            result = await method(target.effective_bnet_id, tag2, limit)

        if result is None:
            return error_response("同玩查询失败", 500)
        if hasattr(result, "image") and result.image:
            return image_response(result.image, fallback_text="同玩：{0} & {1}".format(target.display_name, tag2))
        matches = getattr(result, "matches", []) or []
        lines = ["同玩对局：{0} & {1}（共 {2} 场）".format(target.display_name, tag2, len(matches))]
        for m in matches:
            if isinstance(m, dict):
                win = "胜" if m.get("matchRet") == 1 else "败"
                hero = m.get("heroGuid", "?")
                lines.append("  {0} | {1}".format(win, hero))
        return text_response("\n".join(lines))
    except HTTPException:
        raise
    except Exception as exc:
        return error_response("查询失败：{0}".format(type(exc).__name__, exc), 500)


@router.get("/sameplay-detail")
async def get_sameplay_detail(
    player2: str = Query(...),
    index: int = Query(1, ge=1),
    target: ResolvedTarget = Depends(resolve_target),
    state: AppState = Depends(require_account_ready),
):
    if state.match is None:
        return error_response("同玩详情功能未启用", 503)
    tag2 = player2.strip()
    if not tag2 or "#" not in tag2:
        return error_response("player2 格式不正确（需要包含 #）", 400)

    try:
        from overstats.src.modules.dashen_match.requests import DashenMatchQuery

        query = DashenMatchQuery(
            customer_token=target.customer_token,
            bnet_id=target.effective_bnet_id,
        )
        result = await state.match.query_match_detail_by_index(query, index - 1, render=True)

        if result and getattr(result, "image", None):
            return image_response(result.image, fallback_text="同玩详情 #{0}".format(index))
        return text_response("同玩详情 #{0}（{1} & {2}）".format(index, target.display_name, tag2))
    except HTTPException:
        raise
    except Exception as exc:
        return error_response("查询失败：{0}".format(type(exc).__name__, exc), 500)


@router.get("/sameplay-analyze")
async def sameplay_analyze(
    player2: str = Query(...),
    index: int = Query(1, ge=1),
    target: ResolvedTarget = Depends(resolve_target),
    state: AppState = Depends(require_account_ready),
):
    if state.match is None:
        return error_response("同玩锐评功能未启用", 503)
    tag2 = player2.strip()
    if not tag2 or "#" not in tag2:
        return error_response("player2 格式不正确（需要包含 #）", 400)
    llm_enabled = (
        state.llm_client is not None
        and state.llm_client.enabled
        and (state.config.get("analysis") or {}).get("enable_ai_match_replies", True)
    )

    try:
        from overstats.src.modules.dashen_match.requests import DashenMatchQuery

        query = DashenMatchQuery(
            customer_token=target.customer_token,
            bnet_id=target.effective_bnet_id,
        )
        result = await state.match.query_match_detail_replies(
            query=query,
            index=index - 1,
            show_all_heroes=True,
            analyze=llm_enabled,
        )

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
            "data": {
                "type": "multi",
                "players": [target.display_name, tag2],
                "items": items,
                "ai_enabled": llm_enabled,
            },
        }
    except HTTPException:
        raise
    except Exception as exc:
        return error_response("查询失败：{0}".format(type(exc).__name__, exc), 500)
