from __future__ import annotations

import logging
import secrets
import sys
import time
from pathlib import Path
from typing import Any, Optional

from fastapi import FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse, PlainTextResponse, RedirectResponse, Response
from fastapi.staticfiles import StaticFiles

# 让 overstats 能被 import
BASE_DIR = Path(__file__).resolve().parent.parent
if str(BASE_DIR) not in sys.path:
    sys.path.insert(0, str(BASE_DIR))

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger("ow_api_server")

from .app_context import AppState, initialize_modules, shutdown_modules  # noqa: E402
from .config_loader import load_config  # noqa: E402
from .metrics import REGISTRY, render_metrics_text  # noqa: E402
from .ratelimit import LIMITERS  # noqa: E402
from .response_utils import error_response  # noqa: E402
from .query_audit import record_successful_ids  # noqa: E402
from .frontend import rating_page  # noqa: E402
from .feature_flags import is_ocr_enabled  # noqa: E402
from .routers import (  # noqa: E402
    bind_router,
    hero_router,
    patch_router,
    player_router,
    sameplay_router,
    search_router,
    shop_router,
    summary_router,
)

# ---------------------------------------------------------------------------
# 配置加载（在 app 创建之前完成，便于中间件配置使用）
# ---------------------------------------------------------------------------
_config = load_config()
_server_cfg = _config.get("server") or {}
_security_cfg = _config.get("security") or {}
_rate_cfg = (_config.get("rate_limit") or {}) if isinstance(_config, dict) else {}

# ---------------------------------------------------------------------------
# FastAPI 应用
# ---------------------------------------------------------------------------
app = FastAPI(
    title="守望先锋数据查询 Web API",
    version="2.0.0",
    docs_url="/docs" if _server_cfg.get("enable_docs", True) else None,
    redoc_url="/redoc" if _server_cfg.get("enable_docs", True) else None,
)

# ---------------------------------------------------------------------------
# CORS（默认严格模式；通过配置 server.cors_origins 指定允许来源）
# ---------------------------------------------------------------------------
_cors_origins_raw = _server_cfg.get("cors_origins") or []
_cors_origins = [o for o in (_cors_origins_raw if isinstance(_cors_origins_raw, list) else []) if o]
if not _cors_origins:
    _cors_origins = ["https://localhost", "https://127.0.0.1"]
    logger.info("security: cors_origins 未显式配置，默认仅允许本地 HTTPS 来源")
app.add_middleware(
    CORSMiddleware,
    allow_origins=_cors_origins,
    allow_credentials=True,
    allow_methods=["GET", "POST", "OPTIONS"],
    allow_headers=["X-API-Key", "Content-Type", "Authorization"],
    max_age=3600,
)

# ---------------------------------------------------------------------------
# X-API-Key 访问控制中间件（白名单路径公开，其余一律鉴权）
# ---------------------------------------------------------------------------
ALLOW_WITHOUT_KEY_PREFIXES = (
    "/",
    "/batch-rating",
    "/hero-rates",
    "/static/",
    "/health",
    "/healthz",
    "/ready",
    "/docs",
    "/redoc",
    "/openapi.json",
    "/favicon.ico",
    "/metrics",
)


def _needs_api_key(path: str, api_key_cfg: str) -> bool:
    if not api_key_cfg:
        return False
    if path in ALLOW_WITHOUT_KEY_PREFIXES or path.startswith("/static/") or path.startswith("/docs"):
        return False
    return True


# ---------------------------------------------------------------------------
# API 路径 → 标签映射（用于 metrics 分组；避免 high-cardinality 标签）
# ---------------------------------------------------------------------------
_ROUTE_LABEL_CACHE: dict[str, str] = {}


def _route_label(path: str) -> str:
    cached = _ROUTE_LABEL_CACHE.get(path)
    if cached is not None:
        return cached
    if path == "/" or path.startswith("/static/") or path == "/favicon.ico":
        result = "static"
    elif path in ("/health", "/healthz", "/ready"):
        result = "health"
    elif path == "/metrics":
        result = "metrics"
    elif path.startswith("/openapi"):
        result = "openapi"
    elif path.startswith("/docs") or path.startswith("/redoc"):
        result = "docs"
    elif path.startswith("/api/profile"):
        result = "/api/profile"
    elif path.startswith("/api/matches"):
        result = "/api/matches"
    elif path.startswith("/api/match-detail"):
        result = "/api/match-detail"
    elif path.startswith("/api/analyze"):
        result = "/api/analyze"
    elif path.startswith("/api/rank-history"):
        result = "/api/rank-history"
    elif path.startswith("/api/quick-strength"):
        result = "/api/quick-strength"
    elif path.startswith("/api/competitive-strength"):
        result = "/api/competitive-strength"
    elif path.startswith("/api/summary"):
        result = "/api/summary"
    elif path.startswith("/api/sameplay"):
        result = "/api/sameplay"
    elif path.startswith("/api/hero-pick-rate"):
        result = "/api/hero-pick-rate"
    elif path.startswith("/api/hero-perk"):
        result = "/api/hero-perk"
    elif path.startswith("/api/hero-wiki"):
        result = "/api/hero-wiki"
    elif path.startswith("/api/shop"):
        result = "/api/shop"
    elif path.startswith("/api/patch-notes"):
        result = "/api/patch-notes"
    elif path.startswith("/api/search-player"):
        result = "/api/search-player"
    elif path.startswith("/api/bind") or path.startswith("/api/unbind") or path.startswith("/api/my-bind"):
        result = "/api/bind"
    elif path.startswith("/api/token/"):
        result = "/api/token"
    else:
        result = "unknown"
    _ROUTE_LABEL_CACHE[path] = result
    return result


def _client_ip(request: Request) -> str:
    xff = request.headers.get("x-forwarded-for") or ""
    if xff:
        first = xff.split(",", 1)[0].strip()
        if first:
            return first
    addr = request.client
    return addr.host if addr else "unknown"


# ---------------------------------------------------------------------------
# 主中间件：速率限制 → API Key → 请求计数/耗时 → 错误统计
# ---------------------------------------------------------------------------
@app.middleware("http")
async def _security_and_metrics_middleware(request: Request, call_next):
    start = time.perf_counter()
    state = getattr(request.app.state, "ow_app", None)
    api_key_cfg = ""
    if state is not None:
        s_cfg = (state.config.get("server") or {}) if isinstance(state.config, dict) else {}
        api_key_cfg = str(s_cfg.get("api_key", "") or "").strip()

    path = request.url.path
    if len(path) > 1 and path.endswith("/"):
        path = path.rstrip("/") or "/"
    route_label = _route_label(path)

    # ------ 速率限制（Nginx 层为首选；本层作为兜底） ------
    rate_bypass = False
    try:
        rl_config = (state.config.get("rate_limit") or {}) if state is not None and isinstance(state.config, dict) else {}
        ip_max = int(rl_config.get("requests_per_ip_per_minute", 0) or 0)
        api_key_max = int(rl_config.get("requests_per_api_key_per_minute", 0) or 0)
        user_token_max = int(rl_config.get("requests_per_user_token_per_minute", 0) or 0)
        window = float(rl_config.get("window_seconds", 60) or 60)
        if ip_max or api_key_max or user_token_max:
            LIMITERS.configure(ip_max=ip_max or None, api_key_max=api_key_max or None,
                               user_token_max=user_token_max or None, window_seconds=window)
        else:
            rate_bypass = True
    except Exception:
        rate_bypass = True

    if not rate_bypass and route_label not in ("static", "health", "metrics", "docs", "openapi"):
        provided_key = request.headers.get("x-api-key") or ""
        ip = _client_ip(request)
        # user_token 从查询参数或 headers 取
        user_token = ""
        try:
            q = request.query_params
            user_token = (q.get("token") or "").strip()
        except Exception:
            pass
        ok, rule, retry_after = LIMITERS.check(ip=ip, api_key=provided_key.strip(), user_token=user_token)
        if not ok:
            REGISTRY.counter(
                "http_requests_total", {"method": request.method, "route": route_label, "status": "429"}
            ).inc()
            REGISTRY.counter("rate_limit_throttled_total", {"rule": rule, "route": route_label}).inc()
            return JSONResponse(
                status_code=429,
                content={
                    "success": False,
                    "error": "请求过于频繁，请稍后再试（{0}）".format(rule),
                    "status_code": 429,
                    "details": {"retry_after_seconds": int(retry_after) + 1},
                },
                headers={"Retry-After": str(int(retry_after) + 1)},
            )

    # ------ API Key 检查 ------
    if _needs_api_key(path, api_key_cfg):
        provided = (request.headers.get("x-api-key") or "").strip()
        if not provided or not secrets.compare_digest(provided, api_key_cfg):
            logger.warning(
                "api_key_mismatch path=%s client=%s",
                request.url.path,
                _client_ip(request),
            )
            REGISTRY.counter(
                "http_requests_total", {"method": request.method, "route": route_label, "status": "401"}
            ).inc()
            REGISTRY.counter("http_errors_total", {"method": request.method, "route": route_label, "status": "401"}).inc()
            return JSONResponse(
                status_code=401,
                content={
                    "success": False,
                    "error": "缺少或无效的 X-API-Key",
                    "status_code": 401,
                    "details": {},
                },
            )

    # ------ 活跃请求 + 执行 ------
    REGISTRY.gauge("http_requests_in_flight", {"method": request.method}).inc()
    try:
        response = await call_next(request)
    except Exception as exc:
        # 通用兜底异常
        status = 500
        REGISTRY.counter(
            "http_requests_total", {"method": request.method, "route": route_label, "status": str(status)}
        ).inc()
        REGISTRY.counter(
            "http_errors_total", {"method": request.method, "route": route_label, "status": str(status)}
        ).inc()
        REGISTRY.histogram("http_request_duration_seconds", {"route": route_label}).observe(
            time.perf_counter() - start
        )
        logger.exception("请求处理异常: %s", exc)
        return JSONResponse(
            status_code=500,
            content={
                "success": False,
                "error": "服务内部错误：{0}".format(type(exc).__name__),
                "status_code": 500,
                "details": {"message": str(exc)},
            },
        )
    finally:
        REGISTRY.gauge("http_requests_in_flight", {"method": request.method}).dec()

    # ------ 响应后记录 ------
    REGISTRY.counter(
        "http_requests_total",
        {"method": request.method, "route": route_label, "status": str(response.status_code)},
    ).inc()
    if response.status_code >= 400:
        REGISTRY.counter(
            "http_errors_total",
            {"method": request.method, "route": route_label, "status": str(response.status_code)},
        ).inc()
    REGISTRY.histogram("http_request_duration_seconds", {"route": route_label}).observe(
        time.perf_counter() - start
    )
    return response


# ---------------------------------------------------------------------------
# 生命周期（startup / shutdown）
# ---------------------------------------------------------------------------
@app.on_event("startup")
async def on_startup():
    cfg = _config
    state = await initialize_modules(cfg)
    app.state.ow_app = state

    server_cfg = cfg.get("server") or {}
    api_key = str(server_cfg.get("api_key", "") or "").strip()
    if api_key:
        logger.info(
            "FastAPI 服务启动完成；已启用 X-API-Key 访问控制；监听 %s:%s",
            server_cfg.get("host", "127.0.0.1"),
            server_cfg.get("port", 8080),
        )
    else:
        logger.warning(
            "FastAPI 服务启动完成；未配置 server.api_key，将完全不校验 API Key。"
            "请立即在 api_server/config.yaml 中填入一个随机字符串作为 api_key，并重启服务。"
        )

    rl_cfg = (cfg.get("rate_limit") or {}) if isinstance(cfg, dict) else {}
    ip_max = int(rl_cfg.get("requests_per_ip_per_minute", 0) or 0)
    if not ip_max:
        logger.warning("未配置 rate_limit.requests_per_ip_per_minute；应用层未启用限流（请依赖 Nginx 层限流）。")


@app.on_event("shutdown")
async def on_shutdown():
    state = getattr(app.state, "ow_app", None)
    if state is not None:
        await shutdown_modules(state)
        logger.info("FastAPI 服务已安全关闭")


# ---------------------------------------------------------------------------
# 基础端点
# ---------------------------------------------------------------------------
STATIC_DIR = Path(__file__).resolve().parent / "static"


@app.get("/static/batch_rating.html", include_in_schema=False)
async def static_rating_entry(request: Request):
    return RedirectResponse("/" + ("?" + request.url.query if request.url.query else ""))


app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")


@app.get("/", include_in_schema=False)
async def root():
    return rating_page(ocr_enabled=is_ocr_enabled(_config))


@app.get("/hero-rates", include_in_schema=False)
async def hero_rates_page():
    return rating_page(page="hero_rates.html")


@app.get("/health", include_in_schema=False)
async def health():
    return {"status": "ok", "api_key_required": bool(_server_cfg.get("api_key"))}


@app.get("/healthz", include_in_schema=False)
async def healthz():
    return {"status": "ok"}


@app.get("/ready", include_in_schema=False)
async def ready(request: Request):
    state = getattr(request.app.state, "ow_app", None)
    initialized = state is not None and getattr(state, "initialized", False)
    status_code = 200 if initialized else 503
    return JSONResponse(
        status_code=status_code,
        content={
            "ready": initialized,
            "modules": {
                "profile": getattr(state, "profile", None) is not None if state else False,
                "match": getattr(state, "match", None) is not None if state else False,
                "rank_history": getattr(state, "rank_history", None) is not None if state else False,
                "summary": getattr(state, "summary", None) is not None if state else False,
                "hero_leaderboard": getattr(state, "hero_leaderboard_sync", None) is not None if state else False,
                "shop": getattr(state, "shop", None) is not None if state else False,
                "patch_notes": getattr(state, "patch_notes", None) is not None if state else False,
                "sameplay": getattr(state, "sameplay", None) is not None if state else False,
                "hero_perk": getattr(state, "hero_perk", None) is not None if state else False,
                "hero_wiki": getattr(state, "hero_wiki", None) is not None if state else False,
                "llm": getattr(state, "llm_client", None) is not None if state else False,
            } if state else {},
        },
    )


@app.get("/metrics", include_in_schema=False, response_class=PlainTextResponse)
async def metrics_endpoint(request: Request):
    text = render_metrics_text()
    return Response(content=text, media_type="text/plain; version=0.0.4; charset=utf-8")


# ---------------------------------------------------------------------------
# 异常标准化
# ---------------------------------------------------------------------------
@app.exception_handler(HTTPException)
async def _ow_http_exception_handler(request: Request, exc: HTTPException):
    return JSONResponse(
        status_code=exc.status_code,
        content={
            "success": False,
            "error": str(exc.detail) if exc.detail is not None else "请求失败",
            "status_code": exc.status_code,
            "details": getattr(exc, "details", {}),
        },
    )


@app.exception_handler(Exception)
async def _ow_generic_exception_handler(request: Request, exc: Exception):
    logger.exception("请求处理异常: %s", exc)
    return JSONResponse(
        status_code=500,
        content={
            "success": False,
            "error": "服务内部错误：{0}".format(type(exc).__name__),
            "status_code": 500,
            "details": {"message": str(exc)},
        },
    )


# ---------------------------------------------------------------------------
# 业务路由
# ---------------------------------------------------------------------------
app.include_router(bind_router)
app.include_router(player_router)
app.include_router(sameplay_router)
app.include_router(summary_router)
app.include_router(hero_router)
app.include_router(shop_router)
app.include_router(patch_router)
app.include_router(search_router)
from .routers.stream import router as stream_router  # noqa: E402
app.include_router(stream_router)
from .routers.task import router as task_router  # noqa: E402
app.include_router(task_router)
from .routers.batch_rating import router as batch_rating_router  # noqa: E402
app.include_router(batch_rating_router)
from .routers.hero_rates import router as hero_rates_router  # noqa: E402
app.include_router(hero_rates_router)


@app.get("/batch-rating", include_in_schema=False)
async def batch_rating_page(request: Request):
    target = "/" + ("?" + request.url.query if request.url.query else "")
    return RedirectResponse(target, status_code=307)


@app.middleware("http")
async def record_completed_player_query(request: Request, call_next):
    response = await call_next(request)
    tag = getattr(request.state, "query_battletag", None)
    if tag and 200 <= response.status_code < 300:
        tags = [tag]
        if request.url.path.rstrip("/").startswith("/api/sameplay"):
            tags.append(request.query_params.get("player2"))
        await record_successful_ids(tags)
    return response

__all__ = ["app"]
