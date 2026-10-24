"""Add an opt-in context profile without rewriting frozen shared contracts.

Old 47/46 profiles delegate to their existing registry. The new profile is
bound by its exact ordered columns, not a mutable request label. This module
does not train, load a model, publish or activate an artifact.
"""
from __future__ import annotations

from training import db_shared_contract as legacy
from training.db_shared_profiles import SHARED_PIPELINES as LEGACY_SHARED_PIPELINES
from training.local_retrain import LocalRetrainError


CONTEXT_PIPELINE = "local-db-context-v1"
SHARED_PIPELINES = LEGACY_SHARED_PIPELINES | frozenset({CONTEXT_PIPELINE})


def selected_feature_columns(pipeline: str) -> tuple[str, ...]:
    if pipeline == CONTEXT_PIPELINE:
        from training.db_context_features import FEATURE_COLUMNS
        return tuple(FEATURE_COLUMNS)
    return legacy.selected_feature_columns(pipeline)


def fit_module(pipeline: str):
    if pipeline == CONTEXT_PIPELINE:
        from training import db_context_fit
        return db_context_fit
    return legacy.fit_module(pipeline)


def bound_pipeline(contract) -> str:
    # Preserve the original validation/error behavior for all old contracts.
    previous = legacy.bound_pipeline(contract)
    if previous != "legacy":
        return previous
    if len(tuple(contract.selected_features)) <= 47:
        return "legacy"
    if tuple(contract.selected_features) != selected_feature_columns(CONTEXT_PIPELINE):
        return "legacy"
    parameters = contract.training_parameters
    if (contract.target != "speed_deviation" or contract.model_type != "lightgbm"
            or tuple(contract.removed_features)
            or parameters.get("use_optuna") is not False
            or parameters.get("use_optimizer") is not True):
        raise LocalRetrainError("local-context-contract-invalid")
    return CONTEXT_PIPELINE


def request_payload(bundle) -> dict[str, object]:
    payload = bundle.training_request_payload()
    payload["feature_pipeline"] = bound_pipeline(bundle.contract)
    return payload


def verify_pipeline(contract, requested_pipeline: str) -> None:
    if bound_pipeline(contract) != requested_pipeline:
        raise LocalRetrainError("local-training-pipeline-binding-mismatch")
