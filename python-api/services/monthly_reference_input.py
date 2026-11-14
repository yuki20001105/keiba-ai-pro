"""Sealed monthly-study models for explicit, non-purchasing app comparison."""
from __future__ import annotations

from copy import deepcopy
from functools import lru_cache
import hashlib
from io import BytesIO
import json
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile

import joblib
import pandas as pd

from app_config import APP_ENV, MODELS_DIR

ROOT = Path(__file__).resolve().parents[2]
STUDY_ROOT = ROOT / "reports" / "generated" / "model-experiments" / "monthly-validation-20261105-v1"
STUDY_SHA256 = "e5d0954bc7034ce64f692b735cfa4e23b59314a5c42083a59efef392ee8d80f7"
PREFIX = "model_monthly_reference_"
PIPELINE = "monthly-validation-research-model-v1"
_SPECS = (
    {"model_id": PREFIX + "random_20261105", "directory": "monthly_random",
     "display_name": "月別seed42・46特徴量（参考運用・2026/10まで学習）",
     "sha256": "f845013ddfe15ea9dc7d65109d969a07684b58ec4fb7d0cfa8bdc09dc93c5f5d"},
    {"model_id": PREFIX + "forward_2026_20261105", "directory": "forward_2026",
     "display_name": "年次前進・46特徴量（参考運用・2026年TEST）",
     "sha256": "723643e79793ebf22ef07690ec9a1b23800cee942545058320c8cd2b0ad5037b"},
)


def specs():
    return deepcopy(list(_SPECS))


def _sha(body):
    return hashlib.sha256(body).hexdigest()


def _study():
    body = (STUDY_ROOT / "study-summary.json").read_bytes()
    if _sha(body) != STUDY_SHA256:
        raise ValueError("monthly-reference-study-receipt-mismatch")
    return json.loads(body)


def verified_file(relative):
    """Only study-manifest members, never caller-selected pickle paths."""
    manifest = _study()
    key = str(Path(relative))
    expected = manifest["artifact_sha256"].get(key)
    if expected is None:
        raise ValueError("monthly-reference-unregistered-study-file")
    path = STUDY_ROOT / key
    body = path.read_bytes()
    if path.is_symlink() or _sha(body) != expected:
        raise ValueError("monthly-reference-study-file-mismatch")
    return path


def frozen_sources():
    result = {}
    for module, digest in _study()["source_hashes"].items():
        path = STUDY_ROOT / "runtime-sources" / (digest + ".py")
        body = path.read_bytes()
        if path.is_symlink() or _sha(body) != digest:
            raise ValueError("monthly-reference-frozen-source-mismatch")
        result[module] = body
    return result


def _runtime_hashes():
    return {name: _sha((Path(__file__).parent / name).read_bytes())
            for name in ("monthly_reference_input.py", "monthly_reference_worker.py")}


def _receipt(spec):
    return {"schema": "monthly-reference-publication-v1", "model_id": spec["model_id"],
            "artifact_sha256": spec["sha256"], "study_sha256": STUDY_SHA256,
            "runtime_source_sha256": _runtime_hashes(),
            "reference_only": True, "purchase_authorized": False}


def validate_published_artifact(path):
    path = Path(path)
    spec = next((s for s in _SPECS if s["model_id"] == path.stem), None)
    if (APP_ENV not in {"development", "local", "test"} or spec is None
            or path.resolve().parent != MODELS_DIR.resolve()
            or path.is_symlink() or not path.is_file() or _sha(path.read_bytes()) != spec["sha256"]):
        raise ValueError("monthly-reference-untrusted-artifact")
    receipt = path.with_suffix(".reference-manifest.json")
    if receipt.is_symlink() or json.loads(receipt.read_text(encoding="utf-8")) != _receipt(spec):
        raise ValueError("monthly-reference-publication-receipt-mismatch")
    frozen_sources()
    return spec["sha256"]


@lru_cache(maxsize=2)
def _decode(digest, body):
    if _sha(body) != digest:
        raise ValueError("monthly-reference-artifact-changed")
    bundle = joblib.load(BytesIO(body))
    if (bundle.get("schema") != PIPELINE or bundle.get("feature_pipeline") != PIPELINE
            or bundle.get("source_hashes") != _study()["source_hashes"]
            or bundle.get("reference_only") is not True
            or bundle.get("probability_validation", {}).get("validated") is not False
            or bundle.get("probability_validation", {}).get("eligible_for_ev") is not False):
        raise ValueError("monthly-reference-model-contract-mismatch")
    return bundle


def _admitted_bundle(path, spec):
    body = Path(path).read_bytes()
    if Path(path).is_symlink() or _sha(body) != spec["sha256"]:
        raise ValueError("monthly-reference-untrusted-artifact")
    frozen_sources()
    original = _decode(spec["sha256"], body)
    roles = pd.read_csv(verified_file(Path(spec["directory"]) / "membership.csv"), dtype=str)
    members = original["partition_membership"]
    if (roles.race_id.duplicated().any()
            or any(set(roles.loc[roles.partition.eq(name)].race_id) != set(members[name])
                   for name in ("TRAIN", "CAL", "TEST"))
            or set(members["TEST"]) & set(members["TRAIN"] + members["CAL"])):
        raise ValueError("monthly-reference-membership-mismatch")
    test = roles.loc[roles.partition.eq("TEST")]
    test_dates = dict(zip(test.race_id, pd.to_datetime(test.race_date, format="%Y%m%d").dt.strftime("%Y-%m-%d")))
    return {**original, "model_id": spec["model_id"], "display_name": spec["display_name"],
            "dataset_period_end": str(roles.race_date.max()),
            "_shared_artifact_sha256": spec["sha256"],
            "_monthly_reference": {
                "partition_sha256": original["partition_sha256"], "test_dates": test_dates,
                "development_races": frozenset(members["TRAIN"] + members["CAL"]),
            }}


def load_source_bundle(spec):
    registered = next((s for s in _SPECS if s == spec), None)
    if registered is None:
        raise ValueError("monthly-reference-unregistered-source")
    return _admitted_bundle(verified_file(Path(spec["directory"]) / "model.joblib"), spec)


def load_published_bundle(path):
    validate_published_artifact(path)
    spec = next(s for s in _SPECS if s["model_id"] == Path(path).stem)
    bundle = _admitted_bundle(path, spec)
    validate_published_artifact(path)
    return bundle


def publish():
    """Copy exact fitted bytes; no refit, default switch or purchase approval."""
    if APP_ENV not in {"development", "local", "test"}:
        raise ValueError("monthly-reference-local-publication-required")
    for spec in _SPECS:
        load_source_bundle(spec)
        path = MODELS_DIR / (spec["model_id"] + ".joblib")
        if path.exists() and _sha(path.read_bytes()) != spec["sha256"]:
            raise ValueError("monthly-reference-refusing-artifact-overwrite")
        if not path.exists():
            shutil.copyfile(STUDY_ROOT / spec["directory"] / "model.joblib", path)
        path.with_suffix(".reference-manifest.json").write_text(
            json.dumps(_receipt(spec), ensure_ascii=False, indent=2), encoding="utf-8")
        validate_published_artifact(path)


def list_reference_models(*, models_dir=None):
    models = []
    for spec in _SPECS:
        path = Path(models_dir if models_dir is not None else MODELS_DIR) / (spec["model_id"] + ".joblib")
        try:
            bundle = load_published_bundle(path)
            models.append({"model_id": spec["model_id"], "model_path": str(path),
                "display_name": spec["display_name"], "target": "speed_deviation", "model_type": "lightgbm",
                "ultimate_mode": True, "reference_only": True, "can_activate": False, "can_delete": False,
                "feature_count": len(bundle["feature_columns"]), "feature_pipeline": PIPELINE, "is_active": False,
                "training_date_from": bundle["training_date_from"], "training_date_to": bundle["training_date_to"],
                "n_rows": bundle["data_count"], "race_count": bundle["race_count"], "evaluation": bundle["evaluation"],
                "probability_validation": bundle["probability_validation"], "artifact_sha256": spec["sha256"],
                "prediction_readiness": {"feature_compatibility": "verified", "win_probability_validated": False,
                    "eligible_bet_types": [], "model_betting_ready": False, "profitability_verified": False}})
        except (OSError, ValueError, KeyError, TypeError):
            continue
    return models


def diagnostics(bundle, *, missing_value_rate=0.0, no_history_rows=0):
    return {"schema": "prediction-input-contract-v1", "status": "unverified",
            "feature_pipeline": PIPELINE, "reference_only": True,
            "feature_source_hashes": bundle["feature_source_hashes"], "source_hash_matches": True,
            "semantic_parity_verified": True, "current_outcomes_consumed": False, "history_available": True,
            "same_day_or_future_history_used": False,
            "heldout_outcomes_consumed": False if bundle["split_kind"] == "monthly_random" else None,
            "expected_count": len(bundle["feature_columns"]), "actual_count": len(bundle["feature_columns"]),
            "missing_columns": [], "blocked_columns": [], "missing_value_rate": missing_value_rate,
            "no_history_rows": no_history_rows, "issue_codes": ["pre_race_db_capture_unverified"],
            "evaluation_membership_sha256": bundle["partition_sha256"],
            "validation_split_kind": bundle["split_kind"],
            "coverage_semantics": "Observed eligible past races; no same-day or current outcomes.",
            "live_capture_hash_bound": False}


def run_prediction(bundle, horses, *, db_path, race_info, model_path, n_threads=2):
    from services.trained_model_runtime import _OPTIMIZED_MODULES, _source_path

    digest = validate_published_artifact(model_path)
    sources = frozen_sources()
    with tempfile.TemporaryDirectory(prefix="keiba-monthly-reference-") as directory:
        snapshot = Path(directory)
        packages = set()
        for module in sorted(_OPTIMIZED_MODULES | {"training.monthly_validation"}):
            relative = _source_path(module)
            target = snapshot / relative
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(sources[module])
            packages.add(relative.parent)
        for package in packages:
            (snapshot / package / "__init__.py").write_text(
                f"__path__.append({str(ROOT / package)!r})\n", encoding="utf-8")
        request, response = snapshot / "request.joblib", snapshot / "response.joblib"
        joblib.dump({"root": str(ROOT), "snapshot": str(snapshot), "model_path": str(Path(model_path).resolve()),
                     "artifact_sha256": digest, "db_path": str(Path(db_path).resolve()),
                     "race_info": race_info, "horses": horses, "n_threads": n_threads}, request)
        result = subprocess.run([sys.executable, "-I", str(Path(__file__).with_name("monthly_reference_worker.py")),
                                 str(request), str(response)], capture_output=True, timeout=270)
        if result.returncode or not response.is_file():
            raise ValueError("monthly-reference-runtime-failed")
        output = joblib.load(response)
    if "error" in output:
        raise ValueError(output["error"])
    if (validate_published_artifact(model_path) != digest or output.get("artifact_sha256") != digest
            or list(output["features"]) != bundle["feature_columns"]
            or len(output["features"]) != len(horses)):
        raise ValueError("monthly-reference-runtime-result-mismatch")
    return output
