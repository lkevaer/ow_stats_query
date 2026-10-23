from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException

from ..app_context import AppState
from ..response_utils import error_response, image_response, text_response
from .utils import feature_enabled, get_app_state

router = APIRouter(prefix="/api", tags=["shop"])


@router.get("/shop")
async def get_shop(
    state: AppState = Depends(get_app_state),
):
    if state.shop is None or not feature_enabled(state.config, "enable_shop"):
        return error_response("商店查询功能未启用", 503)
    prefer_img = (state.config.get("output") or {}).get("prefer_image_for_shop", True)

    try:
        method = None
        for name in ("query_shop", "get_shop", "query"):
            candidate = getattr(state.shop, name, None)
            if callable(candidate):
                method = candidate
                break
        if method is None:
            raise RuntimeError("商店模块接口不匹配")
        result = await method(render=prefer_img)

        if prefer_img and getattr(result, "image", None):
            return image_response(result.image, fallback_text="守望先锋商店")
        return text_response("守望先锋商店")
    except HTTPException:
        raise
    except Exception as exc:
        return error_response("查询失败：{0}".format(exc), 500)
