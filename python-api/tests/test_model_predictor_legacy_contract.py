from __future__ import annotations

import sys
from pathlib import Path

import pandas as pd


ROOT = Path(__file__).resolve().parents[2]
PYTHON_API = ROOT / "python-api"
KEIBA = ROOT / "keiba"
for path in (PYTHON_API, KEIBA):
    if str(path) not in sys.path:
        sys.path.insert(0, str(path))

from routers.predict import ModelPredictor  # type: ignore  # noqa: E402


class _DropLegacyColumnsOptimizer:
    def transform(self, frame: pd.DataFrame) -> pd.DataFrame:
        return frame.drop(columns=["age", "season"], errors="ignore")


def test_build_features_restores_computed_legacy_numeric_columns() -> None:
    bundle = {
        "model": object(),
        "target": "speed_deviation",
        "optimizer": _DropLegacyColumnsOptimizer(),
        "feature_columns": ["age", "season", "odds"],
    }
    source = pd.DataFrame(
        [{
            "race_id": "202604020812",
            "horse_id": "2024100001",
            "age": 4,
            "odds": 8.2,
            "date": "20260816",
        }]
    )

    features = ModelPredictor(bundle, "legacy.joblib").build_features(source, source)

    assert features.columns.tolist() == ["age", "season", "odds"]
    assert features.loc[0, "age"] == 4
    assert features.loc[0, "season"] == 1
    assert features.loc[0, "odds"] == 8.2
