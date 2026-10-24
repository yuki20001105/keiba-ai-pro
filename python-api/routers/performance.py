"""Local, personal paper/confirmed-purchase records; never place a bet."""
from __future__ import annotations

import asyncio
from datetime import datetime
import logging
from typing import Annotated, Literal

from fastapi import APIRouter, Depends, HTTPException, Query, Response
from pydantic import BaseModel, Field, StrictBool, StrictInt, field_validator

from app_config import APP_ENV, CONFIG_PATH, ULTIMATE_DB  # type: ignore
from betting.performance import list_performance, record_selection, save_snapshot  # type: ignore
from deps.auth import get_current_user  # type: ignore

router = APIRouter()
logger = logging.getLogger(__name__)


def _ledger_path():
    return CONFIG_PATH.parent / "data" / "betting_performance.db"


def _require_local():
    if APP_ENV not in {"development", "local", "test"}:
        raise HTTPException(status_code=403, detail="成績記録はローカル環境専用です")


class SelectedTicket(BaseModel):
    model_config = {"extra": "forbid"}
    bet_type: Literal["単勝", "馬連", "馬単", "ワイド", "三連複", "三連単"]
    combination: str = Field(min_length=1, max_length=30)
    horse_numbers: list[Annotated[StrictInt, Field(ge=1, le=99)]] = Field(min_length=1, max_length=3)
    stake: Annotated[StrictInt, Field(ge=100, le=10_000_000, multiple_of=100)]


class RecordSelectionRequest(BaseModel):
    model_config = {"extra": "forbid"}
    snapshot_id: str = Field(min_length=1, max_length=100)
    tickets: list[SelectedTicket] = Field(max_length=5)
    mode: Literal["paper", "actual"] = "paper"
    confirmed: StrictBool = False

    @field_validator("snapshot_id")
    @classmethod
    def _snapshot_id(cls, value):
        if not value.strip():
            raise ValueError("予測記録が必要です")
        return value


def _filter_date(value: str | None) -> str | None:
    if value is None:
        return None
    if len(value) != 8 or not value.isascii() or not value.isdigit():
        raise HTTPException(status_code=400, detail="日付はYYYYMMDDで指定してください")
    try:
        return datetime.strptime(value, "%Y%m%d").date().isoformat()
    except ValueError as exc:
        raise HTTPException(status_code=400, detail="存在する日付を指定してください") from exc


async def attach_performance_snapshot(response, owner: str | None, race_id: str):
    """Attach outside the shared inference cache, so IDs never cross users."""
    data = response.model_dump()
    data.pop("performance_snapshot_id", None)
    data.pop("performance_recording_error", None)
    if APP_ENV not in {"development", "local", "test"}:
        return type(response)(**data)
    if not owner:
        data["performance_recording_error"] = "成績記録にはログインが必要です"
        return type(response)(**data)
    data["race_info"] = {**data["race_info"], "race_id": race_id}
    try:
        data["performance_snapshot_id"] = await asyncio.to_thread(save_snapshot, _ledger_path(), owner, data)
    except Exception:
        logger.exception("Personal recommendation snapshot could not be saved")
        data["performance_recording_error"] = "予測は完了しましたが、成績記録を準備できませんでした"
    return type(response)(**data)


@router.post("/api/performance/records")
async def save_record(body: RecordSelectionRequest, response: Response, user: dict = Depends(get_current_user)):
    _require_local()
    response.headers["Cache-Control"] = "no-store"
    if body.mode == "actual" and not body.confirmed:
        raise HTTPException(status_code=400, detail="購入済みであることを確認してください（購入操作は行いません）")
    try:
        record = await asyncio.to_thread(
            record_selection, _ledger_path(), user["user_id"], body.snapshot_id,
            [ticket.model_dump() for ticket in body.tickets], body.mode,
        )
    except PermissionError as exc:
        raise HTTPException(status_code=404, detail="予測記録が見つかりません") from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except Exception as exc:
        logger.exception("Personal performance record could not be saved")
        raise HTTPException(status_code=503, detail="成績を記録できませんでした。再試行してください") from exc
    return {"record": record, "id": record["id"], "existing": bool(record.get("existing"))}


@router.get("/api/performance")
async def get_performance(
    response: Response, user: dict = Depends(get_current_user),
    mode: Literal["paper", "actual"] = "paper", limit: int = Query(100, ge=1, le=200),
    date_from: str | None = None, date_to: str | None = None,
):
    _require_local()
    response.headers["Cache-Control"] = "no-store"
    start, end = _filter_date(date_from), _filter_date(date_to)
    if start and end and start > end:
        raise HTTPException(status_code=400, detail="開始日は終了日以前にしてください")
    try:
        return await asyncio.to_thread(
            list_performance, _ledger_path(), ULTIMATE_DB, user["user_id"], mode,
            limit=limit, date_from=start, date_to=end,
        )
    except Exception as exc:
        logger.exception("Personal performance records could not be read")
        raise HTTPException(status_code=503, detail="成績を読み込めませんでした。再試行してください") from exc
