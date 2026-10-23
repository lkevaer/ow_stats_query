from __future__ import annotations

from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query

from ..app_context import AppState
from ..response_utils import error_response, image_response, text_response
from .utils import (
    ResolvedTarget,
    feature_enabled,
    get_app_state,
    require_account_ready,
    resolve_target,
)

router = APIRouter(prefix="/api", tags=["player"])


@router.get("/profile")
async def get_profile(
    target: ResolvedTarget = Depends(resolve_target),
    battletag: Optional[str] = Query(None, description="BattleTag；留空则使用 token 绑定的账号"),
    state: AppState = Depends(require_account_ready),
):
    if state.profile is None or not feature_enabled(state.config, "enable_profile"):
        return error_response("玩家资料查询功能未启用", 503)

    prefer_img = (state.config.get("output") or {}).get("prefer_image_for_profile", True)

    try:
        from overstats.src.modules.dashen_profile.requests import DashenProfileQuery

        if target.customer_token:
            query = DashenProfileQuery(
                customer_token=target.customer_token,
                bnet_id=target.effective_bnet_id,
            )
        else:
            query = DashenProfileQuery(bnet_id=target.effective_bnet_id)
        result = await state.profile.query_profile(query, render=prefer_img)

        display_name = (
            result.resolved_bnet.full_id
            if getattr(result, "resolved_bnet", None)
            else target.display_name
        )
        if prefer_img and result.image:
            return image_response(result.image, fallback_text="玩家：{0}".format(display_name))
        return text_response("玩家：{0}".format(display_name))
    except HTTPException:
        raise
    except Exception as exc:
        return error_response(
            "查询失败：{0}: {1}".format(type(exc).__name__, exc),
            500,
            details={"battletag": target.battletag, "customer_token_resolved": bool(target.customer_token)},
        )


@router.get("/matches")
async def get_matches(
    target: ResolvedTarget = Depends(resolve_target),
    limit: int = Query(5, ge=1, le=30, description="最近 N 场"),
    state: AppState = Depends(require_account_ready),
):
    if state.match is None or not feature_enabled(state.config, "enable_match"):
        return error_response("战绩查询功能未启用", 503)
    prefer_img = (state.config.get("output") or {}).get("prefer_image_for_match", True)

    try:
        from overstats.src.modules.dashen_match.requests import DashenMatchQuery

        if target.customer_token:
            query = DashenMatchQuery(
                customer_token=target.customer_token,
                bnet_id=target.effective_bnet_id,
                target_count=limit,
            )
        else:
            query = DashenMatchQuery(bnet_id=target.effective_bnet_id, target_count=limit)
        result = await state.match.query_match_list(query, render=prefer_img)

        display_name = (
            result.resolved_bnet.full_id
            if getattr(result, "resolved_bnet", None)
            else target.display_name
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
        return text_response("\n".join(lines))
    except HTTPException:
        raise
    except Exception as exc:
        return error_response(
            "查询失败：{0}: {1}".format(type(exc).__name__, exc),
            500,
            details={"battletag": target.battletag, "customer_token_resolved": bool(target.customer_token)},
        )


@router.get("/match-detail")
async def get_match_detail(
    index: int = Query(1, ge=1),
    target: ResolvedTarget = Depends(resolve_target),
    state: AppState = Depends(require_account_ready),
):
    if state.match is None or not feature_enabled(state.config, "enable_match_detail"):
        return error_response("对局详情查询功能未启用", 503)
    prefer_img = (state.config.get("output") or {}).get("prefer_image_for_match_detail", True)

    try:
        from overstats.src.modules.dashen_match.requests import DashenMatchQuery

        if target.customer_token:
            query = DashenMatchQuery(
                customer_token=target.customer_token,
                bnet_id=target.effective_bnet_id,
            )
        else:
            query = DashenMatchQuery(bnet_id=target.effective_bnet_id)
        result = await state.match.query_match_detail_by_index(query, index - 1, render=prefer_img)

        if prefer_img and result.image:
            return image_response(result.image, fallback_text="对局详情 #{0}".format(index))
        return text_response("对局详情 #{0}".format(index))
    except HTTPException:
        raise
    except Exception as exc:
        return error_response(
            "查询失败：{0}: {1}".format(type(exc).__name__, exc),
            500,
            details={"battletag": target.battletag, "index": index},
        )


@router.get("/analyze")
async def analyze_match(
    index: int = Query(1, ge=1),
    target: ResolvedTarget = Depends(resolve_target),
    state: AppState = Depends(require_account_ready),
):
    if state.match is None or not feature_enabled(state.config, "enable_match_detail"):
        return error_response("对局锐评功能未启用", 503)

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
                "items": items,
                "ai_enabled": llm_enabled,
            },
        }
    except HTTPException:
        raise
    except Exception as exc:
        return error_response("查询失败：{0}: {1}".format(type(exc).__name__, exc), 500)


@router.get("/rank-history")
async def get_rank_history(
    target: ResolvedTarget = Depends(resolve_target),
    state: AppState = Depends(require_account_ready),
):
    if state.rank_history is None or not feature_enabled(state.config, "enable_rank_history"):
        return error_response("段位历史查询功能未启用", 503)
    prefer_img = (state.config.get("output") or {}).get("prefer_image_for_rank_history", True)

    try:
        from overstats.src.modules.dashen_rank_history.requests import DashenRankHistoryQuery

        if target.customer_token:
            query = DashenRankHistoryQuery(
                customer_token=target.customer_token,
                bnet_id=target.effective_bnet_id,
            )
        else:
            query = DashenRankHistoryQuery(bnet_id=target.effective_bnet_id)
        result = await state.rank_history.query_rank_history(query, render=prefer_img)

        display_name = (
            result.resolved_bnet.full_id
            if getattr(result, "resolved_bnet", None)
            else target.display_name
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
    except HTTPException:
        raise
    except Exception as exc:
        return error_response("查询失败：{0}: {1}".format(type(exc).__name__, exc), 500)


@router.get("/quick-strength")
async def get_quick_strength(
    limit: int = Query(5, ge=3, le=12),
    target: ResolvedTarget = Depends(resolve_target),
    state: AppState = Depends(require_account_ready),
):
    if state.quick_strength is None or not feature_enabled(state.config, "enable_quick_strength"):
        return error_response("快速强度分析功能未启用", 503)
    prefer_img = (state.config.get("output") or {}).get("prefer_image_for_strength", True)

    try:
        from overstats.src.modules.dashen_quick_strength.requests import DashenQuickStrengthQuery

        query = DashenQuickStrengthQuery(
            customer_token=target.customer_token,
            bnet_id=target.effective_bnet_id,
            limit=limit,
        )
        result = await state.quick_strength.query_quick_strength(query, render=prefer_img)

        if prefer_img and getattr(result, "image", None):
            return image_response(result.image, fallback_text="快速强度分析")
        summary = getattr(result, "summary", None)
        return text_response(
            "快速强度分析 - 平均分：{0} 段位：{1}".format(
                getattr(summary, "overall_avg_score", "?"),
                getattr(summary, "overall_avg_rank", "?"),
            )
        )
    except HTTPException:
        raise
    except Exception as exc:
        return error_response("查询失败：{0}: {1}".format(type(exc).__name__, exc), 500)


@router.get("/competitive-strength")
async def get_competitive_strength(
    limit: int = Query(5, ge=3, le=12),
    target: ResolvedTarget = Depends(resolve_target),
    state: AppState = Depends(require_account_ready),
):
    if state.competitive_strength is None or not feature_enabled(
        state.config, "enable_competitive_strength"
    ):
        return error_response("竞技强度分析功能未启用", 503)
    prefer_img = (state.config.get("output") or {}).get("prefer_image_for_strength", True)

    try:
        from overstats.src.modules.dashen_competitive_strength.requests import DashenCompetitiveStrengthQuery

        query = DashenCompetitiveStrengthQuery(
            customer_token=target.customer_token,
            bnet_id=target.effective_bnet_id,
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
        result = await method(query, render=prefer_img)

        if prefer_img and getattr(result, "image", None):
            return image_response(result.image, fallback_text="竞技强度分析")
        summary = getattr(result, "summary", None)
        return text_response(
            "竞技强度分析 - 平均分：{0} 段位：{1}".format(
                getattr(summary, "overall_avg_score", "?"),
                getattr(summary, "overall_avg_rank", "?"),
            )
        )
    except HTTPException:
        raise
    except Exception as exc:
        return error_response("查询失败：{0}: {1}".format(type(exc).__name__, exc), 500)
