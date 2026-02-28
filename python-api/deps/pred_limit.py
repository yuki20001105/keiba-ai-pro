"""
予測回数制限ヘルパー
  free:    10回/月 (pred_count_remaining カラム)
  premium: 無制限  (pred_count_remaining = -1)

Supabase 無効 / エラー時はスルー（ローカル開発・fallback）。
"""
from __future__ import annotations

import logging
from typing import Optional

from fastapi import HTTPException, Request

logger = logging.getLogger(__name__)
_FREE_MONTHLY_LIMIT = 10


def _consume_pred_count(user_id: str) -> int:
    """
    Supabase RPC consume_pred_count を呼んで消費後残数を返す。
    -1   : premium（無制限）
    -999 : 残数不足 → 呼び出し元が 429 を返す
    """
    try:
        from app_config import get_supabase_client  # type: ignore
        client = get_supabase_client()
        if not client:
            return -1  # Supabase 無効 → スルー
        res = client.rpc("consume_pred_count", {"p_user_id": user_id}).execute()
        return int(res.data) if res.data is not None else -1
    except Exception as e:
        logger.warning(f"pred_count 消費失敗（スルー）: {e}")
        return -1


async def check_and_consume_pred_count(request: Request) -> None:
    """
    predict エンドポイント先頭で直接呼ぶ関数。
    user_id が無い（= 未認証）場合は JWT ミドルウェアに任せてスルー。
    残数不足なら HTTP 429 を返す。
    """
    user_id: Optional[str] = getattr(request.state, "user_id", None)
    if not user_id:
        return

    remaining = _consume_pred_count(user_id)

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
            },
        )

    # 残数を state に保存（レスポンスヘッダーへの転送等に利用可）
    request.state.pred_count_remaining = remaining if remaining >= 0 else -1
