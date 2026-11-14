"""New learning default; old artifacts retain their saved prediction schema."""
from training import db_context_registry as previous
from training.db_context_registry import (
    CONTEXT_PIPELINE, SHARED_PIPELINES, bound_pipeline, request_payload,
    selected_feature_columns, verify_pipeline,
)
from training.db_shared_profiles import OPTIMIZED_PIPELINE


def fit_module(pipeline):
    if pipeline == OPTIMIZED_PIPELINE:
        from training import db_shared_grouped
        return db_shared_grouped
    return previous.fit_module(pipeline)


def prediction_module(bundle):
    from training.db_shared_grouped import SCHEMA
    if bundle.get("schema") == SCHEMA:
        return fit_module(OPTIMIZED_PIPELINE)
    return previous.fit_module(bundle["feature_pipeline"])
