from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

from fastapi import Depends, HTTPException, Request

from ..app_context import AppState


def get_app_state(request: Request) -> AppState:
    state = getattr(request.app.state, "ow_app", None)
    if state is None:
        raise HTTPException(status_code=503, detail="服务尚未初始化")
    return state


def _bindings_manager(state: AppState):
    return getattr(state, "bindings_manager", None)


def _get_bnet_search(state: AppState):
    return getattr(state, "bnet_search", None)


def require_token(token: Optional[str] = None) -> str:
    if not token or not isinstance(token, str) or not token.strip():
        raise HTTPException(status_code=400, detail="缺少 user token (token 参数)")
    return token.strip()


@dataclass(frozen=True)
class ResolvedTarget:
    """解析后的玩家目标信息，供各路由使用。

    - token: 用户提供的 user_token（若为空则表明本次查询是通过显式 battletag 传入）
    - battletag: 用户输入的 BattleTag，便于错误提示与日志追溯
    - bnet_id: 传给 overstats 模块查询时使用的玩家标识（bnet_id 优先；若为空格则使用 battletag）
    - customer_token: 直接可用时使用该玩家的 customer_token（若已缓存则直接传入，否则为空字符串）
    - display_name: 用于顶部展示名（可能是中文昵称 / full_id）
    - source: 标识本次解析的来源（"binding" 表示从 token 绑定记录读取；"inline" 表示调用方显式传入 battletag）
    """

    token: str
    battletag: str
    bnet_id: str
    customer_token: str
    display_name: str
    source: str = "binding"

    @property
    def effective_bnet_id(self) -> str:
        """实际传给 overstats 模块的 bnet_id：优先使用反查得到的 bnet_id，否则退回用户输入 tag"""
        return self.bnet_id or self.battletag


def resolve_target(
    token: Optional[str] = None,
    battletag: Optional[str] = None,
    state: AppState = Depends(get_app_state),
    request: Request = None,
) -> ResolvedTarget:
    """统一解析入口。

    优先级：
      1. 若调用方显式传入 battletag，则把它当作 bnet_id 使用；
         若同时已有 token 绑定记录且其中 customer_token 可用，直接采用该 token 加速查询。
      2. 否则根据 user_token 必须已绑定某个玩家；从绑定记录读取 bnet_id/customer_token。
    """
    if request is not None:
        token = request.headers.get("x-user-token") or token
    explicit_tag = battletag.strip() if isinstance(battletag, str) and battletag.strip() else ""

    bm = _bindings_manager(state)
    binding_rec = None
    if bm is not None:
        binding_rec = bm.get(token)

    if explicit_tag:
        # 调用方显式传入了 battletag → 以它为主；但若绑定记录中有 customer_token 也一并利用
        if "#" not in explicit_tag:
            raise HTTPException(status_code=400, detail="BattleTag 格式不正确（需要包含 #）")
        customer_token = ""
        bnet_id = explicit_tag
        display_name = explicit_tag
        source = "inline"
        if binding_rec is not None and binding_rec.battletag == explicit_tag and binding_rec.customer_token:
            # 绑定记录有 customer_token，直接复用（无需再次反查）
            customer_token = binding_rec.customer_token
            bnet_id = binding_rec.bnet_id or explicit_tag
            if binding_rec.display_name:
                display_name = binding_rec.display_name
        target = ResolvedTarget(
            token=token or "",
            battletag=explicit_tag,
            bnet_id=bnet_id,
            customer_token=customer_token,
            display_name=display_name,
            source=source,
        )
        if request is not None:
            request.state.query_battletag = target.battletag
        return target

    # 走绑定记录路径
    token = require_token(token)
    if bm is None:
        raise HTTPException(status_code=503, detail="绑定系统未初始化。")
    if binding_rec is None:
        raise HTTPException(
            status_code=400,
            detail=(
                "当前 token 尚未绑定账号。请先调用 POST /api/bind?battletag=名字%23数字，"
                "或在查询接口中同时传入 battletag 参数。"
            ),
        )
    if not binding_rec.customer_token and not binding_rec.battletag:
        raise HTTPException(
            status_code=400,
            detail="绑定记录中缺少有效信息，请重新调用 POST /api/bind。",
        )

    target = ResolvedTarget(
        token=token,
        battletag=binding_rec.battletag,
        bnet_id=binding_rec.bnet_id or binding_rec.battletag,
        customer_token=binding_rec.customer_token,
        display_name=binding_rec.display_name or binding_rec.battletag,
        source="binding",
    )
    if request is not None:
        request.state.query_battletag = target.battletag
    return target


# ---------------------------------------------------------------------------
# 账号就绪检查：确保 API_CLIENT 已被初始化（即配置文件中 dashen_accounts 有效）
# ---------------------------------------------------------------------------
def require_account_ready(state: AppState = Depends(get_app_state)) -> AppState:
    if getattr(state, "api_client", None) is not None:
        return state
    raise HTTPException(
        status_code=503,
        detail=(
            "尚未配置网易大神凭据：请在本机 .env 填写 OW_DASHEN_ROLE_ID 和 "
            "OW_DASHEN_TOKEN 后重启服务。网页绑定 BattleTag 不能代替这一步。"
        ),
    )


def feature_enabled(cfg, feature_name: str) -> bool:
    if not isinstance(cfg, dict):
        return True
    features = cfg.get("features") or {}
    if feature_name not in features:
        return True
    return bool(features.get(feature_name, True))
