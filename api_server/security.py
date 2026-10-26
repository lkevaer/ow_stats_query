from __future__ import annotations

import ipaddress
import asyncio
import secrets
import time
import uuid

from fastapi import HTTPException, Request
from .ratelimit import SlidingWindowLimiter

DEFAULT_TRUSTED_PROXIES = ["127.0.0.1/32", "::1/128"]


def client_ip(request: Request) -> str:
    peer = request.client.host if request.client else "unknown"
    state = getattr(request.app.state, "ow_app", None)
    config = getattr(state, "config", {}) or {}
    networks = (config.get("security") or {}).get("trusted_proxy_cidrs", DEFAULT_TRUSTED_PROXIES)
    if isinstance(networks, str):
        networks = networks.split(",")
    try:
        trusted = [ipaddress.ip_network(value.strip(), strict=False) for value in networks]
        address = ipaddress.ip_address(peer)
        if not any(address in network for network in trusted):
            return str(address)
        chain = [ipaddress.ip_address(value.strip()) for value in request.headers.get("x-forwarded-for", "").split(",") if value.strip()]
        # Walk from the immediate proxy, never trust an arbitrary first entry.
        for address in reversed(chain):
            if not any(address in network for network in trusted):
                return str(address)
        return str(chain[0]) if chain else peer
    except (ValueError, TypeError, AttributeError):
        return peer


def is_local(request: Request) -> bool:
    try:
        return ipaddress.ip_address(client_ip(request)).is_loopback
    except ValueError:
        return False


def require_admin(request: Request):
    state = getattr(request.app.state, "ow_app", None)
    config = getattr(state, "config", {}) or {}
    expected = str((config.get("security") or {}).get("admin_api_key") or "")
    provided = request.headers.get("x-admin-key") or ""
    if not expected or not secrets.compare_digest(provided, expected):
        raise HTTPException(403, "此操作需要管理员凭据")


def internal_error(request_id=None):
    request_id = request_id or uuid.uuid4().hex
    return {"success": False, "error": "服务暂时无法完成请求，请稍后重试", "status_code": 500, "request_id": request_id}


def request_limits(state):
    limits = getattr(state, "request_limits", None)
    if limits is None:
        cfg = (getattr(state, "config", {}) or {}).get("rate_limit") or {}
        limits = {"query": SlidingWindowLimiter(int(cfg.get("heavy_queries_per_ip_per_minute", 3)), 60),
                  "browser": SlidingWindowLimiter(1, float(cfg.get("heavy_browser_cooldown_seconds", 30))),
                  "ocr": SlidingWindowLimiter(int(cfg.get("ocr_per_ip_per_minute", 6)), 60),
                  "progress": SlidingWindowLimiter(240, 60)}
        state.request_limits = limits
    return limits


def admit_heavy(request: Request, kind="query"):
    state = getattr(request.app.state, "ow_app", None) or request.app.state
    if ((getattr(state, "config", {}) or {}).get("rate_limit") or {}).get("enable_heavy_limits") is False:
        return
    limits = request_limits(state)
    ip = client_ip(request)
    ok, retry = limits[kind].try_with_retry_after(ip)
    if ok and kind == "query":
        identity = getattr(request.state, "browser_id", "") or request.cookies.get("ow_browser_id", "")
        if identity:
            ok, retry = limits["browser"].try_with_retry_after(ip + ":" + identity)
    if not ok:
        from .metrics import REGISTRY
        REGISTRY.counter("rate_limit_throttled_total", {"rule": "heavy_" + kind, "route": kind}).inc()
        seconds = max(1, int(retry) + 1)
        raise HTTPException(429, f"操作过于频繁，请在{seconds}秒后重试", headers={"Retry-After": str(seconds)})


class RequestBodyLimit:
    """Bound JSON/base64 bodies before parsing, including chunked requests."""
    def __init__(self, app, maximum=12 * 1024 * 1024):
        self.app, self.maximum = app, maximum

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http" or scope["method"] not in {"POST", "PUT", "PATCH"}:
            return await self.app(scope, receive, send)
        from fastapi.responses import JSONResponse
        chunks, total = [], 0
        deadline = time.monotonic() + 30
        try:
            while True:
                message = await asyncio.wait_for(receive(), max(.01, deadline - time.monotonic()))
                if message["type"] == "http.disconnect":
                    return
                total += len(message.get("body", b""))
                if total > self.maximum:
                    return await JSONResponse({"success": False, "error": "请求体不能超过12 MB"}, status_code=413)(scope, receive, send)
                chunks.append(message)
                if not message.get("more_body", False):
                    break
        except asyncio.TimeoutError:
            return await JSONResponse({"success": False, "error": "上传超时，请重试"}, status_code=408)(scope, receive, send)
        iterator = iter(chunks)

        async def replay():
            try:
                return next(iterator)
            except StopIteration:
                return await receive()

        return await self.app(scope, replay, send)
