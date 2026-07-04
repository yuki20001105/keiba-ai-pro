"""
notebooks/utils/nb_config.py
============================
全Notebookが共通でimportする設定・パス・ユーティリティ。

使い方（各ノートブックの先頭セル）:
    import sys
    from pathlib import Path
    _NB_ROOT = Path(__file__).resolve().parent.parent  # notebooks/
    # または Jupyter の場合:
    _NB_ROOT = Path().resolve()
    sys.path.insert(0, str(_NB_ROOT))
    from utils.nb_config import *
"""
from __future__ import annotations

import logging
import sqlite3
import sys
from datetime import datetime
from pathlib import Path

import pandas as pd

# ═══════════════════════════════════════════════════════════════
# パス解決
# ═══════════════════════════════════════════════════════════════

def _find_project_root() -> Path:
    """カレントディレクトリから上に向かって keiba-ai-pro のルートを探す"""
    p = Path().resolve()
    for _ in range(5):
        if (p / "keiba" / "keiba_ai").exists():
            return p
        p = p.parent
    raise RuntimeError(
        "プロジェクトルートが見つかりません。notebooks/ ディレクトリで実行してください。"
    )


ROOT        = _find_project_root()           # keiba-ai-pro/
NB_DIR      = ROOT / "notebooks"             # notebooks/
KEIBA_DIR   = ROOT / "keiba"                 # keiba/
API_DIR     = ROOT / "python-api"            # python-api/

# データベースパス
EXISTING_DB = KEIBA_DIR / "data" / "keiba_ultimate.db"
SIM_DB_DIR  = NB_DIR / "data" / "simulation"
SIM_DB      = SIM_DB_DIR / "keiba_sim.db"

# モデル・レポート出力先
MODELS_DIR  = API_DIR / "models"
REPORTS_DIR = NB_DIR / "reports"
REPORTS_DIR.mkdir(parents=True, exist_ok=True)

# ═══════════════════════════════════════════════════════════════
# ストア（Notebook 間データ受け渡しキャッシュ）
# ═══════════════════════════════════════════════════════════════
FEATURE_STORE = NB_DIR / "data" / "feature_store"   # 03 → 04 / 05 / 06
MODEL_STORE   = NB_DIR / "data" / "model_store"     # 05 → 04 / 06 / 07
REPORT_STORE  = NB_DIR / "data" / "report_store"    # 07 / 08 用中間出力

for _d in (FEATURE_STORE, MODEL_STORE, REPORT_STORE):
    _d.mkdir(parents=True, exist_ok=True)

# Python パス追加（keiba_ai, python-api の直接 import 用）
for _p in (str(KEIBA_DIR), str(API_DIR)):
    if _p not in sys.path:
        sys.path.insert(0, _p)

# ═══════════════════════════════════════════════════════════════
# ユーザー設定（各 Notebook の先頭セルで上書き可能）
# ═══════════════════════════════════════════════════════════════

# データソースモード
DATA_SOURCE_MODE = "existing"   # "existing" | "simulation"
FEATURE_SOURCE   = "existing"   # "existing" | "simulation"

# 学習・テスト期間
TRAIN_START = "20200101"
TRAIN_END   = "20241231"
TEST_START  = "20250101"
TEST_END    = "20251231"

# モデル設定
TARGET      = "win"          # "win" | "place3"
CV_FOLDS    = 5
OPTUNA_TRIALS = 5
RANDOM_STATE  = 42

# ═══════════════════════════════════════════════════════════════
# ロガー設定
# ═══════════════════════════════════════════════════════════════

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger("nb")


# ═══════════════════════════════════════════════════════════════
# DB ユーティリティ
# ═══════════════════════════════════════════════════════════════

def get_db_path(mode: str | None = None) -> Path:
    """モードに応じた DB パスを返す"""
    m = mode or DATA_SOURCE_MODE
    if m == "simulation":
        SIM_DB_DIR.mkdir(parents=True, exist_ok=True)
        return SIM_DB
    return EXISTING_DB


def db_stats(db_path: Path | None = None) -> dict:
    """DB の基本統計を返す"""
    p = db_path or get_db_path()
    if not p.exists():
        return {"error": f"DB not found: {p}", "races": 0, "results": 0}
    conn = sqlite3.connect(str(p))
    try:
        races   = conn.execute("SELECT COUNT(*) FROM races_ultimate").fetchone()[0]
        results = conn.execute("SELECT COUNT(*) FROM race_results_ultimate").fetchone()[0]
        latest  = conn.execute(
            "SELECT MAX(race_id) FROM races_ultimate"
        ).fetchone()[0]
        dates   = conn.execute("SELECT COUNT(*) FROM scraped_dates").fetchone()[0]
        return {"races": races, "results": results, "latest": latest, "scraped_dates": dates}
    except Exception as e:
        return {"error": str(e)}
    finally:
        conn.close()


def print_config():
    """現在の設定を出力"""
    print("=" * 60)
    print("  Keiba AI Notebook 設定")
    print("=" * 60)
    print(f"  ROOT           : {ROOT}")
    print(f"  DATA_SOURCE    : {DATA_SOURCE_MODE}")
    print(f"  FEATURE_SOURCE : {FEATURE_SOURCE}")
    print(f"  DB (existing)  : {EXISTING_DB} ({'存在' if EXISTING_DB.exists() else '未存在'})")
    print(f"  DB (sim)       : {SIM_DB}")
    print(f"  TRAIN          : {TRAIN_START} ～ {TRAIN_END}")
    print(f"  TEST           : {TEST_START} ～ {TEST_END}")
    print(f"  TARGET         : {TARGET}")
    print(f"  MODELS_DIR     : {MODELS_DIR}")
    print(f"  REPORTS_DIR    : {REPORTS_DIR}")
    print(f"  FEATURE_STORE  : {FEATURE_STORE}")
    print(f"  MODEL_STORE    : {MODEL_STORE}")
    print(f"  REPORT_STORE   : {REPORT_STORE}")
    print("=" * 60)


# ═══════════════════════════════════════════════════════════════
# キャッシュ I/O ユーティリティ
# ═══════════════════════════════════════════════════════════════

def save_store(obj, store_dir: Path, name: str) -> Path:
    """joblib でオブジェクトをストアに保存し、パスを返す"""
    import joblib
    p = store_dir / f"{name}.pkl"
    joblib.dump(obj, p)
    sz = p.stat().st_size / 1024
    print(f"  ✓ {store_dir.name}/{name}.pkl  ({sz:.1f} KB)")
    return p


def load_store(store_dir: Path, name: str):
    """ストアからオブジェクトをロードして返す。未存在の場合は None"""
    import joblib
    p = store_dir / f"{name}.pkl"
    if not p.exists():
        print(f"  ⚠ {store_dir.name}/{name}.pkl が見つかりません")
        return None
    obj = joblib.load(p)
    sz = p.stat().st_size / 1024
    print(f"  ✓ {store_dir.name}/{name}.pkl ロード ({sz:.1f} KB)")
    return obj


def store_exists(store_dir: Path, name: str) -> bool:
    return (store_dir / f"{name}.pkl").exists()
