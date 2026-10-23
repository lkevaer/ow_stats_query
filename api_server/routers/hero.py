from __future__ import annotations

from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query

from ..app_context import AppState
from ..response_utils import error_response, image_response, text_response
from .utils import feature_enabled, get_app_state

router = APIRouter(prefix="/api", tags=["hero"])


@router.get("/hero-pick-rate")
async def get_hero_pick_rate(
    mode: str = Query("competitive", description="quick | competitive"),
    rank: str = Query("all", description="all | bronze | silver | gold | platinum | diamond | master | grandmaster | champion"),
    state: AppState = Depends(get_app_state),
):
    if state.pick_rate is None or not feature_enabled(state.config, "enable_pick_rate"):
        return error_response("英雄选取率查询功能未启用", 503)
    prefer_img = (state.config.get("output") or {}).get("prefer_image_for_pick_rate", True)
    cn_map = {
        "快速": "quick", "竞技": "competitive", "全部": "all",
        "青铜": "bronze", "白银": "silver", "黄金": "gold",
        "铂金": "platinum", "钻石": "diamond", "大师": "master",
        "宗师": "grandmaster", "冠军": "champion",
    }
    mode_en = cn_map.get(mode, mode)
    rank_en = cn_map.get(rank, rank)

    try:
        from overstats.src.modules.ow_hero_pick_rate.requests import OWHeroPickRateQuery
        result = await state.pick_rate.query_pick_rate(
            OWHeroPickRateQuery(game_mode=mode_en, mmr=rank_en), render=prefer_img
        )

        if prefer_img and getattr(result, "image", None):
            return image_response(result.image, fallback_text="英雄热度榜单：{0} / {1}".format(mode_en, rank_en))
        return text_response("英雄热度榜单：{0} / {1}".format(mode_en, rank_en))
    except HTTPException:
        raise
    except Exception as exc:
        return error_response("查询失败：{0}".format(exc), 500)


@router.get("/hero-perk")
async def get_hero_perk(
    hero: str = Query(..., description="英雄名（中文或英文均可）"),
    state: AppState = Depends(get_app_state),
):
    if state.hero_perk is None:
        return error_response("英雄威能查询功能未启用", 503)
    prefer_img = (state.config.get("output") or {}).get("prefer_image_for_pick_rate", True)

    try:
        from overstats.src.modules.ow_hero_perk.requests import OWHeroPerkQuery
        result = await state.hero_perk.query_perk(OWHeroPerkQuery(hero=hero), render=prefer_img)

        if prefer_img and getattr(result, "image", None):
            return image_response(result.image, fallback_text="英雄威能：{0}".format(hero))
        return text_response("英雄威能：{0}".format(hero))
    except HTTPException:
        raise
    except Exception as exc:
        return error_response("查询失败：{0}".format(exc), 500)


@router.get("/hero-wiki")
async def get_hero_wiki(
    hero: str = Query(..., description="英雄名"),
    question: Optional[str] = Query(None, description="可选问题；为空则返回英雄资料卡"),
    state: AppState = Depends(get_app_state),
):
    if state.hero_wiki is None:
        return error_response("英雄维基查询功能未启用", 503)

    try:
        from overstats.src.modules.ow_hero_wiki.requests import OWHeroWikiQuery
        result = await state.hero_wiki.query_hero(
            OWHeroWikiQuery(hero=hero, question=question or ""), render=True
        )

        if getattr(result, "image", None):
            return image_response(result.image, fallback_text="英雄维基：{0}".format(hero))
        return text_response(result.answer or result.overview or "英雄维基：{0}".format(hero))
    except HTTPException:
        raise
    except Exception as exc:
        return error_response("查询失败：{0}".format(exc), 500)
