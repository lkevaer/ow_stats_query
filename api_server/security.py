from __future__ import annotations

import ipaddress
import asyncio
import secrets
import time
import uuid
import re
import math

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
        forwarded = request.headers.get("x-forwarded-for")
        if forwarded is None:
            return str(address)
        # Ignore untrusted entries to the left of the first external hop.
        # Malformed trusted-chain entries must never turn into local privilege.
        for value in reversed(forwarded.split(",")):
            address = ipaddress.ip_address(value.strip())
            if not any(address in network for network in trusted):
                return str(address)
        return "unknown"  # A forwarded chain containing only proxies is not a local caller.
    except (ValueError, TypeError, AttributeError):
        return "unknown"


def is_local(request: Request) -> bool:
    if any(header in request.headers for header in ("x-forwarded-for", "x-real-ip", "forwarded")):
        return False
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


def service_identity(request: Request):
    """Only an independently authenticated bot may assert opaque QQ identities."""
    provided = request.headers.get("x-bot-key")
    if provided is None:
        return None
    state = getattr(request.app.state, "ow_app", None)
    expected = str(((getattr(state, "config", {}) or {}).get("security") or {}).get("bot_api_key") or "")
    if not expected or not secrets.compare_digest(provided, expected):
        raise HTTPException(403, "服务凭据无效")
    user = request.headers.get("x-bot-user", "")
    group = request.headers.get("x-bot-group", "")
    if not re.fullmatch(r"[0-9a-f]{64}", user) or not re.fullmatch(r"[0-9a-f]{64}", group):
        raise HTTPException(422, "缺少有效的服务请求身份")
    return user, group


def request_owner(request: Request):
    service = service_identity(request)
    if service:
        return "bot:" + service[0]
    identity = getattr(request.state, "browser_id", "") or request.cookies.get("ow_browser_id", "")
    return "web:" + client_ip(request) + ":" + identity


def admit_rating_query(request: Request, *, full_match: bool = False):
    """Charge every validated web submission, including cached/shared reports."""
    if service_identity(request):
        return
    state = getattr(request.app.state, "ow_app", None) or request.app.state
    cfg = (getattr(state, "config", {}) or {}).get("rate_limit") or {}
    if cfg.get("enable_heavy_limits") is False:
        return
    limits = getattr(state, "rating_request_limits", None)
    if limits is None:
        limits = state.rating_request_limits = {
            "ordinary": SlidingWindowLimiter(int(cfg.get("rating_queries_per_window", 2)),
                                               float(cfg.get("rating_window_seconds", 10))),
            "full_match": SlidingWindowLimiter(1, float(cfg.get("full_match_cooldown_seconds", 60))),
        }
    policy = "full_match" if full_match else "ordinary"
    owner = request_owner(request)
    allowed, retry = limits[policy].try_with_retry_after(owner)
    if not allowed:
        from .metrics import REGISTRY
        REGISTRY.counter("rate_limit_throttled_total", {"rule": "rating_" + policy, "route": "/api/batch-rating"}).inc()
        seconds = max(1, math.ceil(retry))
        rule = "查询当局名单每分钟只能查询一次" if full_match else "普通查询每10秒最多查询两次"
        raise HTTPException(429, f"{rule}，请在{seconds}秒后重试", headers={"Retry-After": str(seconds)})
    if full_match:
        return lambda: limits[policy].restart_window(owner)


async def run_ocr(request, operation):
    state = getattr(request.app.state, "ow_app", None) or request.app.state
    owners = getattr(state, "active_ocr", None)
    if owners is None:
        owners = state.active_ocr = set()
    owner = request_owner(request)
    if owner in owners:
        raise HTTPException(429, "上一张截图仍在识别，请等待完成", headers={"Retry-After": "3"})
    owners.add(owner)
    try:
        admit_heavy(request, "ocr")
        return await operation()
    finally:
        owners.discard(owner)


def request_limits(state):
    limits = getattr(state, "request_limits", None)
    if limits is None:
        cfg = (getattr(state, "config", {}) or {}).get("rate_limit") or {}
        limits = {"query": SlidingWindowLimiter(int(cfg.get("heavy_queries_per_ip_per_minute", 3)), 60),
                  "browser": SlidingWindowLimiter(1, float(cfg.get("heavy_browser_cooldown_seconds", 30))),
                  "ocr": SlidingWindowLimiter(int(cfg.get("ocr_per_ip_per_minute", 6)), 60),
                  "bot_query": SlidingWindowLimiter(30, 60),
                  "bot_user": SlidingWindowLimiter(1, 30),
                  "bot_group": SlidingWindowLimiter(6, 60),
                  "bot_ocr": SlidingWindowLimiter(6, 60),
                  "progress": SlidingWindowLimiter(240, 60)}
        state.request_limits = limits
    return limits


def admit_heavy(request: Request, kind="query"):
    state = getattr(request.app.state, "ow_app", None) or request.app.state
    if ((getattr(state, "config", {}) or {}).get("rate_limit") or {}).get("enable_heavy_limits") is False:
        return
    limits = request_limits(state)
    service = service_identity(request)
    if service:
        user, group = service
        checks = [("progress", "bot:" + user)] if kind == "progress" else (
            [("bot_ocr", user)] if kind == "ocr" else
            [("bot_user", user), ("bot_group", group), ("bot_query", "all")])
    else:
        ip = client_ip(request)
        checks = [(kind, ip)]
        identity = getattr(request.state, "browser_id", "") or request.cookies.get("ow_browser_id", "")
        if kind == "query" and identity:
            checks.append(("browser", ip + ":" + identity))
    # No await between checking and consuming: all policies belong to this
    # single event loop. Rejected retries must not drain other users' quotas.
    for policy, key in checks:
        ok, retry = limits[policy].try_with_retry_after(key, consume=False)
        if not ok:
            from .metrics import REGISTRY
            REGISTRY.counter("rate_limit_throttled_total", {"rule": "heavy_" + kind, "route": kind}).inc()
            seconds = max(1, int(retry) + 1)
            raise HTTPException(429, f"操作过于频繁，请在{seconds}秒后重试", headers={"Retry-After": str(seconds)})
    for policy, key in checks:
        limits[policy].try_with_retry_after(key)


class RequestBodyLimit:
    """Bound JSON/base64 bodies before parsing, including chunked requests."""
    def __init__(self, app, maximum=12 * 1024 * 1024, capacity=4):
        self.app, self.maximum = app, maximum
        self.capacity, self.active = capacity, 0
        from .feature_flags import rating_no_queue
        self.rating_no_queue = rating_no_queue()

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http" or scope["method"] not in {"POST", "PUT", "PATCH"}:
            return await self.app(scope, receive, send)
        from fastapi.responses import JSONResponse
        lengths = [value for key, value in scope.get("headers", []) if key.lower() == b"content-length"]
        try:
            if lengths and (len(lengths) != 1 or int(lengths[0]) < 0):
                raise ValueError("Invalid Content-Length")
            if lengths and int(lengths[0]) > self.maximum:
                return await JSONResponse({"success": False, "error": "请求体过大"}, status_code=413)(scope, receive, send)
        except ValueError:
            return await JSONResponse({"success": False, "error": "无效的请求长度"}, status_code=400)(scope, receive, send)
        if self.rating_no_queue and scope.get("path") in {"/api/batch-rating/competitive", "/api/batch-rating/leisure"}:
            return await self._receive_body(scope, receive, send)
        if self.active >= self.capacity:
            return await JSONResponse({"success": False, "error": "服务繁忙，请稍后重试"}, status_code=503,
                                      headers={"Retry-After": "5"})(scope, receive, send)
        self.active += 1
        try:
            return await self._receive_body(scope, receive, send)
        finally:
            self.active -= 1

    async def _receive_body(self, scope, receive, send):
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
