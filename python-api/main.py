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

from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from slowapi import Limiter, _rate_limit_exceeded_handler  # type: ignore
from slowapi.util import get_remote_address  # type: ignore
from slowapi.errors import RateLimitExceeded  # type: ignore
from middleware.auth import SupabaseJWTMiddleware  # type: ignore

from routers import (  # type: ignore
    backfill,
    bet_export,
    debug_data,
    export,
    feature_analysis,
    feature_store,
    internal,
    models_mgmt,
    mlops,
    predict,
    prediction_history,
    profiling,
    purchase,
    races,
    realtime_odds,
    scrape,
    stats,
    train,
    win5,
)
from scheduler import start_scheduler, stop_scheduler  # type: ignore
import asyncio

import logging
logger = logging.getLogger(__name__)

# ── Rate Limiter（インメモリ・Redis不要） ──────────────────────────────
limiter = Limiter(key_func=get_remote_address, default_limits=["60/minute"])


async def _warmup_caches() -> None:
    """起動後バックグラウンドでモデルと history キャッシュをプリロードする。
    初回リクエスト時の cold start（モデル joblib.load + DB full-scan）を回避する。
    失敗してもサーバー動作には影響しない（エラーは WARNING 止まり）。
    """
    import time as _t
    _t0 = _t.time()
    try:
        from routers.predict import _load_hist_cached  # type: ignore
        await asyncio.to_thread(_load_hist_cached)
        logger.info("[warmup] history キャッシュ ロード完了")
    except Exception as _e:
        logger.warning(f"[warmup] history プリロード失敗: {_e}")

    try:
        from app_config import get_latest_model, load_model_bundle  # type: ignore
        _mp = get_latest_model()
        if _mp:
            await asyncio.to_thread(load_model_bundle, _mp)
            logger.info(f"[warmup] win モデル ロード完了: {_mp.name}")
        for _glob in ("model_place3_*.joblib", "model_speed_deviation_*.joblib"):
            _files = sorted(
                ((__import__("app_config").MODELS_DIR)).glob(_glob),
                key=lambda p: p.stat().st_mtime, reverse=True,
            )
            if _files:
                await asyncio.to_thread(load_model_bundle, _files[0])
                logger.info(f"[warmup] サブモデル ロード完了: {_files[0].name}")
    except Exception as _e:
        logger.warning(f"[warmup] モデルプリロード失敗: {_e}")

    logger.info(f"[warmup] 完了: {_t.time() - _t0:.1f}s")


@asynccontextmanager
async def lifespan(app: FastAPI):
    start_scheduler()
    # 起動直後にモデル・historyキャッシュをバックグラウンドでプリロード（初回リクエストの cold start を回避）
    asyncio.create_task(_warmup_caches())
    # 特徴量ドリフト検出（起動時サイレントチェック）
    try:
        from routers.feature_analysis import _get_catalog, _load_model_features  # type: ignore
        _catalog = _get_catalog()
        _cat_enabled = set(_catalog.enabled_features())
        for _tgt in ("win", "place3", "speed_deviation"):
            try:
                _mf, _mn = _load_model_features(_tgt)
                if not _mf:
                    continue
                _nr = _cat_enabled - _mf
                _nc = _mf - _cat_enabled
                if _nr:
                    logger.warning(
                        f"[feature-drift:{_tgt}] カタログ enabled → モデル未反映 {len(_nr)} 件 "
                        f"→ 再学習推奨: {sorted(_nr)[:5]}{'...' if len(_nr) > 5 else ''}"
                    )
                if _nc:
                    logger.info(
                        f"[feature-drift:{_tgt}] モデル → カタログ未登録 {len(_nc)} 件: "
                        f"{sorted(_nc)[:5]}{'...' if len(_nc) > 5 else ''}"
                    )
                if not _nr and not _nc:
                    logger.debug(f"[feature-drift:{_tgt}] OK — {len(_mf)} 特徴量 整合")
            except Exception:
                pass
    except Exception:
        pass
    yield
    stop_scheduler()
    # 共有 Playwright ブラウザのクリーンアップ
    try:
        from routers.realtime_odds import close_shared_browser  # type: ignore
        await close_shared_browser()
    except Exception:
        pass


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
app.include_router(mlops.router)
app.include_router(purchase.router)
app.include_router(scrape.router)
app.include_router(backfill.router)
app.include_router(profiling.router)
app.include_router(races.router)
app.include_router(internal.router)
app.include_router(debug_data.router)
app.include_router(realtime_odds.router)
app.include_router(bet_export.router)
app.include_router(feature_analysis.router)
app.include_router(feature_store.router)
app.include_router(prediction_history.router)
app.include_router(win5.router)


@app.get("/health")
async def health():
    return {"status": "ok"}


if __name__ == "__main__":
    import os
    import uvicorn

    # FASTAPI_DEV=true で起動すると --reload が有効になる（ターミナル起動用）
    # 注意: VS Code debugpy 経由の F5 起動では reload=False のまま使用すること
    #       (reload=True は uvicorn のマルチプロセス起動となり debugpy と競合するため)
    dev_reload = os.getenv("FASTAPI_DEV", "false").lower() == "true"
    uvicorn.run("main:app", host="0.0.0.0", port=8000, reload=dev_reload)
