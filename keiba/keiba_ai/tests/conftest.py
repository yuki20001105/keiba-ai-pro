"""
pytest フィクスチャ共有定義。

scope='session' で一度だけ読み込む DB 接続・学習用 DataFrame を提供する。
DB が存在しない環境ではスキップマーカーを付与する。

使用例:
    pytest keiba/keiba_ai/tests/ -v
    pytest keiba/keiba_ai/tests/test_feature_engineering.py -v
    pytest keiba/keiba_ai/tests/test_train_inference_consistency.py -v -s
"""
from __future__ import annotations

import warnings
from pathlib import Path
from typing import Optional

import pandas as pd
import pytest

# ── pandas FutureWarning（all-NA列の concat 動作変更）を抑制 ─────────────────
# pandas >= 2.1 で発生する警告。テストの合否に影響しないため非表示にする。
warnings.filterwarnings(
    "ignore",
    category=FutureWarning,
    message=".*DataFrame concatenation with empty or all-NA entries.*",
)

# ── プロジェクトルートとDB パス解決 ─────────────────────────────────────────
_HERE   = Path(__file__).resolve().parent
_KEIBA  = _HERE.parent          # keiba/keiba_ai/
_ROOT   = _KEIBA.parent.parent  # プロジェクトルート (keiba-ai-pro/)
_PYTHON_API = _ROOT / "python-api"

_ULTIMATE_DB_CANDIDATES = [
    _PYTHON_API / "data" / "keiba_ultimate.db",
    _ROOT / "keiba" / "data" / "keiba_ultimate.db",
    _ROOT / "data" / "keiba_ultimate.db",
]


def _find_db() -> Optional[Path]:
    for p in _ULTIMATE_DB_CANDIDATES:
        if p.exists():
            return p
    return None


# ── セッション共有フィクスチャ ────────────────────────────────────────────────

@pytest.fixture(scope="session")
def ultimate_db_path() -> Path:
    """Ultimate DB のパスを返す。存在しなければテストをスキップする。"""
    p = _find_db()
    if p is None:
        pytest.skip(
            "keiba_ultimate.db が見つかりません。"
            "データ取得を実行してから再テストしてください。"
        )
    return p


@pytest.fixture(scope="session")
def history_df(ultimate_db_path: Path) -> pd.DataFrame:
    """Ultimate DB から全学習フレームを読み込む（セッション中1回のみ）。"""
    import sys
    sys.path.insert(0, str(_PYTHON_API))

    from keiba_ai.db_ultimate_loader import load_ultimate_training_frame  # type: ignore

    df = load_ultimate_training_frame(ultimate_db_path)
    if df.empty:
        pytest.skip("DB にデータがありません。インジェストを先に実行してください。")
    return df


@pytest.fixture(scope="session")
def small_history_df(history_df: pd.DataFrame) -> pd.DataFrame:
    """テスト用に最新 500 行の部分データを返す（速度優先）。"""
    return history_df.tail(500).reset_index(drop=True)


@pytest.fixture(scope="session")
def sample_race_ids(history_df: pd.DataFrame) -> list[str]:
    """結果が確定しているレース ID を最大 5 件返す（整合性テスト用）。"""
    df = history_df
    if "finish" not in df.columns or "race_id" not in df.columns:
        return []
    finished = df[pd.to_numeric(df["finish"], errors="coerce").between(1, 18)]
    race_ids = finished["race_id"].unique().tolist()
    # 最新レースから選ぶ（race_id は YYYYMMDD プレフィックス）
    race_ids.sort(reverse=True)
    return race_ids[:5]
