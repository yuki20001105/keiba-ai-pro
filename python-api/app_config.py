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


def _model_sort_key(path: "Path") -> "datetime":
    """ファイル名から学習日時を抽出してソートキーに使う。

    対応フォーマット:
      model_win_lightgbm_20260308_044017_ultimate.joblib  → 2026-03-08 04:40:17
      model_win_lightgbm_20160101_20260322_20260418_1923.joblib → 2026-04-18 19:23:00

    ファイル名に日時が含まれない場合は st_mtime にフォールバック。
    """
    import re
    from datetime import datetime as _dt
    stem = path.stem.replace("_ultimate", "")
    # ファイル名末尾の YYYYMMDD_HHMMSS または YYYYMMDD_HHMM を探す
    m = re.search(r"(\d{8})_(\d{4,6})$", stem)
    if m:
        date_part = m.group(1)
        time_part = m.group(2).ljust(6, "0")  # HHMM → HHMMSS 相当に揃える
        try:
            return _dt.strptime(date_part + time_part, "%Y%m%d%H%M%S")
        except ValueError:
            pass
    return _dt.fromtimestamp(path.stat().st_mtime)


def get_latest_model() -> Optional[Path]:
    """最新の win モデルファイルを返す（rank/no_odds モデルは除外）

    [L3-2] model_win_* を優先検索。該当なければ全 model_* から最新を返す。
    ファイル名内の学習日時（YYYYMMDD_HHMMSS）でソートするため、
    コピー・移動によるファイルシステムの st_mtime 変化に影響されない。
    """
    win_models = list(MODELS_DIR.glob("model_win_*.joblib"))
    if win_models:
        return max(win_models, key=_model_sort_key)
    # フォールバック: 全モデルから最新
    models = list(MODELS_DIR.glob("model_*.joblib"))
    if not models:
        return None
    return max(models, key=_model_sort_key)


def verify_feature_columns(
    X: "pd.DataFrame",
    bundle: dict,
    fill_value: float = float("nan"),
) -> "pd.DataFrame":
    """[L3-2 A-6] 推論時に学習時特徴量と一致しているかアサートし、不一致を補修する。

    - 不足列: fill_value（デフォルト NaN）で補完し、WARNING ログを出す
    - 余剰列: 無視（model が使う feature_columns で絞るのでスキップ）
    Returns:
        feature_columns 順に整列した DataFrame
    """
    import numpy as _np
    import pandas as _pd

    feat_cols = bundle.get("feature_columns")
    if not feat_cols:
        return X

    missing_cols = [c for c in feat_cols if c not in X.columns]
    extra_cols   = [c for c in X.columns  if c not in feat_cols]

    if missing_cols:
        logger.warning(
            f"[A-6] 推論 vs 学習 特徴量不一致: "
            f"{len(missing_cols)} 列が欠損 → NaN 補完: {missing_cols[:8]}"
            f"{'...' if len(missing_cols) > 8 else ''}"
        )
        for c in missing_cols:
            X[c] = fill_value
    if extra_cols:
        logger.debug(
            f"[A-6] 推論時に学習時にない {len(extra_cols)} 列（無視）: {extra_cols[:5]}"
        )

    return X[feat_cols]


def assert_feature_columns(
    X: "pd.DataFrame",
    bundle: dict,
    missing_error_threshold: float = 0.10,
) -> None:
    """[S] 推論時に学習特徴量との一致を厳格チェック。

    欠損列が全特徴量の missing_error_threshold を超えたら RuntimeError を raise する。
    verify_feature_columns（NaN補完）の前に呼ぶことを想定。
    閾値: 10%（110特徴量なら11列超過でエラー）
    """
    import pandas as _pd

    feat_cols = bundle.get("feature_columns")
    if not feat_cols:
        return
    missing_cols = [c for c in feat_cols if c not in X.columns]
    missing_rate = len(missing_cols) / max(len(feat_cols), 1)
    if missing_rate > missing_error_threshold:
        raise RuntimeError(
            f"[A-6 ASSERT] 特徴量不一致が重大: {len(missing_cols)}/{len(feat_cols)} 列欠損 "
            f"({missing_rate:.0%} > 閾値{missing_error_threshold:.0%}): {missing_cols[:15]}"
        )
    if missing_cols:
        logger.warning(
            f"[A-6 ASSERT] {len(missing_cols)} 列欠損（閾値以下のため続行）: {missing_cols[:8]}"
        )


def _ensure_model_local(model_id: str) -> Optional[Path]:
    """モデルをローカルで探し、なければ Supabase からダウンロード
    model_id はファイルステム（model_win_lightgbm_...）で渡すと完全一致する。
    旧形式（created_at のみ）は partial glob でフォールバック。
    """
    # 完全一致: model_id がステムと完全に一致するファイル
    exact = MODELS_DIR / f"{model_id}.joblib"
    if exact.exists():
        return exact
    # 部分一致（旧形式: created_at 文字列を含むファイル）— 複数あれば先頭
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
