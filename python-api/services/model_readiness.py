"""Read-only model readiness facts for prediction selection.

This reports static artifact/source validation only. It never declares a race
ready to bet: live input receipts, feature missingness and current market odds
are still checked independently for every prediction.
"""
from __future__ import annotations

from pathlib import Path
from typing import Any


def assess_model_readiness(model_path: Path, bundle: dict[str, Any]) -> dict[str, Any]:
    from training.db_context_registry import SHARED_PIPELINES

    if bundle.get("_monthly_reference"):
        from services.monthly_reference_input import validate_published_artifact
        try:
            verified = validate_published_artifact(model_path) == bundle.get("_shared_artifact_sha256")
        except (OSError, ValueError, TypeError):
            verified = False
        return {
            "schema": "prediction-model-readiness-v1",
            "feature_compatibility": "verified" if verified else "mismatch",
            "win_probability_validated": False, "eligible_bet_types": [], "model_betting_ready": False,
            "reason_codes": ["reference_only", "win_probability_not_validated", "no_bet_type_validated"],
            "race_evidence_required": ["input_contract", "feature_completeness", "pre_start_market_odds", "odds_observed_at"],
            "profitability_verified": False,
        }
    pipeline = bundle.get("feature_pipeline")
    reasons: list[str] = []
    if pipeline in SHARED_PIPELINES:
        try:
            # Verifies the artifact's exact training-source contract. Older
            # pinned sources may be recovered from repository history, but no
            # current-race data is inferred here.
            from services.db_shared_input import verify_bundle_sources
            verify_bundle_sources(bundle)
            feature_status = "verified"
        except (OSError, ValueError, TypeError):
            feature_status = "mismatch"
            reasons.append("feature_source_unavailable_or_mismatched")
    else:
        config = bundle.get("pipeline_config") or {}
        training_hash = config.get("feature_engineering_hash") if isinstance(config, dict) else None
        try:
            from services.prediction_input_contract import current_feature_hash
            runtime_hash = current_feature_hash()
        except (OSError, TypeError):
            runtime_hash = None
        if not training_hash or not runtime_hash:
            feature_status = "unverified"
            reasons.append("feature_source_hash_missing")
        elif training_hash != runtime_hash:
            feature_status = "mismatch"
            reasons.append("feature_source_hash_mismatch")
        else:
            # A matching formula hash is useful, but legacy input semantics
            # still need per-race verification in the serving diagnostics.
            feature_status = "verified"

    try:
        if pipeline in SHARED_PIPELINES:
            from services.db_shared_input import embedded_probability_report, validate_published_artifact
            # The bundle is loaded by the listing route, not by the serving
            # loader, so bind its embedded report to the verified on-disk
            # publisher digest before asking the shared validator to inspect it.
            verified_bundle = dict(bundle)
            verified_bundle["_shared_artifact_sha256"] = validate_published_artifact(model_path)
            report = embedded_probability_report(verified_bundle, model_path)
        else:
            from betting.value_policy import load_probability_validation
            report = load_probability_validation(
                model_path, target=str(bundle.get("target") or ""),
                calibrated=bundle.get("calibrator") is not None,
            )
    except (OSError, ValueError, TypeError):
        report = {"validated": False, "eligible_bet_types": []}

    probability_validated = report.get("validated") is True
    eligible_bet_types = report.get("eligible_bet_types", []) if probability_validated else []
    if not probability_validated:
        reasons.append("win_probability_not_validated")
    if not eligible_bet_types:
        reasons.append("no_bet_type_validated")

    return {
        "schema": "prediction-model-readiness-v1",
        "feature_compatibility": feature_status,
        "win_probability_validated": probability_validated,
        "eligible_bet_types": list(eligible_bet_types),
        "model_betting_ready": feature_status == "verified" and probability_validated and bool(eligible_bet_types),
        "reason_codes": reasons,
        "race_evidence_required": ["input_contract", "feature_completeness", "pre_start_market_odds", "odds_observed_at"],
        "profitability_verified": report.get("profitability_verified") is True,
    }
