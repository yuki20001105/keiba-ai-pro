from pathlib import Path


def test_legacy_feature_source_mismatch_is_reported_and_never_ready(monkeypatch, tmp_path):
    from betting import value_policy
    from services import prediction_input_contract
    from services.model_readiness import assess_model_readiness

    monkeypatch.setattr(prediction_input_contract, "current_feature_hash", lambda: "runtime-hash")
    monkeypatch.setattr(value_policy, "load_probability_validation", lambda *args, **kwargs: {
        "validated": True, "eligible_bet_types": ["単勝"], "profitability_verified": False,
    })
    result = assess_model_readiness(
        tmp_path / "legacy.joblib",
        {"target": "win", "pipeline_config": {"feature_engineering_hash": "trained-hash"},
         "calibrator": object()},
    )

    assert result["feature_compatibility"] == "mismatch"
    assert result["win_probability_validated"] is True
    assert result["model_betting_ready"] is False
    assert "feature_source_hash_mismatch" in result["reason_codes"]
    assert "pre_start_market_odds" in result["race_evidence_required"]
    assert result["profitability_verified"] is False


def test_matching_legacy_formula_does_not_claim_win_probability_validation(monkeypatch, tmp_path):
    from betting import value_policy
    from services import prediction_input_contract
    from services.model_readiness import assess_model_readiness

    monkeypatch.setattr(prediction_input_contract, "current_feature_hash", lambda: "same-hash")
    monkeypatch.setattr(value_policy, "load_probability_validation", lambda *args, **kwargs: {
        "validated": False, "eligible_bet_types": [],
    })
    result = assess_model_readiness(
        tmp_path / "legacy.joblib",
        {"target": "speed_deviation", "pipeline_config": {"feature_engineering_hash": "same-hash"}},
    )

    assert result["feature_compatibility"] == "verified"
    assert result["win_probability_validated"] is False
    assert result["model_betting_ready"] is False
    assert "win_probability_not_validated" in result["reason_codes"]


def test_shared_readiness_requires_exact_source_and_embedded_validation(monkeypatch, tmp_path):
    from services import db_shared_input
    from services.model_readiness import assess_model_readiness
    from training.db_shared_profiles import OPTIMIZED_PIPELINE

    monkeypatch.setattr(db_shared_input, "verify_bundle_sources", lambda _: None)
    monkeypatch.setattr(db_shared_input, "validate_published_artifact", lambda _: "fixture-digest")
    monkeypatch.setattr(db_shared_input, "embedded_probability_report", lambda *_: {
        "validated": True, "eligible_bet_types": ["単勝"], "profitability_verified": False,
    })
    result = assess_model_readiness(
        tmp_path / "shared.joblib",
        {"feature_pipeline": OPTIMIZED_PIPELINE, "target": "speed_deviation"},
    )

    assert result["feature_compatibility"] == "verified"
    assert result["win_probability_validated"] is True
    assert result["eligible_bet_types"] == ["単勝"]
    assert result["model_betting_ready"] is True
    assert result["profitability_verified"] is False
