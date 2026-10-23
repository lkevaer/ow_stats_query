from __future__ import annotations

import asyncio
import json
import logging
import time
import uuid
from typing import Any, Dict, Optional

from fastapi import APIRouter, Body, HTTPException, Request

from ..app_context import AppState
from ..query_audit import record_successful_ids
from ..db.task_db import (
    cleanup_expired_tasks,
    create_task as db_create_task,
    delete_task as db_delete_task,
    get_task as db_get_task,
    init_task_db,
    list_tasks as db_list_tasks,
    update_task_done,
    update_task_error,
    update_task_progress,
    update_task_started,
)
from .utils import resolve_target, ResolvedTarget, require_account_ready
from .stream import _ENDPOINT_DISPATCH, _ENDPOINTS_NEED_ACCOUNT, _STAGE_PCT

logger = logging.getLogger("ow_api_server.task")
router = APIRouter(prefix="/api/task", tags=["task"])

_TASK_TTL_SECONDS = 300

init_task_db()


async def _update_progress(task_id: str, stage: str):
    pct = _STAGE_PCT.get(stage, 50.0)
    update_task_progress(task_id, stage, pct)


async def _run_task(task_id: str, endpoint: str, params: Dict[str, Any], state: AppState):
    try:
        update_task_started(task_id)
        await _update_progress(task_id, "启动查询")

        handler = _ENDPOINT_DISPATCH.get(endpoint)
        if handler is None:
            raise HTTPException(status_code=400, detail=f"未知的 endpoint: {endpoint}")

        need_account = endpoint in _ENDPOINTS_NEED_ACCOUNT

        async def _send(chunk: bytes) -> None:
            try:
                payload = chunk.decode("utf-8")
                if "progress" in payload:
                    data_start = payload.find("{")
                    data_end = payload.rfind("}") + 1
                    if data_start >= 0 and data_end > data_start:
                        data = json.loads(payload[data_start:data_end])
                        update_task_progress(task_id, data.get("stage", ""), data.get("pct", 50.0))
            except Exception:
                pass

        resolved: ResolvedTarget
        if need_account:
            require_account_ready(state)
            resolved = resolve_target(token=str(params.get("token") or ""), battletag=str(params.get("battletag") or ""), state=state)
        else:
            resolved = ResolvedTarget(token="", battletag="", bnet_id="", customer_token="", display_name="", source="inline")

        result = await handler(params, state, resolved, _send)
        if need_account and isinstance(result, dict) and result.get("success") is True:
            await record_successful_ids([resolved.battletag, params.get("player2") if endpoint.startswith("/api/sameplay") else None])
        update_task_done(task_id, result)
        logger.info("Task %s completed successfully", task_id)

    except HTTPException as exc:
        update_task_error(task_id, str(exc.detail), exc.status_code)
        logger.error("Task %s failed with HTTP error: %s", task_id, exc.detail)

    except Exception as exc:
        update_task_error(task_id, f"查询失败：{exc}", 500)
        logger.exception("Task %s failed with exception", task_id)

    finally:
        cleanup_expired_tasks(_TASK_TTL_SECONDS)


@router.post("/create")
async def create_task(request: Request, body: Dict[str, Any] = Body(...)):
    cleanup_expired_tasks(_TASK_TTL_SECONDS)

    endpoint = str(body.get("endpoint") or "").strip()
    params_raw = body.get("params") or {}
    if not isinstance(params_raw, dict):
        params_raw = {}

    if not endpoint:
        raise HTTPException(status_code=400, detail="请提供 endpoint")

    if endpoint not in _ENDPOINT_DISPATCH:
        raise HTTPException(status_code=400, detail=f"不支持的 endpoint: {endpoint}")

    params: Dict[str, Any] = {}
    for k, v in params_raw.items():
        if v is None:
            continue
        params[str(k)] = v

    state: Optional[AppState] = getattr(request.app.state, "ow_app", None)
    if state is None:
        raise HTTPException(status_code=503, detail="应用尚未初始化完成")

    if endpoint in _ENDPOINTS_NEED_ACCOUNT:
        require_account_ready(state)

    task_id = str(uuid.uuid4())[:8]
    db_create_task(task_id, endpoint, params)

    asyncio.create_task(_run_task(task_id, endpoint, params, state))

    return {
        "success": True,
        "data": {
            "task_id": task_id,
            "status": "pending",
            "message": "任务已创建，请轮询 /api/task/{task_id} 获取结果",
        },
    }


@router.get("/{task_id}")
async def get_task(task_id: str):
    cleanup_expired_tasks(_TASK_TTL_SECONDS)

    task = db_get_task(task_id)
    if task is None:
        raise HTTPException(status_code=404, detail="任务不存在或已过期")

    response = {
        "success": True,
        "data": {
            "task_id": task["task_id"],
            "status": task["status"],
            "progress_pct": round(task["progress_pct"], 1),
            "stage": task["stage"],
        },
    }

    if task["status"] == "done":
        response["data"]["result"] = task["result"]
        response["data"]["completed_at"] = task["completed_at"]
    elif task["status"] == "error":
        response["success"] = False
        response["data"]["error"] = task["error"]
        response["data"]["error_code"] = task["error_code"]

    return response


@router.get("/list")
async def list_tasks():
    cleanup_expired_tasks(_TASK_TTL_SECONDS)
    tasks = db_list_tasks()
    return {
        "success": True,
        "data": {
            "count": len(tasks),
            "tasks": [
                {
                    "task_id": t["task_id"],
                    "status": t["status"],
                    "progress_pct": round(t["progress_pct"], 1),
                    "stage": t["stage"],
                    "created_at": t["created_at"],
                }
                for t in tasks
            ],
        },
    }


@router.delete("/{task_id}")
async def delete_task(task_id: str):
    if db_delete_task(task_id):
        return {"success": True, "message": "任务已删除"}
    raise HTTPException(status_code=404, detail="任务不存在")
