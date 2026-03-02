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
import sys

# Windowsの cp932 エンコード環境で Unicode 文字列の print/log が失敗しないよう UTF-8 に固定
for _s in (sys.stdout, sys.stderr):
    if _s is not None and hasattr(_s, "reconfigure"):
        try:
            _s.reconfigure(encoding="utf-8", errors="backslashreplace")
        except Exception:
            pass

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from slowapi import Limiter, _rate_limit_exceeded_handler  # type: ignore
from slowapi.util import get_remote_address  # type: ignore
from slowapi.errors import RateLimitExceeded  # type: ignore
from middleware.auth import SupabaseJWTMiddleware  # type: ignore

from routers import (  # type: ignore
    backfill,
    debug_data,
    export,
    internal,
    models_mgmt,
    predict,
    profiling,
    purchase,
    scrape,
    stats,
    train,
)

# ── Rate Limiter（インメモリ・Redis不要） ──────────────────────────────
limiter = Limiter(key_func=get_remote_address, default_limits=["60/minute"])

app = FastAPI(
    title="Keiba AI - Machine Learning API",
    description="競馬予測AIのための機械学習API",
    version="1.0.0",
)
app.state.limiter = limiter
app.add_exception_handler(RateLimitExceeded, _rate_limit_exceeded_handler)

# JWT 認証ミドルウェア（CORS より先に登録）
# 注意: Starlette のミドルウェアスタックは LIFOオーダー（後入れが先に動く）なので、最後に add したものが一番外側で動く
_EXEMPT_PATHS = {"/", "/health", "/docs", "/openapi.json", "/redoc"}
app.add_middleware(SupabaseJWTMiddleware, exempt_paths=_EXEMPT_PATHS)

# CORS設定（全オリジンを許可）
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
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
app.include_router(export.router)
app.include_router(backfill.router)
app.include_router(profiling.router)
app.include_router(internal.router)
app.include_router(debug_data.router)


@app.get("/health")
async def health():
    return {"status": "ok"}


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(app, host="0.0.0.0", port=8000)
