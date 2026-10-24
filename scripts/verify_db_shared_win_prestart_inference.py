"""Read-only three-model inference from sealed real card/quote research inputs.

No DB reads, HTTP requests, collectors, activation, stake, EV or purchase API.
Study/model and capture objects are fully hash checked before unpickling trusted
repository-generated research bundles. A sealed replay or outside-window probe
remains unverified. Prediction output is exclusively written to a new research
JSON outside both source directories; originals are never edited.
"""
from __future__ import annotations

import argparse
from copy import deepcopy
from datetime import datetime, timezone
import json
from pathlib import Path
import re
import sys
import time

ROOT = Path(__file__).resolve().parents[1]
OUTPUT_ROOT = ROOT / "reports/generated"
for _directory in (ROOT / "scripts", ROOT / "python-api", ROOT / "keiba"):
    if str(_directory) not in sys.path:
        sys.path.insert(0, str(_directory))

import db_shared_win_direct_worker as worker
import db_shared_win_direct_features as features
from research_capture import prestart

SCHEMA = "research-db-shared-win-prestart-inference-v1"
MANIFEST_SCHEMA = "db-shared-win-direct-manifest-v1"
RESULT_SCHEMA = "db-shared-win-direct-result-v1"
SHA_RE = re.compile(r"[0-9a-f]{64}")
_MODEL_OUTPUTS = ("probability", "p_original", "market_probability", "temperature_probability", "residual_score", "direct_logit")


def _json(path):
    return json.loads(Path(path).read_text(encoding="utf-8"))


def _plain(path):
    path = Path(path)
    if any(value.is_symlink() for value in (path, *path.parents)):
        raise ValueError("prestart-inference-symlink-path-forbidden")
    return path


def validate_paths(study, capture, output):
    study, capture, output = map(_plain, (study, capture, output))
    boundary = OUTPUT_ROOT.resolve()
    if (not study.is_dir() or not capture.is_dir() or study.name.endswith(".partial")
            or capture.name.endswith(".partial") or not study.resolve().is_relative_to(boundary)
            or not capture.resolve().is_relative_to(boundary)):
        raise ValueError("prestart-inference-sealed-research-source-directories-required")
    if (output.suffix != ".json" or output.exists() or not output.parent.is_dir()
            or not output.resolve().is_relative_to(boundary) or output.parent.resolve() == boundary
            or any(output.resolve().is_relative_to(source.resolve()) for source in (study, capture))):
        raise ValueError("prestart-inference-new-separated-output-required")
    return study, capture, output


def verify_manifest(study):
    """Hash every study file, not just models, before loading trusted joblib."""
    study = _plain(study)
    manifest_path = _plain(study / "artifact-manifest.json")
    manifest = _json(manifest_path)
    files = manifest.get("files")
    if manifest.get("schema") != MANIFEST_SCHEMA or not isinstance(files, dict) or not files:
        raise ValueError("prestart-inference-completed-study-manifest-required")
    if not {"results.json", "protocol.json", "protocol-hash.json", "common-input.parquet"} <= set(files):
        raise ValueError("prestart-inference-study-key-objects-unsealed")
    for relative, receipt in files.items():
        if (not isinstance(relative, str) or "\\" in relative or Path(relative).is_absolute()
                or ".." in Path(relative).parts or not isinstance(receipt, dict)
                or not isinstance(receipt.get("sha256"), str) or SHA_RE.fullmatch(receipt["sha256"]) is None
                or type(receipt.get("bytes")) is not int or receipt["bytes"] < 0):
            raise ValueError("prestart-inference-study-object-receipt-invalid")
        path = _plain(study / relative)
        if (not path.is_file() or not path.resolve().is_relative_to(study.resolve())
                or worker.file_hash(path) != receipt["sha256"] or path.stat().st_size != receipt["bytes"]):
            raise ValueError("prestart-inference-study-object-hash-mismatch")
    protocol = _json(study / "protocol.json")
    protocol_sha = worker.canonical_hash(protocol)
    marker = _json(study / "protocol-hash.json")
    results = _json(study / "results.json")
    if (marker.get("sha256") != protocol_sha or manifest.get("protocol_sha256") != protocol_sha
            or results.get("protocol_sha256") != protocol_sha or results.get("schema") != RESULT_SCHEMA
            or results.get("status") != "completed" or results.get("models_research_only") is not True
            or results.get("models_published") is not False or results.get("models_activated") is not False
            or results.get("forward_changed") is not False or results.get("actual_bets") != 0):
        raise ValueError("prestart-inference-completed-research-only-study-required")
    pins = protocol.get("source_hashes")
    worker.validate_source_pins({"expected_source_hashes": pins})
    workers = results.get("worker_results")
    if (not isinstance(workers, list) or len(workers) != 3
            or set(record.get("candidate_id") for record in workers) != set(worker.CANDIDATES)):
        raise ValueError("prestart-inference-three-fixed-model-results-required")
    models = {}
    for record in workers:
        candidate = record["candidate_id"]
        if (record.get("schema") != worker.SCHEMA or record.get("status") != "completed"
                or record.get("protocol_sha256") != protocol_sha or record.get("source_hashes") != pins
                or record.get("frame_sha256") != files["common-input.parquet"]["sha256"]):
            raise ValueError("prestart-inference-worker-provenance-mismatch")
        item = record.get("artifacts", {}).get("model", {})
        path = _plain(item.get("path", ""))
        if (not path.is_file() or not path.resolve().is_relative_to(study.resolve())
                or path.suffix != ".joblib" or item.get("schema") != worker.MODEL_SCHEMA):
            raise ValueError("prestart-inference-research-model-object-required")
        relative = path.resolve().relative_to(study.resolve()).as_posix()
        if relative not in files or files[relative]["sha256"] != item.get("sha256"):
            raise ValueError("prestart-inference-model-not-bound-to-manifest")
        models[candidate] = {"path": path, "sha256": item["sha256"], "worker_result": record}
    return {"manifest_sha256": worker.file_hash(manifest_path), "protocol_sha256": protocol_sha,
            "source_hashes": pins, "models": models, "results_sha256": files["results.json"]["sha256"]}


def frame_from_capture(bundle, capture):
    """Current features/quotes joined by identity, never using current results."""
    import numpy as np
    import pandas as pd
    extension = bundle.get("features_extended")
    expected_columns = worker.feature_contract("market46_extended")[0]
    if (bundle.get("schema") != prestart.SCHEMA or bundle.get("research_only") is not True
            or bundle.get("eligible_for_ev") is not False or bundle.get("purchase_approved") is not False
            or bundle.get("model_activated") is not False or bundle.get("forward_modified") is not False
            or not isinstance(extension, dict) or extension.get("schema") != "db-shared-win-direct-captured-input-v1"
            or extension.get("columns") != expected_columns or extension.get("feature_count") != 92
            or extension.get("eligible_for_ev") is not False):
        raise ValueError("prestart-inference-extended-research-capture-required")
    matrix_payload = {"columns": extension["columns"], "values": extension.get("values")}
    if prestart.sha256(prestart._body(matrix_payload)) != extension.get("input_sha256"):
        raise ValueError("prestart-inference-captured-matrix-hash-mismatch")
    formula = extension.get("formula_receipt", {})
    if (formula.get("feature_columns") != list(features.FEATURE_COLUMNS) or formula.get("feature_count") != 88
            or formula.get("input_outcomes_consumed") is not False or formula.get("same_day_or_future_history_used") is not False
            or formula.get("dummy_current_results_used") is not False or formula.get("base46_parity", {}).get("verified") is not True
            or formula.get("history_contract") != features.HISTORY_CONTRACT
            or formula.get("source_hashes") != features.source_hashes()
            or extension.get("transform_file_sha256") != worker.file_hash(features.__file__)):
        raise ValueError("prestart-inference-frozen-formula-or-parity-mismatch")
    canonical_path = _plain(Path(capture) / "sources/canonical_inputs.json")
    if worker.file_hash(canonical_path) != extension.get("source_snapshot_sha256"):
        raise ValueError("prestart-inference-canonical-source-snapshot-hash-mismatch")
    source = _json(canonical_path)
    if (source.get("target_race_id") != bundle.get("race_id")
            or source.get("history_scope") != "ALL complete eligible JRA flat rows since 2013; strictly prior target day"):
        raise ValueError("prestart-inference-full-history-scope-required")
    canonical = pd.DataFrame(source.get("current"), columns=source.get("canonical_columns"))
    identities = pd.DataFrame(extension.get("identities"))
    values = pd.DataFrame(extension.get("values"), columns=expected_columns)
    if len(values) != len(identities) or len(identities) != len(canonical) or identities.empty:
        raise ValueError("prestart-inference-complete-runner-matrix-required")
    keys = ["race_id", "horse_number"]
    required = set(features.IDENTITY_COLUMNS)
    if (not required <= set(identities) or not required <= set(canonical)
            or identities.duplicated(keys).any() or canonical.duplicated(keys).any()
            or not identities.race_id.eq(bundle["race_id"]).all()):
        raise ValueError("prestart-inference-runner-key-cohort-invalid")
    canonical = canonical.set_index(keys)
    wanted = pd.MultiIndex.from_frame(identities.loc[:, keys])
    if set(canonical.index) != set(wanted):
        raise ValueError("prestart-inference-canonical-card-key-mismatch")
    canonical = canonical.loc[wanted].reset_index()
    for name in ("horse_id", "race_date"):
        if not canonical[name].astype(str).equals(identities[name].astype(str)):
            raise ValueError("prestart-inference-card-horse-identity-mismatch")
    card = bundle.get("card", {}).get("horses", [])
    card_by_number = {int(row["horse_number"]): row for row in card}
    numbers = identities.horse_number.to_numpy(int)
    quotes = bundle.get("quotes", {})
    if (len(card_by_number) != len(card) or set(card_by_number) != set(numbers)
            or set(quotes) != {str(number) for number in numbers} or bundle.get("odds_status") != "middle"):
        raise ValueError("prestart-inference-whole-card-and-market-quotes-required")
    for row in identities.to_dict("records"):
        if str(card_by_number[int(row["horse_number"])].get("horse_id")) != str(row["horse_id"]):
            raise ValueError("prestart-inference-captured-horse-id-conflict")
    odds_receipts = [row for row in bundle.get("source_receipts", []) if row.get("kind") == "odds"]
    if len(odds_receipts) != 1 or odds_receipts[0].get("body_sha256") != extension.get("market_source_body_sha256"):
        raise ValueError("prestart-inference-captured-price-source-unbound")
    frame = pd.concat([identities.loc[:, list(features.IDENTITY_COLUMNS)].reset_index(drop=True), values], axis=1)
    # Only non-outcome canonical metadata is projected. Finish, payout, time,
    # last3f and card display prices never qualify as inference inputs.
    for name in ("venue", "surface", "num_horses"):
        frame[name] = canonical[name].to_numpy()
    frame["expected_runner_count"] = canonical.num_horses.to_numpy()
    frame["odds"] = [quotes[str(number)] for number in numbers]
    check = features.add_market_features(frame)
    if not np.allclose(frame.loc[:, list(features.MARKET_COLUMNS)].to_numpy(float),
                       check.loc[:, list(features.MARKET_COLUMNS)].to_numpy(float), rtol=0., atol=1e-12):
        raise ValueError("prestart-inference-price-components-not-exact-quote-derived")
    worker.validate_frame(frame, require_results=False)
    worker.market_values(frame)
    for name in ("safe_venue", "safe_surface"):
        if not frame[name].astype(str).eq(frame["venue" if name == "safe_venue" else "surface"].astype(str)).all():
            raise ValueError("prestart-inference-feature-metadata-conflict")
    if {"finish", "tansho_payout", "time_seconds", "last_3f_time"} & set(frame):
        raise ValueError("prestart-inference-current-outcome-leak")
    return frame, {"source_snapshot_sha256": extension["source_snapshot_sha256"],
        "matrix_input_sha256": extension["input_sha256"], "formula_receipt": deepcopy(formula),
        "model_input_sha256": worker.frame_hash(frame), "current_outcomes_consumed": False,
        "market_source_body_sha256": extension["market_source_body_sha256"], "actual_quote_key_join_verified": True}


def load_models(study_receipt):
    import joblib
    bundles = {}
    for candidate in worker.CANDIDATES:
        item = study_receipt["models"][candidate]
        bundle = joblib.load(item["path"])
        worker.validate_bundle(bundle)
        if (bundle.get("candidate_id") != candidate or bundle.get("protocol_sha256") != study_receipt["protocol_sha256"]
                or bundle.get("source_hashes") != study_receipt["source_hashes"]
                or bundle.get("frame_sha256") != item["worker_result"].get("frame_sha256")
                or bundle.get("model_text_sha256") != item["worker_result"].get("model_text_sha256")
                or bundle.get("encoder_sha256") != item["worker_result"].get("encoder_sha256")):
            raise ValueError("prestart-inference-loaded-model-provenance-mismatch")
        if worker.file_hash(item["path"]) != item["sha256"]:
            raise ValueError("prestart-inference-model-mutated-during-load")
        bundles[candidate] = bundle
    return bundles


def predict_all(bundles, frame):
    import numpy as np
    import pandas as pd
    output = {}
    for candidate in worker.CANDIDATES:
        if candidate not in bundles:
            raise ValueError("prestart-inference-all-three-bundles-required")
        components = worker.predict_components(bundles[candidate], frame)
        if set(components) != set(_MODEL_OUTPUTS):
            raise ValueError("prestart-inference-fixed-component-contract-required")
        rows = frame.loc[:, list(features.IDENTITY_COLUMNS) + ["odds"]].copy()
        for name in _MODEL_OUTPUTS:
            vector = np.asarray(components[name], float)
            if vector.shape != (len(frame),) or not np.isfinite(vector).all():
                raise ValueError("prestart-inference-finite-complete-vector-required")
            rows[name] = vector
        if not np.allclose(rows.groupby("race_id").probability.sum(), 1., rtol=0, atol=1e-10):
            raise ValueError("prestart-inference-race-normalized-probability-required")
        output[candidate] = rows.to_dict("records")
    return output


def run(study, capture, output):
    clock = time.perf_counter()
    study, capture, output = validate_paths(study, capture, output)
    study_receipt = verify_manifest(study)
    bundle = prestart.read_bundle(capture)
    capture_manifest_sha = worker.file_hash(capture / "artifact_manifest.json")
    capture_manifest = _json(capture / "artifact_manifest.json")
    frame, input_receipt = frame_from_capture(bundle, capture)
    models = load_models(study_receipt)
    predictions = predict_all(models, frame)
    # A second deserialization/prediction is an independent artifact-read replay,
    # not another data fetch or second model fit.
    replay = predict_all(load_models(study_receipt), frame)
    if predictions != replay:
        raise ValueError("prestart-inference-serialized-model-replay-mismatch")
    sources = {Path(__file__).resolve().relative_to(ROOT).as_posix(): worker.file_hash(__file__),
               Path(prestart.__file__).resolve().relative_to(ROOT).as_posix(): worker.file_hash(prestart.__file__)}
    result = {"schema": SCHEMA, "status": "completed_reference_only", "study_directory": str(study.resolve()),
        "capture_directory": str(capture.resolve()), "study_manifest_sha256": study_receipt["manifest_sha256"],
        "study_results_sha256": study_receipt["results_sha256"], "protocol_sha256": study_receipt["protocol_sha256"],
        "capture_manifest_sha256": capture_manifest_sha, "capture_sha256": capture_manifest["capture_sha256"],
        "race_id": bundle["race_id"], "captured_at": bundle["captured_at"], "capture_mode": bundle["mode"],
        "post_time": bundle["post_time"], "capture_validation": deepcopy(bundle["capture_validation"]),
        "fixed_window": deepcopy(bundle["fixed_window"]), "input_receipt": input_receipt,
        "source_hashes": sources, "model_source_hashes": study_receipt["source_hashes"],
        "models": {name: {"model_sha256": item["sha256"], "feature_count": len(models[name]["feature_columns"]),
            "calibration_audit": deepcopy(models[name]["calibration_audit"]),
            "temperature_calibration_audit": deepcopy(models[name]["temperature_calibration_audit"]),
            "research_only": True, "artifact_published": False, "eligible_for_ev": False}
            for name, item in study_receipt["models"].items()},
        "predictions": predictions, "prediction_sha256": worker.canonical_hash(predictions),
        "runner_count": len(frame), "completed_at": datetime.now(timezone.utc).isoformat(),
        "elapsed_seconds": time.perf_counter() - clock, "current_outcomes_consumed": False,
        "serialized_model_replay_equal": True, "source_claims_upgraded": False,
        "input_window_validation_preserved": True, "model_inference_before_post_verified": False,
        "additional_http_requests": 0, "additional_database_reads": 0,
        "models_activated": False, "forward_modified": False, "reference_only": True,
        "research_only": True, "eligible_for_ev": False, "purchase_approved": False,
        "stakes_assigned": False, "actual_purchases": 0, "expected_values_display_authorized": False}
    if (verify_manifest(study)["manifest_sha256"] != study_receipt["manifest_sha256"]
            or worker.file_hash(capture / "artifact_manifest.json") != capture_manifest_sha
            or prestart.read_bundle(capture) != bundle or features.source_hashes() != input_receipt["formula_receipt"]["source_hashes"]):
        raise ValueError("prestart-inference-input-source-mutated-during-run")
    with output.open("x", encoding="utf-8") as stream:
        json.dump(result, stream, ensure_ascii=False, sort_keys=True, indent=2, allow_nan=False)
    loaded = _json(output)
    if loaded["prediction_sha256"] != worker.canonical_hash(loaded["predictions"]) or loaded["predictions"] != predictions:
        raise ValueError("prestart-inference-output-readback-failed")
    return result


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--study", type=Path, required=True)
    parser.add_argument("--capture", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args(argv)
    result = run(args.study, args.capture, args.output)
    print(json.dumps({"status": result["status"], "race_id": result["race_id"], "runner_count": result["runner_count"],
                      "capture_point_in_time_verified": result["capture_validation"]["point_in_time_verified"],
                      "eligible_for_ev": False, "elapsed_seconds": result["elapsed_seconds"]}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
