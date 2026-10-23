from __future__ import annotations

import os
from pathlib import Path


APP_NAME = "ow_api"


def _resolve_app_root() -> Path:
    if root := os.environ.get("OW_API_ROOT"):
        return Path(root).resolve()
    return Path.cwd().resolve()


def get_plugin_data_dir() -> Path:
    data_dir = _resolve_app_root() / "data" / APP_NAME
    data_dir.mkdir(parents=True, exist_ok=True)
    return data_dir


def get_overstats_data_dir() -> Path:
    overstats_dir = get_plugin_data_dir() / "overstats"
    overstats_dir.mkdir(parents=True, exist_ok=True)
    return overstats_dir


def ensure_dir(path: Path) -> Path:
    path.mkdir(parents=True, exist_ok=True)
    return path
