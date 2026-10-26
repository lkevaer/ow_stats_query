from __future__ import annotations

from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query, Request

from ..bindings import BindingRecord, generate_token, is_valid_token
from ..response_utils import error_response
from .utils import _bindings_manager, _get_bnet_search, get_app_state

router = APIRouter(prefix="/api", tags=["bind"])


@router.post("/bind")
async def bind_account(
    request: Request,
    token: Optional[str] = Query(
        None,
        description="用户 token；留空则由服务器自动生成新 token 返回。",
    ),
    battletag: str = Query(..., description="守望先锋 BattleTag，形如 名字#数字。"),
    state=Depends(get_app_state),
):
    """绑定玩家账号。

    处理流程：
      1. 校验 BattleTag 格式。
      2. 调用 bnet_search 反查 customer_token / bnet_id（若搜索模块不可用则跳过）。
      3. 把 tag + 反查结果写入 bindings.json，供后续战绩查询使用。

    返回体说明：
      - success: true 表示绑定完成；false 表示参数或内部错误。
      - customer_token_resolved: true 表示成功反查到了 customer_token，
        之后直接调用 /api/profile、/api/matches 等接口时无需再次传 battletag。
    """
    bm = _bindings_manager(state)
    if bm is None:
        raise HTTPException(status_code=503, detail="绑定系统未初始化。")

    tag = str(battletag or "").strip()
    if not tag or "#" not in tag:
        return error_response("BattleTag 格式不正确，正确格式：名字#数字", 400)

    token = request.headers.get("x-user-token") or token
    provided_token = token.strip() if isinstance(token, str) and token.strip() else ""
    if not provided_token or not is_valid_token(provided_token):
        provided_token = generate_token()

    # 默认值：customer_token / bnet_id 为空，display_name 使用用户输入
    customer_token = ""
    bnet_id = tag
    display_name = tag
    icon_url = ""
    search_error: Optional[str] = None

    search_module = _get_bnet_search(state)
    if search_module is not None:
        try:
            output = await search_module.search(tag, render=False)
            result = getattr(output, "result", None)
            if result is not None:
                # BnetSearchResult 暴露了 customer_token / bnet_id / full_id / icon_url 属性
                customer_token = str(getattr(result, "customer_token", "") or "").strip()
                bnet_id_raw = str(getattr(result, "bnet_id", "") or "").strip()
                if bnet_id_raw:
                    bnet_id = bnet_id_raw
                full_id = str(getattr(result, "full_id", "") or "").strip()
                if full_id:
                    display_name = full_id
                icon_url = str(getattr(result, "icon_url", "") or "").strip()
        except Exception as exc:
            # 搜索失败不应阻止绑定——绑定本身只是本地操作；把错误信息写入响应体供前端展示。
            search_error = "暂未取得公开身份信息，可稍后重试"
    else:
        search_error = "bnet_search 模块未初始化（通常是由于 dashen_accounts 未配置）"

    record = BindingRecord(
        battletag=tag,
        customer_token=customer_token,
        bnet_id=bnet_id,
        display_name=display_name,
        icon_url=icon_url,
    )
    bm.set(provided_token, record)

    resolved = bool(customer_token)
    resp = {
        "success": True,
        "token": provided_token,
        "battletag": record.battletag,
        "display_name": record.display_name,
        "customer_token_resolved": resolved,
        "icon_url": record.icon_url,
        "message": "绑定完成" if resolved else "已绑定账号，但未能反查到 customer_token，稍后会自动重试。",
    }
    if search_error:
        resp["search_error"] = search_error
    return resp


@router.post("/unbind")
async def unbind_account(
    request: Request,
    token: Optional[str] = Query(None, description="兼容旧调用；推荐 X-User-Token 请求头"),
    state=Depends(get_app_state),
):
    bm = _bindings_manager(state)
    if bm is None:
        raise HTTPException(status_code=503, detail="绑定系统未初始化。")
    t = str(request.headers.get("x-user-token") or token or "").strip()
    if not t:
        return error_response("缺少 token 参数", 400)
    existed = bm.remove(t)
    if not existed:
        return error_response("当前 token 没有绑定任何账号", 404)
    return {"success": True, "message": "已解绑账号"}


@router.get("/my-bind")
async def my_bind(
    request: Request,
    token: Optional[str] = Query(None, description="兼容旧调用；推荐 X-User-Token 请求头"),
    state=Depends(get_app_state),
):
    bm = _bindings_manager(state)
    if bm is None:
        raise HTTPException(status_code=503, detail="绑定系统未初始化。")
    t = str(request.headers.get("x-user-token") or token or "").strip()
    rec = bm.get(t)
    if rec is None:
        return error_response("当前 token 没有绑定账号", 404)
    return {
        "success": True,
        "token": t,
        "battletag": rec.battletag,
        "display_name": rec.display_name,
        "customer_token_resolved": bool(rec.customer_token),
        "icon_url": rec.icon_url,
    }


@router.post("/refresh-token")
async def refresh_binding_token(
    request: Request,
    token: Optional[str] = Query(None, description="兼容旧调用；推荐 X-User-Token 请求头"),
    state=Depends(get_app_state),
):
    """重新为已绑定的账号调用 bnet_search 刷新 customer_token。

    适用于首次绑定时搜索失败，或 customer_token 失效需要重新反查的场景。
    """
    bm = _bindings_manager(state)
    if bm is None:
        raise HTTPException(status_code=503, detail="绑定系统未初始化。")
    t = str(request.headers.get("x-user-token") or token or "").strip()
    rec = bm.get(t)
    if rec is None:
        return error_response("当前 token 没有绑定账号，请先调用 POST /api/bind。", 404)

    search_module = _get_bnet_search(state)
    if search_module is None:
        return error_response(
            "bnet_search 模块未初始化（通常是由于 dashen_accounts 未配置）",
            503,
        )

    try:
        output = await search_module.search(rec.battletag, render=False)
        result = getattr(output, "result", None)
        new_customer_token = ""
        new_bnet_id = rec.battletag
        new_display = rec.display_name
        new_icon = rec.icon_url
        if result is not None:
            new_customer_token = str(getattr(result, "customer_token", "") or "").strip()
            bnet_id_raw = str(getattr(result, "bnet_id", "") or "").strip()
            if bnet_id_raw:
                new_bnet_id = bnet_id_raw
            full_id = str(getattr(result, "full_id", "") or "").strip()
            if full_id:
                new_display = full_id
            new_icon = str(getattr(result, "icon_url", "") or "").strip()
    except Exception as exc:
        return error_response(f"重新搜索失败：{type(exc).__name__}: {exc}", 502)

    if not new_customer_token:
        return error_response(
            f"未能为 {rec.battletag} 反查到 customer_token，请检查 BattleTag 是否正确。",
            502,
        )

    updated = bm.update_customer_token(
        token=t,
        customer_token=new_customer_token,
        bnet_id=new_bnet_id,
        display_name=new_display,
        icon_url=new_icon,
    )
    if updated is None:
        return error_response("记录在刷新期间被删除，请重新绑定。", 409)

    return {
        "success": True,
        "token": t,
        "battletag": updated.battletag,
        "display_name": updated.display_name,
        "customer_token_resolved": True,
        "icon_url": updated.icon_url,
        "message": "customer_token 已刷新，之后查询战绩时可直接使用该 token。",
    }


@router.get("/token/new")
async def new_token():
    """生成一个新的 user token（未绑定任何账号）。"""
    return {"success": True, "token": generate_token()}
