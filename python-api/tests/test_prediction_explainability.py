from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from models import AnalyzeRaceRequest
from routers.predict import ModelPredictor


class _ContributionBooster:
    def predict(self, frame: pd.DataFrame, pred_contrib: bool = False):
        assert pred_contrib is True
        assert list(frame.columns) == ["feature_alpha", "feature_beta"]
        return np.array([
            [0.30, -0.10, 0.05],
            [-0.20, 0.40, 0.05],
        ])


def test_tree_shap_explanation_keeps_top_direction_and_relative_impact() -> None:
    predictor = ModelPredictor({
        "model": _ContributionBooster(),
        "target": "speed_deviation",
        "feature_columns": ["feature_alpha", "feature_beta"],
    })
    frame = pd.DataFrame({
        "feature_alpha": [1.0, 2.0],
        "feature_beta": [3.0, 4.0],
    })

    explanations = predictor.explain_scores(frame, top_n=2)

    assert len(explanations) == 2
    assert explanations[0]["method"] == "tree_shap"
    assert explanations[0]["base_value"] == 0.05
    assert explanations[0]["features"][0] == {
        "feature": "feature_alpha",
        "label": "feature alpha",
        "description": "",
        "value": 1.0,
        "contribution": 0.3,
        "impact_pct": 75.0,
        "direction": "positive",
    }
    assert explanations[0]["features"][1]["direction"] == "negative"
    assert explanations[1]["features"][0]["feature"] == "feature_beta"


def test_explanation_is_opt_in_on_analyze_request() -> None:
    assert AnalyzeRaceRequest(race_id="202604070101").include_explanation is False
    assert AnalyzeRaceRequest(
        race_id="202604070101",
        include_explanation=True,
    ).include_explanation is True


def test_invalid_contribution_shape_does_not_look_like_an_explanation() -> None:
    class InvalidBooster:
        def predict(self, _frame: pd.DataFrame, pred_contrib: bool = False):
            return np.zeros((1, 1))

    predictor = ModelPredictor({
        "model": InvalidBooster(),
        "feature_columns": ["feature_alpha", "feature_beta"],
    })
    with pytest.raises(ValueError, match="特徴量寄与度の形が不正"):
        predictor.explain_scores(pd.DataFrame({"feature_alpha": [1], "feature_beta": [2]}))
