"""
プロファイリングエンドポイント
POST /api/profiling/start
GET  /api/profiling/status/{job_id}
GET  /api/profiling/html/{job_id}
"""
from __future__ import annotations

import threading
import uuid
from pathlib import Path

from fastapi import APIRouter, HTTPException
from fastapi.responses import HTMLResponse

from app_config import SUPABASE_ENABLED, ULTIMATE_DB, get_supabase_client, logger  # type: ignore

router = APIRouter()

_profiling_jobs: dict = {}  # job_id → {status, message, html}


def _run_profiling_sync(job_id: str, use_optimized: bool) -> None:
    """バックグラウンドスレッドで ydata-profiling レポートを生成"""
    try:
        def _update(msg: str):
            _profiling_jobs[job_id]["message"] = msg
            logger.info(f"[profiling:{job_id}] {msg}")

        _update("データ読み込み中...")
        db_path = ULTIMATE_DB

        if SUPABASE_ENABLED and get_supabase_client() and not db_path.exists():
            _update("Supabase からデータ同期中...")
            db_path.parent.mkdir(parents=True, exist_ok=True)
            from app_config import sync_supabase_to_sqlite  # type: ignore
            sync_supabase_to_sqlite(db_path)

        from keiba_ai.db_ultimate_loader import load_ultimate_training_frame  # type: ignore
        df_raw = load_ultimate_training_frame(db_path)
        if df_raw.empty:
            raise ValueError("データがありません。先にデータ取得を実行してください。")

        n_rows = len(df_raw)
        _update(f"特徴量エンジニアリング中... ({n_rows}件)")

        from keiba_ai.feature_engineering import add_derived_features  # type: ignore
        df_fe = add_derived_features(df_raw)

        if use_optimized:
            _update("LightGBM 特徴量最適化中（不要列削除・変換）...")
            from keiba_ai.lightgbm_feature_optimizer import prepare_for_lightgbm_ultimate  # type: ignore
            import io, sys  # noqa: E401
            _buf = io.StringIO()
            _old = sys.stdout
            sys.stdout = _buf
            try:
                df_target, _, _ = prepare_for_lightgbm_ultimate(df_fe, is_training=True)
            finally:
                sys.stdout = _old
            exclude = {"race_id", "horse_id", "jockey_id", "trainer_id", "win"}
            df_target = df_target.drop(columns=[c for c in exclude if c in df_target.columns])
        else:
            df_target = df_fe

        n_cols = len(df_target.columns)
        _update(f"ydata-profiling レポート生成中... ({n_rows}行 × {n_cols}列)  ※数分かかります")

        try:
            from ydata_profiling import ProfileReport  # type: ignore
        except ImportError:
            raise ImportError("ydata-profiling が未インストールです。pip install ydata-profiling を実行してください。")

        profile = ProfileReport(
            df_target,
            title=f"Keiba AI Feature Profiling {'(最適化済)' if use_optimized else '(FE後)'}",
            minimal=True,
            progress_bar=False,
            correlations={
                "pearson": {"calculate": True}, "spearman": {"calculate": False},
                "kendall": {"calculate": False}, "phi_k": {"calculate": False}, "cramers": {"calculate": False},
            },
            explorative=False,
        )
        html = profile.to_html()

        _profiling_jobs[job_id] = {
            "status": "completed",
            "message": f"完了 ({n_rows}行 × {n_cols}列)",
            "html": html,
        }
        logger.info(f"[profiling:{job_id}] 完了")

    except Exception as e:
        logger.error(f"[profiling:{job_id}] エラー: {e}", exc_info=True)
        _profiling_jobs[job_id] = {"status": "error", "message": str(e), "html": None}


@router.post("/api/profiling/start")
async def start_profiling(use_optimized: bool = True):
    """ydata-profiling レポートの非同期生成を開始する"""
    job_id = uuid.uuid4().hex[:10]
    _profiling_jobs[job_id] = {"status": "running", "message": "開始中...", "html": None}
    t = threading.Thread(target=_run_profiling_sync, args=(job_id, use_optimized), daemon=True)
    t.start()
    return {"job_id": job_id}


@router.get("/api/profiling/status/{job_id}")
async def get_profiling_status(job_id: str):
    """プロファイリングジョブの進捗を返す"""
    job = _profiling_jobs.get(job_id)
    if not job:
        raise HTTPException(status_code=404, detail="ジョブが見つかりません")
    return {"status": job["status"], "message": job["message"]}


@router.get("/api/profiling/html/{job_id}")
async def get_profiling_html(job_id: str):
    """生成済み HTML レポートを返す"""
    job = _profiling_jobs.get(job_id)
    if not job:
        raise HTTPException(status_code=404, detail="ジョブが見つかりません")
    if job["status"] != "completed" or not job["html"]:
        raise HTTPException(status_code=202, detail=f"レポート未完成: {job['status']}")
    return HTMLResponse(content=job["html"], media_type="text/html")
