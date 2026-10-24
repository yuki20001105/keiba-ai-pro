"""Local, non-purchase serving of the frozen 2026 winner-NLL research model.

Publication does not upgrade the historical final-odds experiment to a validated
live strategy. Every request remains reference-only, including when current DB
rows have observation timestamps. Original artifacts and formula sources are
hash checked, and current outcomes never enter the frozen input equations.
"""
from __future__ import annotations

from datetime import datetime, timedelta
from functools import lru_cache
import hashlib
import importlib
import json
from pathlib import Path
import sys
from threading import RLock

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[2]
PIPELINE = "local-win-nll-reference-v1"
PREFIX = "model_win_reference_"
MODEL_FILENAME = PREFIX + "nll46_2026_20261012.joblib"
SCHEMA = "local-win-nll-reference-package-v1"
ORIGIN_PLAN = "reports/generated/win-shadow-v1/registered-plan.json"
ORIGIN_PLAN_SHA256 = "bdb583fb1789078bfb131841b5042b0f1effbc3bf2d0f63aab1a06d69d8d82ba"
METADATA = ["race_id", "race_date", "horse_number", "horse_id", "venue", "surface", "num_horses", "expected_runner_count", "odds"]
_LOCK = RLock()
_STATE_CACHE = {}


def file_hash(path):
    digest = hashlib.sha256()
    with Path(path).open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


_IMPORTED_RUNTIME_SOURCES = {relative: file_hash(ROOT / relative) for relative in
    ("python-api/services/win_nll_reference_input.py", "scripts/publish_win_nll_reference.py")}


def runtime_sources():
    for relative, expected in _IMPORTED_RUNTIME_SOURCES.items():
        if file_hash(ROOT / relative) != expected:
            raise ValueError("win-reference-runtime-source-changed-restart-required")
    return dict(_IMPORTED_RUNTIME_SOURCES)


def registered_origin():
    """Fixed original preregistration, not a candidate supplied by a manifest."""
    path = _relative_path(ORIGIN_PLAN)
    if file_hash(path) != ORIGIN_PLAN_SHA256:
        raise ValueError("win-reference-fixed-origin-plan-hash-mismatch")
    plan = json.loads(path.read_text(encoding="utf-8"))
    artifacts = {name: {"path": plan["artifacts"][key]["path"], "sha256": plan["artifacts"][key]["sha256"]}
        for name, key in (("model", "model"), ("worker_result", "worker_result"), ("worker_request", "worker_request"), ("v3_results", "v3_summary"))}
    artifacts["shadow_plan"] = {"path": ORIGIN_PLAN, "sha256": ORIGIN_PLAN_SHA256}
    sources = plan["source_hashes"] | plan["shadow_source_hashes"] | plan["aggregation_source_hashes"]
    return artifacts, sources


def modules():
    # The original TrainEncoder's qualified module name must be importable only
    # when loading its independently sealed research bundle, never normal lists.
    scripts = str(ROOT / "scripts")
    if scripts not in sys.path:
        sys.path.insert(0, scripts)
    return (importlib.import_module("db_shared_win_four_worker"),
            importlib.import_module("db_shared_win_direct_features"))


def probability_validation():
    return {"schema": "local-win-nll-reference-validation-v1", "status": "unverified",
            "calibrated": True, "validated": False, "eligible_for_ev": False,
            "market_edge_verified": False, "profitability_verified": False,
            "reference_only": True, "eligible_bet_types": [],
            "reason_codes": ["historical_final_odds_to_prestart_transfer_unverified", "unknown_period_validation_pending", "selected_calibration_unverified"]}


def _relative_path(relative):
    path = ROOT / str(relative)
    if path.is_symlink() or path.resolve(strict=True).relative_to(ROOT).as_posix() != relative:
        raise ValueError("win-reference-bound-path-invalid")
    return path


def _check_receipt(receipt):
    if not isinstance(receipt, dict) or set(receipt) != {"path", "sha256"}:
        raise ValueError("win-reference-artifact-receipt-required")
    path = _relative_path(receipt["path"])
    if file_hash(path) != receipt["sha256"]:
        raise ValueError("win-reference-source-artifact-hash-mismatch")
    return path


def _check_sources(sources):
    if not isinstance(sources, dict) or not sources:
        raise ValueError("win-reference-frozen-source-pins-required")
    for relative, expected in sources.items():
        if file_hash(_relative_path(relative)) != expected:
            raise ValueError("win-reference-frozen-source-pin-mismatch:" + relative)


def manifest_path(path):
    return Path(path).with_suffix(".reference-manifest.json")


def validate_published_artifact(path):
    """Validate local publication and all source hashes before unpickling."""
    from app_config import APP_ENV, MODELS_DIR
    path = Path(path)
    resolved = path.resolve(strict=True)
    if APP_ENV not in {"local", "development", "test"}:
        raise ValueError("win-reference-local-only")
    if path.is_symlink() or resolved.parent != MODELS_DIR.resolve() or resolved.name != MODEL_FILENAME:
        raise ValueError("win-reference-published-path-invalid")
    sidecar = manifest_path(resolved)
    if sidecar.is_symlink():
        raise ValueError("win-reference-manifest-symlink")
    manifest = json.loads(sidecar.read_text(encoding="utf-8"))
    digest = file_hash(resolved)
    if (manifest.get("schema") != SCHEMA or manifest.get("model_id") != resolved.stem
            or manifest.get("wrapper_sha256") != digest or manifest.get("reference_only") is not True
            or manifest.get("purchase_authorized") is not False or manifest.get("new_fits") != 0):
        raise ValueError("win-reference-publication-manifest-invalid")
    original_artifacts, frozen_sources = registered_origin()
    if (manifest.get("original_artifacts") != original_artifacts
            or manifest.get("frozen_source_hashes") != frozen_sources
            or manifest.get("runtime_source_hashes") != runtime_sources()):
        raise ValueError("win-reference-fixed-origin-publication-mismatch")
    _check_sources(manifest.get("frozen_source_hashes"))
    _check_sources(manifest.get("runtime_source_hashes"))
    for receipt in manifest.get("original_artifacts", {}).values():
        _check_receipt(receipt)
    if set(manifest.get("original_artifacts", {})) != {"model", "worker_result", "worker_request", "shadow_plan", "v3_results"}:
        raise ValueError("win-reference-original-artifact-binding-required")
    return digest


@lru_cache(maxsize=2)
def _original_bundle(relative, expected):
    import joblib
    worker, _features = modules()
    path = _check_receipt({"path": relative, "sha256": expected})
    bundle = joblib.load(path)
    worker.validate_bundle(bundle)
    if bundle.get("candidate_id") != "expanding_race_nll" or bundle.get("fold_year") != 2026:
        raise ValueError("win-reference-frozen2026-candidate-required")
    return bundle


def verify_bundle_sources(bundle):
    worker, _features = modules()
    columns, categorical = worker.feature_contract("expanding_race_nll")
    if (not isinstance(bundle, dict) or bundle.get("schema") != SCHEMA
            or bundle.get("feature_pipeline") != PIPELINE or bundle.get("target") != "win"
            or bundle.get("_win_nll_reference") is not True or bundle.get("reference_only") is not True
            or bundle.get("purchase_authorized") is not False
            or bundle.get("probability_validation") != probability_validation()
            or bundle.get("feature_columns") != columns or bundle.get("categorical_columns") != categorical
            or "encoder" in bundle or bundle.get("calibrator") is not None):
        raise ValueError("win-reference-nonpurchase-wrapper-contract-required")
    original_artifacts, frozen_sources = registered_origin()
    if (bundle.get("frozen_source_hashes") != frozen_sources
            or bundle.get("runtime_source_hashes") != runtime_sources()
            or {"path": bundle.get("source_bundle_path"), "sha256": bundle.get("source_bundle_sha256")} != original_artifacts["model"]):
        raise ValueError("win-reference-wrapper-fixed-origin-mismatch")
    _check_sources(bundle.get("frozen_source_hashes"))
    _check_sources(bundle.get("runtime_source_hashes"))
    original = _original_bundle(bundle["source_bundle_path"], bundle["source_bundle_sha256"])
    if (bundle.get("conditioned_calibration") != original["conditioned_calibration"]
            or bundle.get("model_text_sha256") != original["model_text_sha256"]
            or bundle.get("encoder_sha256") != original.get("encoder_sha256")):
        raise ValueError("win-reference-original-model-calibration-binding-mismatch")
    model = bundle.get("model")
    if model is None or model.num_feature() != 50 or model.feature_name() != columns:
        raise ValueError("win-reference-published-model-feature-mismatch")


def validate_wrapper(bundle, model_path):
    digest = validate_published_artifact(model_path)
    verify_bundle_sources(bundle)
    manifest = json.loads(manifest_path(model_path).read_text(encoding="utf-8"))
    if (manifest["original_artifacts"]["model"] != {"path": bundle["source_bundle_path"], "sha256": bundle["source_bundle_sha256"]}
            or manifest["frozen_source_hashes"] != bundle["frozen_source_hashes"]
            or manifest["runtime_source_hashes"] != bundle["runtime_source_hashes"]):
        raise ValueError("win-reference-wrapper-manifest-binding-mismatch")
    return digest


def load_published_bundle(path):
    """Normal model admission: verify its publisher before deserializing."""
    import joblib
    validate_published_artifact(path)
    bundle = joblib.load(path)
    validate_wrapper(bundle, path)
    return bundle


def _db_fingerprint(path):
    value = []
    for candidate in (Path(path).resolve(strict=True), Path(str(Path(path).resolve(strict=True)) + "-wal")):
        stat = candidate.stat() if candidate.exists() else None
        value.append((str(candidate), stat.st_size if stat else None, stat.st_mtime_ns if stat else None))
    return tuple(value)


def _history_state(db_path, day):
    from forward_adapters import feature_state
    from training import db_shared_features as normal
    key = (_db_fingerprint(db_path), day)
    with _LOCK:
        if key in _STATE_CACHE:
            return _STATE_CACHE[key], True
        prior = (datetime.strptime(day, "%Y%m%d") - timedelta(days=1)).strftime("%Y%m%d")
        audit = normal._audit()
        with normal._database(db_path) as (connection, tables):
            metadata = normal._target_metadata(connection, "20130101", prior, audit)
            history, selected = normal._load_complete(connection, tables, metadata, audit)
        state = feature_state.build_history_state(history, target_date=day)
        state["load_audit"] = {**audit, **selected, "source": "full_observed_eligible_JRA_flat_history_readonly"}
        # A live writer may update during our coherent read snapshot. That state
        # remains a reference snapshot but is not reused under the new DB key.
        if _db_fingerprint(db_path) == key[0]:
            _STATE_CACHE.clear()
            _STATE_CACHE[key] = state
        return state, False


def build_input(bundle, horses, *, db_path, race_info, model_path, historical_frame=None):
    """Frozen 46 + same whole-field market4; returned rows preserve caller order."""
    validate_wrapper(bundle, model_path)
    worker, features = modules()
    from training import db_shared_features as normal
    from forward_adapters import feature_state
    source = horses.copy(deep=True) if isinstance(horses, pd.DataFrame) else pd.DataFrame(horses)
    if source.empty or source.columns.duplicated().any() or source.index.has_duplicates:
        raise ValueError("win-reference-complete-unique-current-card-required")
    current = normal._current_card(race_info, source)
    if current.race_id.nunique() != 1:
        raise ValueError("win-reference-one-current-race-required")
    day = str(current.race_date.iloc[0])
    if day <= "20251231":
        raise ValueError("win-reference-target-must-follow-training-and-calibration")
    if historical_frame is None:
        state, cache_hit = _history_state(db_path, day)
    else:
        state = feature_state.build_history_state(historical_frame.copy(deep=True), target_date=day)
        cache_hit = False
    generated = feature_state.build_current_features(state, current)
    # Frozen generation sorts cards. Restore the explicit horse key mapping,
    # never positional source alignment, including non-default caller indexes.
    ordered = generated.set_index("horse_number").loc[current.horse_number.tolist()].reset_index()
    ordered.index = source.index
    raw = ordered.loc[:, list(features.BASE46_COLUMNS)].copy()
    for name in METADATA:
        if name == "odds":
            raw[name] = pd.to_numeric(source.get("odds", pd.Series(np.nan, index=source.index)), errors="coerce")
        elif name == "expected_runner_count":
            raw[name] = current.num_horses.to_numpy()
        else:
            raw[name] = current[name].to_numpy()
    raw = features.add_market_features(raw)
    columns = bundle["feature_columns"]
    raw = raw.loc[:, columns + METADATA]
    worker.validate_frame(raw, require_results=False)
    required = ["safe_distance", "safe_num_horses", "safe_horse_number", "safe_age", "safe_burden_weight"]
    if not np.isfinite(raw[required].to_numpy(float)).all():
        raise ValueError("win-reference-required-current-feature-missing")
    historical_odds = any(row.get("odds_status") in {"result", "settled"} or any(row.get(name) not in (None, "", 0, "0")
                          for name in ("finish", "finish_position", "finish_time", "time_seconds")) for row in source.to_dict("records"))
    maximum = state["state"]["history_max_source_date"]
    diagnostics = {"schema": "prediction-input-contract-v1", "feature_pipeline": PIPELINE,
        "scope": PIPELINE, "status": "unverified", "reference_only": True,
        "expected_count": 50, "actual_count": 50, "missing_columns": [], "blocked_columns": [], "uncomputed_columns": [],
        "issue_codes": ["win_nll_reference_only", "prestart_market_transfer_unverified"],
        "source_hash_matches": True, "history_available": True, "semantic_parity_verified": False,
        "formula_source_verified": True, "live_capture_hash_bound": False, "card_capture_time_verified": False,
        "input_outcomes_consumed": False, "current_outcomes_consumed": False, "same_day_or_future_history_used": False,
        "calibration_verified": False, "profitability_verified": False, "eligible_for_ev": False,
        "all_missing_columns": [name for name in columns if raw[name].isna().all()],
        "missing_value_rate": float(raw[columns].isna().to_numpy().mean()),
        "history_max_source_date": maximum, "history_rows": state["state"]["history_row_count"],
        "history_lag_days": (datetime.strptime(day, "%Y%m%d") - datetime.strptime(maximum, "%Y%m%d")).days if maximum else None,
        "history_freshness_verified": False, "complete_career_coverage_verified": False,
        "history_cache_hit": cache_hit, "source_odds_kind": "historical_or_unverified" if historical_odds else "unverified_current_quote",
        "input_receipt": {"verified": False, "reason": "win_nll_reference_only"}}
    raw.attrs["win_reference_input_sha256"] = worker.frame_hash(raw)
    return raw, diagnostics


def predict(bundle, frame, *, model_path):
    validate_wrapper(bundle, model_path)
    worker, _features = modules()
    if (not isinstance(frame, pd.DataFrame) or frame.columns.duplicated().any()
            or list(frame) != bundle["feature_columns"] + METADATA
            or frame.attrs.get("win_reference_input_sha256") != worker.frame_hash(frame)):
        raise ValueError("win-reference-bound-outcome-free-input-required")
    if not frame.race_date.astype(str).gt("20251231").all():
        raise ValueError("win-reference-target-must-follow-training-and-calibration")
    original = _original_bundle(bundle["source_bundle_path"], bundle["source_bundle_sha256"])
    result = worker.predict_components(original, frame)
    return {"probability": result["probability"], "raw_probability": result["p_original"],
            "raw_score": result["direct_logit"]}
