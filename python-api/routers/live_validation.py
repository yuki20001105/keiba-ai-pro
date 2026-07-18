"""Admin-only endpoint for bounded targeted-refetch live validation."""

from __future__ import annotations

import logging

from fastapi import APIRouter, Depends, HTTPException

from deps.auth import require_admin  # type: ignore
from services.live_validation import (  # type: ignore
    LiveValidationRequest,
    LiveValidationResponse,
    LiveValidationServiceError,
    live_validation_coordinator,
    live_validation_service,
)

logger = logging.getLogger(__name__)
router = APIRouter()


@router.post(
    "/api/scrape/live-validation",
    response_model=LiveValidationResponse,
    response_model_exclude_none=False,
)
async def run_live_validation(
    request: LiveValidationRequest,
    admin: dict = Depends(require_admin),
) -> LiveValidationResponse:
    user_id = str(admin.get("user_id") or "")
    if not user_id:
        raise HTTPException(status_code=500, detail="live validation authorization context is invalid")

    acquired = False
    try:
        live_validation_coordinator.acquire(user_id)
        acquired = True
        return await live_validation_service.run(request)
    except LiveValidationServiceError as exc:
        raise HTTPException(
            status_code=exc.status_code,
            detail=exc.detail,
            headers=exc.headers,
        ) from exc
    except Exception as exc:
        logger.exception("unexpected live validation failure")
        raise HTTPException(status_code=500, detail="live validation failed safely") from exc
    finally:
        if acquired:
            live_validation_coordinator.release(user_id)
