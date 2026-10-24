from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException

from ..hero_rates import CATALOG, DEFAULT_FILTERS
from .utils import get_app_state

router = APIRouter(prefix="/api/hero-rates", tags=["hero-rates"])


@router.get("/filters")
async def filters():
    return {
        "success": True,
        "data": {"filters": CATALOG["filters"], "defaults": DEFAULT_FILTERS},
    }


@router.get("")
async def rates(
    role: str = "all",
    rq: str = "2",
    rank: str = "master",
    map: str = "all",
    input: str = "pc",
    region: str = "korea",
    state=Depends(get_app_state),
):
    if state.hero_rates is None:
        raise HTTPException(503, "英雄胜率缓存尚未初始化")
    try:
        data = await state.hero_rates.get(
            {
                "role": role,
                "rq": rq,
                "rank": rank,
                "map": map,
                "input": input,
                "region": region,
            }
        )
        return {"success": True, "data": data}
    except ValueError as error:
        raise HTTPException(422, str(error)) from error
    except RuntimeError as error:
        raise HTTPException(503, str(error)) from error
