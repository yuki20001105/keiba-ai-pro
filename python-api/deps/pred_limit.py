"""
予測回数制限ヘルパー
  free:    10回/月 (pred_count_remaining カラム)
  premium: 無制限  (pred_count_remaining = -1)
"""
from __future__ import annotations

import logging
import os
import uuid
from typing import Optional

from fastapi import HTTPException, Request

logger = logging.getLogger(__name__)
_FREE_MONTHLY_LIMIT = 10


def _runtime_env() -> str:
    return (os.environ.get("APP_ENV") or "development").strip().lower()


def _is_local_or_test_env() -> bool:
    return _runtime_env() in {"development", "local", "test"}


def _allow_local_bypass() -> bool:
    return os.environ.get("PRED_LIMIT_ALLOW_FAIL_OPEN", "false").strip().lower() in {
        "1", "true", "yes", "on"
    }


def _raise_quota_backend_503() -> None:
    raise HTTPException(
        status_code=503,
        detail={
            "error": "pred_limit_backend_unavailable",
            "message": "予測回数制御基盤に接続できません。しばらくしてから再試行してください。",
        },
    )


def _validate_remaining_value(raw: object) -> int:
    if raw is None or isinstance(raw, bool):
        raise ValueError(f"invalid remaining type: {type(raw)}")

    remaining = int(raw)
    if remaining in (-1, -999):
        return remaining
    if remaining >= 0:
        return remaining
    raise ValueError(f"invalid remaining value: {remaining}")


def _handle_quota_backend_failure(msg: str) -> int:
    if _is_local_or_test_env() and _allow_local_bypass():
        logger.warning(f"pred_count backend failure bypassed in local/test: {msg}")
        return -1
    logger.error(f"pred_count backend failure (fail-closed): {msg}")
    _raise_quota_backend_503()


def _consume_pred_count(user_id: str, units: int = 1) -> int:
    """
    Supabase RPC で予測回数を消費し、消費後残数を返す。
    -1   : premium（無制限）
    -999 : 残数不足 → 呼び出し元が 429 を返す
    """
    if units < 1 or units > 100:
        raise ValueError("units must be between 1 and 100")

    try:
        uuid.UUID(str(user_id))
    except Exception:
        return _handle_quota_backend_failure("invalid user_id format")

    try:
        from app_config import get_supabase_client  # type: ignore
        client = get_supabase_client()
        if not client:
            return _handle_quota_backend_failure("Supabase client unavailable")

        rpc_name = "consume_pred_count" if units == 1 else "consume_pred_count_batch"
        payload = {"p_user_id": user_id} if units == 1 else {"p_user_id": user_id, "p_units": units}
        res = client.rpc(rpc_name, payload).execute()
        return _validate_remaining_value(res.data)
    except Exception as e:
        return _handle_quota_backend_failure(str(e))


async def check_and_consume_pred_count(request: Request, units: int = 1) -> None:
    """
    predict/analyze エンドポイント先頭で直接呼ぶ関数。
    user_id が無い（= 未認証）場合は JWT ミドルウェアに任せてスルー。
    残数不足なら HTTP 429 を返す。
    """
    user_id: Optional[str] = getattr(request.state, "user_id", None)
    if not user_id:
        return

    remaining = _consume_pred_count(user_id, units=units)

    if remaining == -999:
        raise HTTPException(
            status_code=429,
            detail={
                "error": "pred_limit_exceeded",
                "message": (
                    f"月間予測回数の上限（{_FREE_MONTHLY_LIMIT}回）に達しました。"
                    "プレミアムプランにアップグレードすると無制限になります。"
                ),
                "remaining": 0,
                "required_units": units,
            },
        )

    # 残数を state に保存（レスポンスヘッダーへの転送等に利用可）
    request.state.pred_count_remaining = remaining if remaining >= 0 else -1
