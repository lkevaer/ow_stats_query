from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Response
from pydantic import BaseModel, Field

from ..batch_rating.ocr import recognize_image
from ..batch_rating.scoring import scoring_rules
from ..batch_rating.service import BatchRatingService, QueryFailure
from .utils import get_app_state, require_account_ready

router = APIRouter(prefix="/api/batch-rating", tags=["batch-rating"])


class RatingRequest(BaseModel):
    battletags: list[Annotated[str, Field(max_length=64)]] = Field(default_factory=list, max_length=32)
    image_base64: str | None = Field(None, max_length=11_185_100, description="截图 Base64，可包含 data:image/... 前缀；与 battletags 二选一")
    wait: bool = Field(False, description="默认异步返回 task_id；设 true 等待完整结果")


class ImageRequest(BaseModel):
    image_base64: str = Field(..., max_length=11_185_100)


def service(state=Depends(get_app_state)) -> BatchRatingService:
    if state.batch_rating is None:
        raise HTTPException(503, "批量评分尚未初始化")
    return state.batch_rating


async def _query(mode, body, response, state):
    require_account_ready(state)
    worker = state.batch_rating
    if worker is None:
        raise HTTPException(503, "批量评分尚未初始化")
    try:
        prepared = await worker.prepare(body.battletags, body.image_base64)
        if body.wait:
            return {"success": True, "data": await worker.query(mode, prepared)}
        response.status_code = 202
        return {"success": True, "data": worker.start(mode, prepared)}
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc
    except QueryFailure as exc:
        raise HTTPException(503, str(exc)) from exc
    except RuntimeError as exc:
        raise HTTPException(503, str(exc)) from exc


@router.post("/leisure", summary="图片/多人 BattleTag：最近12场休闲对局评分排序")
async def leisure(body: RatingRequest, response: Response, state=Depends(get_app_state)):
    return await _query("leisure", body, response, state)


@router.post("/competitive", summary="图片/多人 BattleTag：最近12场竞技对局评分排序")
async def competitive(body: RatingRequest, response: Response, state=Depends(get_app_state)):
    return await _query("competitive", body, response, state)


@router.post("/recognize", summary="本机中文OCR识别截图中的完整BattleTag")
async def recognize(body: ImageRequest):
    try:
        return {"success": True, "data": await recognize_image(body.image_base64)}
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc
    except RuntimeError as exc:
        raise HTTPException(503, str(exc)) from exc


@router.get("/rules")
async def rules():
    return {"success": True, "data": scoring_rules()}


@router.get("/tasks/{task_id}")
async def task_status(task_id: str, worker=Depends(service)):
    job = worker.get_job(task_id)
    if job is None:
        raise HTTPException(404, "任务不存在或结果已过期；服务重启后需重新查询")
    return {"success": True, "data": job}
