"""
Supabase JWT 認証ミドルウェア

検証方式: RS256 (JWKS) のみ
  - Supabase 公式推奨の非対称鍵検証
  - SUPABASE_JWT_SECRET（共有秘密）は一切使用しない
  - 漏洩リスクのある秘密情報をサーバーに持たせない設計

SUPABASE_URL 未設定（ローカル開発）の場合は認証スキップ。
/api/* 以外のパスおよび EXEMPT_PATHS はスルー。
"""
from __future__ import annotations

import logging
import os
import time
from typing import Optional, Set

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import JSONResponse

logger = logging.getLogger(__name__)

SUPABASE_URL = os.environ.get("SUPABASE_URL", "")

# JWKS キャッシュ（TTL: 1 時間）
_jwks_cache: Optional[dict] = None
_jwks_cache_at: float = 0.0
_JWKS_TTL = 3600.0  # 秒


async def _fetch_jwks(force: bool = False) -> dict:
    """Supabase JWKS エンドポイントからキーセットを取得（TTL キャッシュ付き）"""
    global _jwks_cache, _jwks_cache_at
    now = time.monotonic()
    if not force and _jwks_cache and (now - _jwks_cache_at) < _JWKS_TTL:
        return _jwks_cache
    if not SUPABASE_URL:
        return {}
    url = f"{SUPABASE_URL}/auth/v1/.well-known/jwks.json"
    try:
        import httpx
        async with httpx.AsyncClient(timeout=5.0) as client:
            resp = await client.get(url)
            resp.raise_for_status()
            _jwks_cache = resp.json()
            _jwks_cache_at = now
            logger.info("JWKS キャッシュ更新完了")
            return _jwks_cache
    except Exception as e:
        logger.warning(f"JWKS 取得失敗: {e}")
        return _jwks_cache or {}


async def verify_jwt(token: str) -> Optional[dict]:
    """
    JWT を JWKS（RS256）で検証してペイロードを返す。

    ⚠️ HS256（共有秘密）は使用しない。
       Supabase が推奨する非対称鍵検証のみ。
    SUPABASE_URL 未設定の場合は検証をスキップ（ローカル開発向け）。
    """
    # ローカル開発: SUPABASE_URL 未設定 → 認証スキップ
    if not SUPABASE_URL:
        logger.debug("SUPABASE_URL 未設定 → JWT 検証スキップ（ローカル開発）")
        try:
            from jose import jwt  # type: ignore
            return jwt.get_unverified_claims(token)
        except Exception:
            return {"sub": "local-dev", "role": "authenticated"}

    try:
        from jose import jwt, jwk, JWTError  # type: ignore

        jwks_data = await _fetch_jwks()
        keys = jwks_data.get("keys", [])
        if not keys:
            logger.error("JWKS キーが取得できません。Supabase JWT Signing Keys を確認してください。")
            return None

        header = jwt.get_unverified_header(token)
        kid = header.get("kid")
        key_data = next(
            (k for k in keys if k.get("kid") == kid),
            keys[0],
        )
        alg = key_data.get("alg", "RS256")
        public_key = jwk.construct(key_data)
        payload = jwt.decode(
            token,
            public_key,
            algorithms=["RS256", "ES256"],
            options={"verify_aud": False},
        )
        return payload

    except Exception as e:
        # kid ミスマッチの可能性 → キャッシュを強制更新して1回再試
        logger.debug(f"JWT 検証失敗（キャッシュ更新して再試行）: {e}")
        try:
            from jose import jwt, jwk  # type: ignore
            jwks_data = await _fetch_jwks(force=True)
            keys = jwks_data.get("keys", [])
            if not keys:
                return None
            header = jwt.get_unverified_header(token)
            kid = header.get("kid")
            key_data = next(
                (k for k in keys if k.get("kid") == kid),
                keys[0],
            )
            public_key = jwk.construct(key_data)
            return jwt.decode(
                token,
                public_key,
                algorithms=["RS256", "ES256"],
                options={"verify_aud": False},
            )
        except Exception as e2:
            logger.debug(f"JWT 検証失敗（再試行後）: {e2}")
            return None


class SupabaseJWTMiddleware(BaseHTTPMiddleware):
    """
    /api/* エンドポイントに JWT 認証を強制するミドルウェア。

    - EXEMPT_PATHS に含まれるパスはスルー
    - Bearer トークンがない / 無効 → 401
    - 検証成功 → request.state に user_id / user_role / subscription_tier をセット
    """

    def __init__(self, app, exempt_paths: Optional[Set[str]] = None):
        super().__init__(app)
        self.exempt_paths: Set[str] = exempt_paths or {
            "/",
            "/health",
            "/docs",
            "/openapi.json",
            "/redoc",
        }

    async def dispatch(self, request: Request, call_next):
        path = request.url.path

        # exempt パス or /api/ 以外はスルー
        if path in self.exempt_paths or not path.startswith("/api/"):
            return await call_next(request)

        # ローカル開発: SUPABASE_URL 未設定 → auth スキップ（admin として扱う）
        if not SUPABASE_URL:
            request.state.user_id = "local-dev"
            request.state.user_role = "admin"
            request.state.subscription_tier = "premium"
            request.state.jwt_payload = {}
            return await call_next(request)

        # Authorization ヘッダー確認
        auth_header = request.headers.get("Authorization", "")
        if not auth_header.startswith("Bearer "):
            return JSONResponse(
                {"detail": "認証が必要です（Authorization: Bearer <token>）"},
                status_code=401,
            )

        token = auth_header[len("Bearer "):]
        payload = await verify_jwt(token)

        if payload is None:
            return JSONResponse(
                {"detail": "無効または期限切れのトークンです"},
                status_code=401,
            )

        # request.state にユーザー情報をセット
        user_meta = payload.get("user_metadata") or {}
        app_meta = payload.get("app_metadata") or {}

        request.state.user_id = payload.get("sub")
        request.state.user_role = (
            app_meta.get("role")
            or user_meta.get("role")
            or "user"
        )
        request.state.subscription_tier = (
            user_meta.get("subscription_tier")
            or "free"
        )
        request.state.jwt_payload = payload

        return await call_next(request)
