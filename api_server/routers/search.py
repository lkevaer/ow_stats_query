from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query

from ..app_context import AppState
from ..response_utils import error_response, text_response
from .utils import feature_enabled, get_app_state

router = APIRouter(prefix="/api", tags=["search"])


@router.get("/search-player")
async def search_player(
    keyword: str = Query(..., description="BattleTag 关键字（模糊搜索本地缓存）"),
    state: AppState = Depends(get_app_state),
):
    if state.identity_search is None or not feature_enabled(state.config, "enable_identity_search"):
        return error_response("玩家搜索功能未启用", 503)

    try:
        from overstats.src.modules.player_identity_search.service import PlayerIdentitySearchQuery
        result = await state.identity_search.search(PlayerIdentitySearchQuery(bnet_id=keyword))
        formatted = [item.to_dict() for item in result.matches]
        lines = ["本地缓存匹配 {0} 位玩家".format(len(formatted))]
        lines.extend(item["battletag"] or item["bnet_id"] for item in formatted)
        response = text_response("\n".join(lines))
        response.update(keyword=keyword, count=len(formatted), results=formatted)
        return response
    except Exception as e:
        return error_response(f"查询失败：{e}", 500)


@router.get("/self-check")
async def self_check(state: AppState = Depends(get_app_state)):
    return {
        "success": True,
        "initialized": state.initialized,
        "api_client_ready": state.api_client is not None,
        "bnet_search_ready": state.bnet_search is not None,
        "profile_ready": state.profile is not None,
        "match_ready": state.match is not None,
        "rank_history_ready": state.rank_history is not None,
        "quick_strength_ready": state.quick_strength is not None,
        "competitive_strength_ready": state.competitive_strength is not None,
        "summary_ready": state.summary is not None,
        "sameplay_ready": state.sameplay is not None,
        "pick_rate_ready": state.pick_rate is not None,
        "shop_ready": state.shop is not None,
        "patch_notes_ready": state.patch_notes is not None,
        "identity_search_ready": state.identity_search is not None,
        "hero_perk_ready": state.hero_perk is not None,
        "hero_wiki_ready": state.hero_wiki is not None,
        "llm_enabled": state.llm_client is not None and state.llm_client.enabled,
    }
