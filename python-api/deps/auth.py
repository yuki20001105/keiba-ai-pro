"""
認証依存関係

FastAPI の Depends() で使用する guard 関数。
JWT ミドルウェアが request.state にセットした情報を利用し、
必要に応じて Supabase profiles テーブルで役割・tier を再確認する。

使い方:
    from deps.auth import require_admin, require_premium

    @router.post("/api/scrape/start")
    async def scrape_start(
        request: ScrapeRequest,
        _: dict = Depends(require_admin),
    ):
        ...
"""
from __future__ import annotations

import logging
from typing import Optional

from fastapi import Depends, HTTPException, Request

logger = logging.getLogger(__name__)


# ── ベース: 現在のユーザー取得 ────────────────────────────────────────


async def get_current_user(request: Request) -> dict:
    """
    JWT ミドルウェアがセットした user_id を取得。
    ミドルウェアを通過していない（= 未認証）場合は 401 を返す。
    """
    user_id: Optional[str] = getattr(request.state, "user_id", None)
    if not user_id:
        raise HTTPException(status_code=401, detail="認証が必要です")
    return {
        "user_id": user_id,
        "role": getattr(request.state, "user_role", "user"),
        "subscription_tier": getattr(request.state, "subscription_tier", "free"),
    }


def _get_profile_from_db(user_id: str) -> Optional[dict]:
    """Supabase profiles テーブルから role / subscription_tier を取得"""
    try:
        from app_config import get_supabase_client  # type: ignore

        client = get_supabase_client()
        if not client:
            return None
        res = (
            client.table("profiles")
            .select("role, subscription_tier")
            .eq("id", user_id)
            .single()
            .execute()
        )
        return res.data if res.data else None
    except Exception as e:
        logger.debug(f"profiles 取得失敗 ({user_id}): {e}")
        return None


# ── Guard: Admin 専用 ────────────────────────────────────────────────


async def require_admin(user: dict = Depends(get_current_user)) -> dict:
    """
    Admin ロールが必要なエンドポイント用ガード。

    JWT の claim よりも DB の profiles.role を優先する（改ざん防止）。
    DB 接続できない場合は JWT claim を信頼する。

    401: 未認証
    403: 権限不足
    """
    profile = _get_profile_from_db(user["user_id"])
    role: str = profile.get("role", "user") if profile else user["role"]

    if role != "admin":
        raise HTTPException(
            status_code=403,
            detail="管理者権限が必要です（スクレイピングは管理者のみ実行できます）",
        )
    return {**user, "role": role}


# ── Guard: Premium 専用 ──────────────────────────────────────────────


async def require_premium(user: dict = Depends(get_current_user)) -> dict:
    """
    Premium サブスクリプションが必要なエンドポイント用ガード。

    JWT の claim よりも DB の profiles.subscription_tier を優先。

    401: 未認証
    403: Free プラン（アップグレード誘導）
    """
    profile = _get_profile_from_db(user["user_id"])
    tier: str = (
        profile.get("subscription_tier", "free") if profile else user["subscription_tier"]
    )

    if tier != "premium":
        raise HTTPException(
            status_code=403,
            detail="Premiumプランへのアップグレードが必要です。/pricing からアップグレードできます。",
        )
    return {**user, "subscription_tier": tier}
