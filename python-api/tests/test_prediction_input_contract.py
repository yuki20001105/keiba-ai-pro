from types import SimpleNamespace

import numpy as np
import pandas as pd
import pytest

from routers.predict import ModelPredictor
from services import prediction_input_contract as contract


def test_alignment_is_not_semantic_validation(monkeypatch):
    monkeypatch.setattr(contract, "current_feature_hash", lambda: "fixed")
    result = contract.describe_input(
        {"feature_columns": ["odds"], "pipeline_config": {"feature_engineering_hash": "fixed"}},
        pd.DataFrame({"odds": [np.nan, 4.]}), missing_columns=[], restored_columns=[],
    )
    assert result["status"] == "aligned"
    assert result["semantic_parity_verified"] is False
    assert result["missing_value_rate"] == .5
    assert not result["issue_codes"]


def test_non_features_exclude_both_old_object_and_new_string_dtype():
    from routers.predict import _drop_non_features
    frame = pd.DataFrame({"number": [4], "category": pd.Categorical([1]),
        "old_text": pd.Series(["old"], dtype=object),
        "new_text": pd.Series(["new"], dtype="string"), "finish_position": [1]})
    assert _drop_non_features(frame).columns.tolist() == ["number", "category"]


@pytest.mark.parametrize("training_hash,status", [("old", "unverified"), (None, "unverified")])
def test_source_change_or_unknown_is_not_verified(monkeypatch, training_hash, status):
    monkeypatch.setattr(contract, "current_feature_hash", lambda: "new")
    result = contract.describe_input({"feature_columns": ["odds"], "pipeline_config": {
        "feature_engineering_hash": training_hash}}, pd.DataFrame({"odds": [4.]}),
        missing_columns=[], restored_columns=[])
    assert result["status"] == status
    assert result["source_hash_matches"] is (False if training_hash else None)


def test_future_requirement_is_recorded_without_restoring_result(monkeypatch):
    monkeypatch.setattr(contract, "current_feature_hash", lambda: "fixed")
    monkeypatch.setattr("keiba_ai.feature_engineering.add_derived_features", lambda frame, **_: frame)
    predictor = ModelPredictor({"model": object(), "feature_columns": ["odds", "finish_time"],
        "pipeline_config": {"feature_engineering_hash": "fixed"}})
    features = predictor.build_features(pd.DataFrame({"odds": [4.], "finish_time": [99.]}))
    assert np.isnan(features.loc[0, "finish_time"])
    assert predictor.input_diagnostics["status"] == "degraded"
    assert predictor.input_diagnostics["blocked_columns"] == ["finish_time"]
    assert predictor.input_diagnostics["missing_columns"] == ["finish_time"]


def test_numeric_restore_preserves_values_and_separates_absent_column(monkeypatch):
    monkeypatch.setattr(contract, "current_feature_hash", lambda: "fixed")
    monkeypatch.setattr("keiba_ai.feature_engineering.add_derived_features", lambda frame, **_: frame)
    class Pruner:
        def transform(self, frame): return frame.drop(columns=["age"])
    predictor = ModelPredictor({"model": object(), "optimizer": Pruner(),
        "feature_columns": ["odds", "age", "absent"],
        "pipeline_config": {"feature_engineering_hash": "fixed"}})
    features = predictor.build_features(pd.DataFrame({"odds": [4., 5.], "age": [3, 4]}))
    pd.testing.assert_frame_equal(features[["odds", "age"]], pd.DataFrame({"odds": [4., 5.], "age": [3, 4]}))
    assert features.absent.isna().all()
    assert predictor.input_diagnostics["restored_columns"] == ["age"]
    assert predictor.input_diagnostics["missing_columns"] == ["absent"]
    assert predictor.input_diagnostics["all_missing_columns"] == ["absent"]


def test_unknown_feature_contract_and_empty_values_are_explicit(monkeypatch):
    monkeypatch.setattr(contract, "current_feature_hash", lambda: "fixed")
    result = contract.describe_input({}, pd.DataFrame(), missing_columns=[], restored_columns=[])
    assert result["status"] == "unverified"
    assert result["missing_value_rate"] == 1.
    assert "feature_contract_missing" in result["issue_codes"]


def test_recommendation_rejects_structural_input_issue_even_with_validated_probability():
    from test_betting_value_policy import context, run
    evidence = context()
    evidence["input_diagnostics"] = {"status": "degraded"}
    result = run(evidence=evidence)["recommendation"]
    assert result["decision"] == "skip" and result["tickets"] == []
    assert "input_contract_unverified" in result["reason_codes"]


@pytest.mark.parametrize("diagnostics", [None, [], "aligned", {"status": "aligned"}])
def test_missing_or_malformed_input_evidence_cannot_select_bets(diagnostics):
    from test_betting_value_policy import context, run
    evidence = context()
    evidence["input_diagnostics"] = diagnostics
    rec = run(evidence=evidence)["recommendation"]
    assert rec["decision"] == "skip" and not rec["tickets"]
    assert rec["input_verified"] is False
    assert "input_contract_unverified" in rec["reason_codes"]


@pytest.mark.parametrize("field,value", [("expected_count", 0), ("actual_count", 4),
    ("expected_count", True), ("missing_columns", ["x"]), ("blocked_columns", ["finish_time"]),
    ("issue_codes", ["feature_source_changed"]), ("source_hash_matches", False),
    ("history_available", False), ("semantic_parity_verified", False)])
def test_aligned_status_does_not_override_contradictory_or_unverified_evidence(field, value):
    from test_betting_value_policy import context, run
    evidence = context()
    evidence["input_diagnostics"][field] = value
    rec = run(evidence=evidence)["recommendation"]
    assert rec["decision"] == "skip" and not rec["tickets"]
    assert rec["input_verified"] is False
    assert "input_contract_unverified" in rec["reason_codes"]


def test_internal_context_carries_server_diagnostics(tmp_path):
    from betting.value_policy import build_live_context
    diagnostics = {"status": "degraded", "missing_columns": ["old"]}
    context = build_live_context(model_path=tmp_path / "none", predictor=SimpleNamespace(
        target="speed_deviation", input_diagnostics=diagnostics), source_records=[],
        feature_frame=pd.DataFrame({"x": [1.]}))
    assert context["input_diagnostics"] == diagnostics
