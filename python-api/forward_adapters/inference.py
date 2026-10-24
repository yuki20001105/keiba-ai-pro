"""Transform-only fixed H04 inference. Caller feature as-of truth is unverified.

Only trusted, externally pinned SHA-256 artifacts may be deserialized. No fit,
network, publication, database writes, or live-capture readiness is performed.
Use the existing CLI import environment: both python-api and keiba on sys.path.
"""
from io import BytesIO
import hashlib
import json
from pathlib import Path
import re

import joblib
import numpy as np
import pandas as pd

from experiments.features import ALLOWED_FEATURES
from experiments.search_features import SEARCH_FEATURES
from prospective.calibration import _normalize, transform_probabilities

METADATA = ["race_id", "race_date", "horse_id", "horse_number", "surface", "expected_runner_count"]
SAFE_FEATURES = frozenset(ALLOWED_FEATURES) | frozenset(SEARCH_FEATURES)


def _verified_bytes(path, expected):
    if not isinstance(expected, str) or not re.fullmatch(r"[0-9a-f]{64}", expected):
        raise ValueError("An externally pinned SHA-256 is required")
    body = Path(path).read_bytes()
    if hashlib.sha256(body).hexdigest() != expected:
        raise ValueError("Artifact SHA-256 mismatch; refusing deserialization")
    return body


def score_fixed_model(current_features: pd.DataFrame, model_path, calibrator_path,
                      expected_model_sha256: str, expected_calibrator_sha256: str) -> pd.DataFrame:
    """Score complete current fields using H04 fold2026 and its bound calibrator.

    Output is date/race/horse sorted metadata plus rank_score/prediction and
    win_probability. Extra input columns (including market/results) are ignored
    and never forwarded to the encoder. Missing required features fail closed;
    known feature NaNs are handled by the already-fitted encoder, never fitted.
    ``policy_ready=False`` explicitly denotes the still-unverified raw/as-of
    feature source, not a capability to place orders or certify capture input.
    """
    model_bytes = _verified_bytes(model_path, expected_model_sha256)
    calibration_bytes = _verified_bytes(calibrator_path, expected_calibrator_sha256)
    frozen = json.loads(calibration_bytes)
    provenance = frozen.get("model_provenance", {})
    if (provenance.get("model_id") != "H04"
            or provenance.get("model_artifact_sha256_by_year", {}).get("2026") != expected_model_sha256
            or frozen.get("frozen_before_later_diagnostics") is not True):
        raise ValueError("Calibrator is not bound to the fixed H04 fold2026 model")
    if not isinstance(current_features, pd.DataFrame) or not set(METADATA).issubset(current_features):
        raise ValueError("Current canonical race/runner metadata is required")
    if (current_features.race_id.isna().any()
            or not current_features.race_id.astype(str).str.fullmatch(r"\d{12}").all()
            or not current_features.race_date.astype(str).str.fullmatch(r"\d{8}").all()):
        raise ValueError("Canonical race ID and YYYYMMDD date required")
    frame = _normalize(current_features.assign(prediction=0.0))
    if (not frame.race_id.str[:4].eq(frame.race_date.str[:4]).all()
            or not frame.race_id.str[4:6].astype(int).between(1, 10).all()
            or not frame.surface.isin(["turf", "dirt"]).all()):
        raise ValueError("Only canonical JRA flat race fields are supported")
    # Load the exact bytes that were verified, eliminating a path replacement
    # between the hash check and pickle deserialization.
    artifact = joblib.load(BytesIO(model_bytes))
    contract = artifact.get("contract", {})
    if (contract.get("schema") != "isolated-feature-search-trial-v1"
            or contract.get("target") != "rank_regression" or contract.get("loss") != "huber"
            or contract.get("evaluation_year") != 2026 or contract.get("score_is_probability") is not False
            or contract.get("validation_used_for_fit_or_early_stopping") is not False
            or contract.get("market_features") is not False):
        raise ValueError("Not the fixed safe H04 fold2026 contract")
    period = contract.get("train_period", [])
    if (len(period) != 2 or period != ["20160101", "20251231"]
            or not frame.race_date.gt(period[1]).all()
            or not frame.race_date.gt(frozen["calibrator"]["fit_date_to"]).all()):
        raise ValueError("Current dates must be after model and calibrator training")
    columns, encoder, model = contract.get("feature_columns", []), artifact["encoder"], artifact["model"]
    if (len(columns) != 99 or len(set(columns)) != 99 or not set(columns).issubset(SAFE_FEATURES)
            or artifact.get("feature_columns") != columns or contract.get("requested_features") != columns
            or encoder.feature_columns != columns or encoder.requested_columns != columns
            or model.feature_name() != columns or model.num_feature() != 99):
        raise ValueError("Fixed 99-feature model/encoder contract mismatch")
    if not set(columns).issubset(frame):
        raise ValueError("Required fixed-model feature columns are missing")
    encoded = encoder.transform(frame.loc[:, columns])
    if list(encoded.columns) != columns or len(encoded) != len(frame):
        raise ValueError("Encoder changed the fixed feature/cohort shape")
    scores = np.asarray(model.predict(encoded, num_threads=2), dtype=float)
    if scores.shape != (len(frame),) or not np.isfinite(scores).all():
        raise ValueError("Nonfinite or incomplete model scores")
    output = frame.loc[:, METADATA].copy()
    output["prediction"] = scores
    output = transform_probabilities(output, frozen)
    output["rank_score"] = output["prediction"]
    output["model_sha256"] = expected_model_sha256
    output["frozen_calibrator_sha256"] = expected_calibrator_sha256
    output["policy_ready"] = False
    output["raw_asof_features_verified"] = False
    return output
