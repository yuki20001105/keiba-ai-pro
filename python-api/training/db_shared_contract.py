"""Bind the new local pipeline without altering frozen schema-v1 contracts."""
from typing import Any

from training.db_shared_features import FEATURE_COLUMNS, FEATURE_VERSION
from training.local_retrain import LocalRetrainError
from training.db_shared_profiles import OPTIMIZED_PIPELINE, SHARED_PIPELINES


def selected_feature_columns(pipeline: str) -> tuple[str, ...]:
    if pipeline == FEATURE_VERSION:
        return tuple(FEATURE_COLUMNS)
    if pipeline == OPTIMIZED_PIPELINE:
        return tuple(name for name in FEATURE_COLUMNS if name != "safe_past5_top3_rate")
    raise LocalRetrainError("local-shared-profile-unknown")


def fit_module(pipeline: str):
    if pipeline == FEATURE_VERSION:
        from training import db_shared_fit
        return db_shared_fit
    if pipeline == OPTIMIZED_PIPELINE:
        from training import db_shared_optimized
        return db_shared_optimized
    raise LocalRetrainError("local-shared-profile-unknown")


def bound_pipeline(contract: Any) -> str:
    # Column order and removals are already included in the immutable feature
    # contract SHA; implementation bytes are bound by the source-tree SHA.
    # Thus a queued worker does not trust a mutable request_json pipeline flag.
    selected = tuple(contract.selected_features)
    pipeline = next((version for version in SHARED_PIPELINES
                     if selected == selected_feature_columns(version)), None)
    if pipeline is None:
        return "legacy"
    parameters = contract.training_parameters
    if (contract.target != "speed_deviation" or contract.model_type != "lightgbm"
            or tuple(contract.removed_features)
            or parameters.get("use_optuna") is not False
            or parameters.get("use_optimizer") is not True):
        raise LocalRetrainError("local-shared-contract-invalid")
    return pipeline


def request_payload(bundle: Any) -> dict[str, object]:
    payload = bundle.training_request_payload()
    payload["feature_pipeline"] = bound_pipeline(bundle.contract)
    return payload


def verify_pipeline(contract: Any, requested_pipeline: str) -> None:
    if bound_pipeline(contract) != requested_pipeline:
        raise LocalRetrainError("local-training-pipeline-binding-mismatch")
