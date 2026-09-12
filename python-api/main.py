"""
FastAPI機械学習サーバー
Streamlit版の機械学習パイプラインをREST APIとして提供
【3-3. 一括予測購入推奨】機能を完全実装

main.py (slim) - ルーターを機能ごとのモジュールに分割した版
  元ファイル: main_original.py (4252行)
  モジュール構成:
    app_config.py      - 共通設定ヘルパー
    models.py          - Pydantic モデル定義
    scraping/
      constants.py     - HTML_STRAINER, VENUE_MAP, SCRAPE_HEADERS, COAT_COLORS, COAT_RE
      horse.py         - scrape_horse_detail, extract_coat_color
      race.py          - scrape_race_full
      jobs.py          - _scrape_jobs, _run_scrape_job, _purge_old_jobs
      storage.py       - _init_sqlite_db, _save_race_sqlite_only, _save_race_to_ultimate_db
    routers/
      stats.py         - GET /, /api/debug, /api/data_stats, /api/test/*
      train.py         - POST /api/train, /api/train/start, GET /api/train/status/{id}
      predict.py       - POST /api/predict, /api/analyze_race
      models_mgmt.py   - GET/DELETE /api/models
      purchase.py      - POST /api/purchase, GET /api/purchase_history, /api/statistics
      scrape.py        - POST /api/scrape/start, GET /api/scrape/status/{id}, etc.
      export.py        - GET /api/export-data, /api/export-db, DELETE /api/data/all
      backfill.py      - POST /api/backfill/*
      profiling.py     - POST /api/profiling/start, GET /api/profiling/*
"""
import asyncio
import sys

# Windowsの cp932 エンコード環境で Unicode 文字列の print/log が失敗しないよう UTF-8 に固定
for _s in (sys.stdout, sys.stderr):
    if _s is not None and hasattr(_s, "reconfigure"):
        try:
            _s.reconfigure(encoding="utf-8", errors="backslashreplace")
        except Exception:
            pass

from contextlib import asynccontextmanager
import os

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from slowapi import Limiter, _rate_limit_exceeded_handler  # type: ignore
from slowapi.util import get_remote_address  # type: ignore
from slowapi.errors import RateLimitExceeded  # type: ignore
from app_config import ALLOWED_ORIGINS, ULTIMATE_DB  # type: ignore
from middleware.auth import SupabaseJWTMiddleware  # type: ignore
from scraping.storage import _init_sqlite_db  # type: ignore

from routers import (  # type: ignore
    backfill,
    bet_export,
    debug_data,
    export,
    feature_analysis,
    internal,
    live_validation,
    models_mgmt,
    predict,
    prediction_history,
    profiling,
    purchase,
    races,
    realtime_odds,
    scrape,
    stats,
    train,
)
from scheduler import start_scheduler, stop_scheduler  # type: ignore
from scraping.operational_saga_runtime import (  # type: ignore
    start_operational_saga_worker,
    stop_operational_saga_worker,
)

# ── Rate Limiter（インメモリ・Redis不要） ──────────────────────────────
limiter = Limiter(key_func=get_remote_address, default_limits=["60/minute"])


@asynccontextmanager
async def lifespan(app: FastAPI):
    # Render Free has an ephemeral filesystem.  After a cold start the SQLite
    # path may exist as an empty file, so every request-path SELECT must be
    # preceded by idempotent schema initialization.  The database is only a
    # local working cache; authoritative Phase 3N observations remain in
    # Supabase PostgreSQL.
    await asyncio.to_thread(_init_sqlite_db, ULTIMATE_DB)
    await asyncio.to_thread(train.reconcile_interrupted_train_jobs)
    start_scheduler()
    await start_operational_saga_worker()
    try:
        yield
    finally:
        await stop_operational_saga_worker()
        stop_scheduler()


app = FastAPI(
    title="Keiba AI - Machine Learning API",
    description="競馬予測AIのための機械学習API",
    version="1.0.0",
    lifespan=lifespan,
)
app.state.limiter = limiter
app.add_exception_handler(RateLimitExceeded, _rate_limit_exceeded_handler)

# JWT 認証ミドルウェア（CORS より先に登録）
# 注意: Starlette のミドルウェアスタックは LIFOオーダー（後入れが先に動く）なので、最後に add したものが一番外側で動く
_EXEMPT_PATHS = {"/", "/health", "/docs", "/openapi.json", "/redoc"}
app.add_middleware(SupabaseJWTMiddleware, exempt_paths=_EXEMPT_PATHS)

# CORS設定（app_config の明示 allowlist のみ）
app.add_middleware(
    CORSMiddleware,
    allow_origins=list(ALLOWED_ORIGINS),
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)

# ルーターを登録
app.include_router(stats.router)
app.include_router(train.router)
app.include_router(predict.router)
app.include_router(models_mgmt.router)
app.include_router(purchase.router)
app.include_router(scrape.router)
app.include_router(backfill.router)
app.include_router(export.router)
app.include_router(profiling.router)
app.include_router(races.router)
app.include_router(internal.router)
app.include_router(live_validation.router)
app.include_router(debug_data.router)
app.include_router(realtime_odds.router)
app.include_router(bet_export.router)
app.include_router(feature_analysis.router)
app.include_router(prediction_history.router)


@app.middleware("http")
async def live_validation_no_store(request, call_next):
    """Never cache live-validation success, validation, or auth responses."""
    response = await call_next(request)
    if request.url.path == "/api/scrape/live-validation":
        response.headers["Cache-Control"] = "no-store"
    return response


@app.get("/health")
async def health():
    return {
        "status": "ok",
        "app_env": os.environ.get("APP_ENV", "development").strip().lower(),
        "model_runtime_status": os.environ.get("MODEL_RUNTIME_STATUS", "disabled").strip().lower(),
        "observation_enabled": os.environ.get("PHASE3N_OBSERVATION_ENABLED", "").strip().lower()
        in {"true", "1", "yes"},
        "observation_release_mode": os.environ.get(
            "PHASE3N_OBSERVATION_RELEASE_MODE", "disabled"
        ).strip().lower(),
        "automated_betting_enabled": os.environ.get(
            "AUTOMATED_BETTING_ENABLED", ""
        ).strip().lower()
        in {"true", "1", "yes"},
    }


if __name__ == "__main__":
    import uvicorn

    api_host = os.environ.get("API_HOST", "0.0.0.0").strip() or "0.0.0.0"
    uvicorn.run("main:app", host=api_host, port=8000, reload=False)
