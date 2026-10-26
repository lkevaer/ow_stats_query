from __future__ import annotations

import os
from pathlib import Path
from typing import Any, Dict, List

import yaml


def _env_override(value: Any, env_key: str, *, allow_empty: bool = False) -> Any:
    env_val = os.getenv(env_key)
    if env_val is None or (not allow_empty and env_val == ""):
        return value
    if isinstance(value, bool):
        return env_val.strip().lower() in {"1", "true", "yes", "on"}
    if isinstance(value, int):
        try:
            return int(env_val)
        except ValueError:
            return value
    return env_val


def _parse_cors_env(value: str) -> List[str]:
    """解析逗号/空格分隔的 origins 列表。"""
    if not value:
        return []
    parts = []
    for item in value.replace(";", ",").split(","):
        s = item.strip()
        if s:
            parts.append(s)
    return parts


def load_config(path: str | os.PathLike[str] | None = None) -> Dict[str, Any]:
    """从 YAML 文件加载配置并支持环境变量覆盖。"""
    base = Path(__file__).resolve().parent
    p = Path(path) if path else base / "config.yaml"
    if path is None and not p.exists():
        p = base / "config.yaml.example"
    if not p.exists():
        raise FileNotFoundError(f"配置文件不存在: {p}")

    with open(p, "r", encoding="utf-8") as f:
        raw = yaml.safe_load(f) or {}

    # 基础类型保证
    raw.setdefault("dashen_accounts", [])
    raw.setdefault("dashen_global", {})
    raw.setdefault("network", {})
    raw.setdefault("analysis", {})
    raw.setdefault("output", {})
    raw.setdefault("features", {})
    raw.setdefault("server", {})
    raw.setdefault("rate_limit", {})
    raw.setdefault("bindings_file", "bindings.json")
    raw["bindings_file"] = _env_override(raw["bindings_file"], "OW_BINDINGS_FILE")

    # —— 环境变量覆盖 ——
    features = raw["features"]
    features["ocr_enabled"] = _env_override(bool(features.get("ocr_enabled", True)), "OW_OCR_ENABLED")

    # server
    server = raw["server"]
    server["host"] = _env_override(server.get("host", "127.0.0.1"), "OW_SERVER_HOST")
    server["port"] = _env_override(int(server.get("port", 8080)), "OW_SERVER_PORT")
    server["api_key"] = _env_override(str(server.get("api_key") or ""), "OW_SERVER_API_KEY")
    cors_env = os.getenv("OW_SERVER_CORS_ORIGINS")
    if cors_env:
        server["cors_origins"] = _parse_cors_env(cors_env)

    # analysis / LLM
    analysis = raw["analysis"]
    analysis["api_key"] = _env_override(analysis.get("api_key", ""), "OW_LLM_API_KEY")
    analysis["base_url"] = _env_override(analysis.get("base_url", ""), "OW_LLM_BASE_URL")
    analysis["model"] = _env_override(analysis.get("model", ""), "OW_LLM_MODEL")

    # 账号（可选：通过环境变量提供单个账号）
    accounts = raw.get("dashen_accounts") or []
    if isinstance(accounts, list):
        token_env = os.getenv("OW_DASHEN_TOKEN")
        role_env = os.getenv("OW_DASHEN_ROLE_ID")
        if token_env or role_env:
            first = dict(accounts[0]) if accounts and isinstance(accounts[0], dict) else {}
            if token_env:
                first["token"] = token_env
            if role_env:
                try:
                    first["role_id"] = int(role_env)
                except ValueError:
                    pass
            if not accounts:
                accounts.append(first)
            else:
                accounts[0] = first
    raw["dashen_accounts"] = accounts

    # rate_limit（可被环境数字覆盖）
    rate = raw["rate_limit"]
    for k_env, k_cfg, default in (
        ("OW_RATE_IP_PER_MIN", "requests_per_ip_per_minute", 60),
        ("OW_RATE_APIKEY_PER_MIN", "requests_per_api_key_per_minute", 300),
        ("OW_RATE_USER_PER_MIN", "requests_per_user_token_per_minute", 30),
        ("OW_RATE_WINDOW_SEC", "window_seconds", 60),
    ):
        env_val = os.getenv(k_env)
        if env_val is not None and env_val.strip():
            try:
                rate[k_cfg] = int(env_val.strip())
            except ValueError:
                pass
        elif k_cfg not in rate or rate[k_cfg] is None:
            rate[k_cfg] = default
    raw["rate_limit"] = rate

    raw["_config_path"] = str(p)
    return raw


def build_injected_config(cfg: Dict[str, Any]) -> Dict[str, Any]:
    """转换为 overstats.config 可接受的扁平 dict 结构。"""
    return {
        "dashen_accounts": list(cfg.get("dashen_accounts") or []),
        "dashen_global": cfg.get("dashen_global") or {},
        "network": cfg.get("network") or {},
        "analysis": cfg.get("analysis") or {},
        "output": cfg.get("output") or {},
        "permissions": cfg.get("permissions") or {},
        "features": cfg.get("features") or {},
    }
