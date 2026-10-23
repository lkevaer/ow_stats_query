from __future__ import annotations

from fastapi import APIRouter, Depends, Query

from ..app_context import AppState
from ..cache import get_or_create_cache
from ..response_utils import error_response, image_response, text_response
from .utils import feature_enabled, get_app_state

router = APIRouter(prefix="/api", tags=["patch"])

_PATCH_TTL = 21600  # 6 小时 - 补丁更新慢


@router.get("/patch-notes")
async def get_patch_notes(
    kind: str = Query("latest", description="latest | small | big | 最新 | 小更新 | 大更新"),
    state: AppState = Depends(get_app_state),
):
    if state.patch_notes is None or not feature_enabled(state.config, "enable_patch_notes"):
        return error_response("补丁查询功能未启用", 503)
    cn_map = {"最新": "latest", "小更新": "small", "大更新": "big"}
    kind_en = cn_map.get(kind, kind)
    prefer_img = (state.config.get("output") or {}).get("prefer_image_for_patch_notes", True)
    cache = await get_or_create_cache("patch_notes", default_ttl_seconds=_PATCH_TTL, max_size=64)
    key = "kind={0}|img={1}".format(kind_en, int(bool(prefer_img)))

    async def _compute():
        method = None
        for name in ("query_patch_notes", "get_patch_notes", "query"):
            candidate = getattr(state.patch_notes, name, None)
            if callable(candidate):
                method = candidate
                break
        if method is None:
            raise RuntimeError("补丁模块接口不匹配")
        return await method(patch_kind=kind_en, render=prefer_img)

    try:
        result = await cache.get_or_compute(key, _compute, ttl_seconds=_PATCH_TTL)
        if prefer_img and getattr(result, "image", None):
            return image_response(result.image, fallback_text="补丁说明：{0}".format(kind_en))
        return text_response("补丁说明：{0}".format(kind_en))
    except Exception as exc:
        return error_response("查询失败：{0}".format(exc), 500)
