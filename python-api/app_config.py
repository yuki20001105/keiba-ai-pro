"""
共通設定・ヘルパー
全ルーターから import される設定・定数・ユーティリティ
"""
from __future__ import annotations

import logging
import sys
from pathlib import Path
from typing import Any, Dict, Optional

import joblib
from fastapi import HTTPException

# ── keiba_ai モジュールパスを sys.path に追加 ──────────────────────
sys.path.insert(0, str(Path(__file__).parent.parent / "keiba"))

# ── ログ設定 ──────────────────────────────────────────────────────
log_file = Path(__file__).parent / "optuna_debug.log"
logging.basicConfig(
    level=logging.DEBUG,
    format="%(asctime)s - %(levelname)s - %(message)s",
    handlers=[
        logging.FileHandler(log_file, mode="a", encoding="utf-8"),
        logging.StreamHandler(),
    ],
)
logger = logging.getLogger(__name__)
logger.info("=" * 80)
logger.info("app_config ロード開始")
logger.info(f"ログファイル: {log_file}")
logger.info("=" * 80)

# ── パス定数 ──────────────────────────────────────────────────────
MODELS_DIR = Path(__file__).parent / "models"
MODELS_DIR.mkdir(exist_ok=True)

CONFIG_PATH = Path(__file__).parent.parent / "keiba" / "config.yaml"
ULTIMATE_DB = Path(__file__).parent.parent / "keiba" / "data" / "keiba_ultimate.db"

# ── keiba_ai.config の load_config を再エクスポート ──────────────
try:
    from keiba_ai.config import load_config  # type: ignore  # noqa: F401
except ImportError:
    def load_config(path):  # type: ignore
        raise ImportError("keiba_ai パッケージが見つかりません")

# ── Supabase クライアント（インポート失敗時はダミーで続行） ──────────
try:
    from supabase_client import (  # type: ignore
        save_race_to_supabase,
        get_data_stats_from_supabase,
        sync_supabase_to_sqlite,
        upload_model_to_supabase,
        download_model_from_supabase,
        list_models_from_supabase,
        delete_model_from_supabase,
        get_client as get_supabase_client,
        get_pedigree_cache,
        get_pedigree_cache_batch,
        save_pedigree_cache,
    )
    SUPABASE_ENABLED = True
    logger.info("Supabase クライアント読み込み成功")
except ImportError:
    SUPABASE_ENABLED = False
    logger.warning("supabase_client.py が見つかりません: Supabase 連携無効")

    # ダミー関数（型エラー回避）
    def save_race_to_supabase(*a, **kw):  # type: ignore
        return False

    def get_data_stats_from_supabase(*a, **kw):  # type: ignore
        return {}

    def sync_supabase_to_sqlite(*a, **kw):  # type: ignore
        return 0

    def upload_model_to_supabase(*a, **kw):  # type: ignore
        return False

    def download_model_from_supabase(*a, **kw):  # type: ignore
        return False

    def list_models_from_supabase(*a, **kw):  # type: ignore
        return []

    def delete_model_from_supabase(*a, **kw):  # type: ignore
        return False

    def get_supabase_client():  # type: ignore
        return None

    def get_pedigree_cache(*a, **kw):  # type: ignore
        return {}

    def get_pedigree_cache_batch(*a, **kw):  # type: ignore
        return {}

    def save_pedigree_cache(*a, **kw):  # type: ignore
        return False


# ── モデルヘルパー ──────────────────────────────────────────────────


def get_latest_model() -> Optional[Path]:
    """最新のモデルファイルを返す"""
    models = list(MODELS_DIR.glob("model_*.joblib"))
    if not models:
        return None
    return max(models, key=lambda p: p.stat().st_mtime)


def _ensure_model_local(model_id: str) -> Optional[Path]:
    """モデルをローカルで探し、なければ Supabase からダウンロード"""
    local_files = list(MODELS_DIR.glob(f"*{model_id}*.joblib"))
    if local_files:
        return local_files[0]
    if SUPABASE_ENABLED and get_supabase_client():
        dest = MODELS_DIR / f"model_{model_id}.joblib"
        if download_model_from_supabase(model_id, dest):
            return dest
    return None


def load_model_bundle(model_path: Path) -> Dict[str, Any]:
    """モデルバンドルをロード"""
    try:
        return joblib.load(model_path)
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"モデルのロードに失敗: {str(e)}")
