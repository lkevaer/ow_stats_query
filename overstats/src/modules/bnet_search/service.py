from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, Optional

try:
    from overstats.src.client.apiclient import DashenAPIClient
except ModuleNotFoundError:
    from src.client.apiclient import DashenAPIClient

try:
    from overstats.src.modules.errors import ModuleError
except ModuleNotFoundError:
    try:
        from src.modules.errors import ModuleError
    except ModuleNotFoundError:
        ModuleError = Exception  # type: ignore[assignment]

from .render import RenderedImage, render_bnet_search_result
from .requests import BnetSearchRequests, BnetSearchResult


@dataclass(frozen=True)
class BnetSearchOutput:
    result: BnetSearchResult
    image: Optional[RenderedImage] = None


class BnetSearchModule:
    def __init__(self, api_client: Optional[DashenAPIClient] = None) -> None:
        self.requests = BnetSearchRequests(api_client)

    async def search(self, bnet_id: str, *, render: bool = False) -> BnetSearchOutput:
        result = await self.requests.search(bnet_id)
        image = render_bnet_search_result(result) if render else None
        return BnetSearchOutput(result=result, image=image)

    async def resolve_customer_token(self, bnet_id: str) -> str:
        output = await self.search(bnet_id, render=False)
        return output.result.customer_token

    async def resolve_and_validate(self, bnet_id: str) -> tuple[str, BnetSearchResult]:
        """搜索 bnet_id 并验证反查结果，各模块共享此方法以避免代码重复。

        Returns:
            (customer_token, BnetSearchResult)

        Raises:
            ModuleError: 当搜索失败或 customer_token 为空时，
                附带详细的上游调试信息（upstream_code/msg/has_data 等）。
        """
        output = await self.search(bnet_id, render=False)
        result_obj = output.result
        customer_token = result_obj.customer_token
        if not customer_token:
            payload: Dict[str, Any] = getattr(result_obj, "payload", {}) or {}
            data = payload.get("data") if isinstance(payload, dict) else None
            raise ModuleError(
                error="bnet_not_found",
                message=f"Could not resolve customerToken from bnet_id: {bnet_id}",
                status_code=404,
                hint=(
                    "Check exact letter case and the number after '#'. "
                    "Dashen search is often case-sensitive. "
                    "If you already have customer_token, query with customer_token directly."
                ),
                details={
                    "query": getattr(result_obj, "query", bnet_id),
                    "upstream_code": payload.get("code") if isinstance(payload, dict) else None,
                    "upstream_msg": payload.get("msg") if isinstance(payload, dict) else None,
                    "has_data": isinstance(data, dict),
                    "has_customer_token": bool(customer_token),
                    "resolved_name": getattr(result_obj, "full_id", None),
                    "resolved_bnet_id": getattr(result_obj, "bnet_id", None),
                },
            )
        return customer_token, result_obj


bnet_search_module = BnetSearchModule()
