from __future__ import annotations

from datetime import date, timedelta
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query

from ..app_context import AppState
from ..cache import get_or_create_cache
from ..response_utils import error_response, image_response, text_response
from .utils import (
    ResolvedTarget,
    feature_enabled,
    get_app_state,
    require_account_ready,
    resolve_target,
)

router = APIRouter(prefix="/api", tags=["summary"])

_SUMMARY_TTL = 43200  # 12 小时 - 总结通常基于历史数据，变化很慢


@router.get("/summary")
async def get_summary(
    scope: str = Query("today", description="today | yesterday | week"),
    target: ResolvedTarget = Depends(resolve_target),
    battletag: Optional[str] = Query(None, description="BattleTag；留空则使用 token 绑定账号"),
    state: AppState = Depends(require_account_ready),
):
    if state.summary is None or not feature_enabled(state.config, "enable_summary"):
        return error_response("总结功能未启用", 503)
    prefer_img = (state.config.get("output") or {}).get("prefer_image_for_summary", True)
    scope_l = (scope or "today").strip().lower()
    today = date.today()
    if scope_l == "today":
        start_date = end_date = today
    elif scope_l == "yesterday":
        start_date = end_date = today - timedelta(days=1)
    elif scope_l == "week":
        start_date = today - timedelta(days=6)
        end_date = today
    else:
        return error_response("scope 必须为 today | yesterday | week", 400)

    cache = await get_or_create_cache("summary", default_ttl_seconds=_SUMMARY_TTL, max_size=2000)
    if target.customer_token:
        base = "ct:" + target.customer_token
    else:
        base = "bnet:" + (target.effective_bnet_id or target.battletag or "")
    key = "{0}|{1}|{2}|img={3}".format(base, start_date.isoformat(), end_date.isoformat(), int(bool(prefer_img)))

    async def _compute():
        method = None
        for name in ("query_summary", "query", "get_summary"):
            candidate = getattr(state.summary, name, None)
            if callable(candidate):
                method = candidate
                break
        if method is None:
            raise HTTPException(status_code=500, detail="总结模块接口不匹配")
        try:
            return await method(
                bnet_id=target.effective_bnet_id,
                customer_token=target.customer_token,
                start_date=start_date,
                end_date=end_date,
                render=prefer_img,
            )
        except TypeError:
            return await method(
                bnet_id=target.effective_bnet_id,
                start_date=start_date,
                end_date=end_date,
                render=prefer_img,
            )

    try:
        result = await cache.get_or_compute(key, _compute, ttl_seconds=_SUMMARY_TTL)
        if prefer_img and getattr(result, "image", None):
            return image_response(result.image, fallback_text="对局总结：{0}（{1}）".format(target.display_name, scope_l))
        return text_response("对局总结：{0}（{1}）".format(target.display_name, scope_l))
    except HTTPException:
        raise
    except Exception as exc:
        return error_response("查询失败：{0}: {1}".format(type(exc).__name__, exc), 500)
