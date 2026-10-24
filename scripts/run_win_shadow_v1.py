"""Append-only shadow plan, CAL-only market calibration, and sealed connection replay.

All real model fits remain the saved 2026 candidate. Only a market calibrator is
fitted once, after registration, on the same 2025 CAL rows and frozen procedure.
This CLI has no network, collector, activation, purchase or production API.
"""
from __future__ import annotations

import argparse
from copy import deepcopy
from datetime import datetime, timezone
import json
from pathlib import Path
import sys
import uuid

ROOT = Path(__file__).resolve().parents[1]
for _directory in (ROOT / "scripts", ROOT / "python-api", ROOT / "keiba"):
    if str(_directory) not in sys.path:
        sys.path.insert(0, str(_directory))

import db_shared_win_four_worker as worker
import evaluate_db_shared_win_four_v2 as v2
import win_shadow_adapter_v1 as adapter
from evaluate_db_shared_venues import preservation_receipt
from research_capture import prestart
from verify_db_shared_win_prestart_inference import frame_from_capture

OUTPUT = ROOT / "reports/generated/win-shadow-v1"
PLAN_PATH = OUTPUT / "registered-plan.json"
CAL_PATH = OUTPUT / "market-calibration.json"
CAL_ATTEMPT_PATH = OUTPUT / "market-calibration-attempt.json"
STUDY = ROOT / "reports/generated/db-shared-win-four-v2/20261012T075916874189Z-92f0f837"
AGGREGATION = ROOT / "reports/generated/db-shared-win-four-v3/20261012T082507686096Z-18180fc7"
CAPTURE = ROOT / "reports/generated/db-shared-win-pipeline/20261012-kyoto11r-v2/capture-inputs/captures/20261012T063952768529Z-72e0cf89da64"
PROOF = ROOT / "reports/generated/db-shared-win-pipeline/20261012-kyoto11r-v2/proof.json"
STUDY_FINGERPRINT = "dd7ba3d310129b687c105e701164fd2f1be4daa9bc3d444ea459d67509010b76"
SUMMARY_SHA256 = "6cd99716945bef6cca22f0a2d81e4ce5aab261359f1c58f17d2c55d3dea8073d"
SOURCES = ("scripts/win_shadow_adapter_v1.py", "scripts/run_win_shadow_v1.py",
    "scripts/verify_db_shared_win_prestart_inference.py", "python-api/research_capture/prestart.py")
CAL_BOUNDS = ("20250101", "20251231")
SCHEMA = "win-shadow-plan-v1"


def read(path):
    return json.loads(Path(path).read_text(encoding="utf-8"))


def _plain(path):
    path = Path(path)
    if any(item.is_symlink() for item in (path, *path.parents)):
        raise ValueError("shadow-symlink-path-forbidden")
    return path


def write_sealed(path, value):
    """Exclusive research-only output with its independent readback seal."""
    path = _plain(path)
    if not path.resolve().is_relative_to(OUTPUT.resolve()) or path.exists():
        raise ValueError("shadow-new-isolated-output-required")
    manifest = path.with_name(path.stem + "-manifest.json")
    if manifest.exists():
        raise ValueError("shadow-output-seal-already-exists")
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("x", encoding="utf-8") as stream:
        json.dump(value, stream, ensure_ascii=False, sort_keys=True, indent=2, allow_nan=False)
    seal = {"schema": "win-shadow-artifact-seal-v1", "file": path.name,
            "sha256": worker.file_hash(path), "bytes": path.stat().st_size}
    with manifest.open("x", encoding="utf-8") as stream:
        json.dump(seal, stream, sort_keys=True, indent=2, allow_nan=False)
    if read_sealed(path) != value:
        raise ValueError("shadow-output-readback-mismatch")
    return seal


def read_sealed(path):
    path = _plain(path)
    seal = read(_plain(path.with_name(path.stem + "-manifest.json")))
    if (not path.resolve().is_relative_to(OUTPUT.resolve()) or seal.get("schema") != "win-shadow-artifact-seal-v1"
            or seal.get("file") != path.name or seal.get("sha256") != worker.file_hash(path)
            or seal.get("bytes") != path.stat().st_size):
        raise ValueError("shadow-output-seal-mismatch")
    return read(path)


def file_receipt(path):
    path = _plain(path)
    if not path.is_file() or not path.resolve().is_relative_to(ROOT.resolve()):
        raise ValueError("shadow-repository-pinned-file-required")
    return {"path": path.resolve().relative_to(ROOT.resolve()).as_posix(),
            "sha256": worker.file_hash(path), "bytes": path.stat().st_size}


def verify_file_receipt(item):
    relative = item.get("path")
    if (not isinstance(relative, str) or Path(relative).is_absolute() or ".." in Path(relative).parts
            or "\\" in relative or file_receipt(ROOT / relative) != item):
        raise ValueError("shadow-input-artifact-drift")
    return _plain(ROOT / relative)


def source_pins():
    return {name: worker.file_hash(ROOT / name) for name in SOURCES}


def verify_study_inventory():
    files = {path.relative_to(STUDY).as_posix(): worker.file_hash(_plain(path))
             for path in sorted(STUDY.rglob("*")) if path.is_file()}
    if len(files) != 311 or worker.canonical_hash(files) != STUDY_FINGERPRINT:
        raise ValueError("shadow-frozen-saved44-study-drift")
    return {"files": files, "fingerprint_sha256": STUDY_FINGERPRINT, "file_count": len(files)}


def capture_input():
    """Re-use the previously sealed actual card; never re-fetch or re-date it."""
    import pandas as pd
    bundle = prestart.read_bundle(_plain(CAPTURE))
    frame92, generation = frame_from_capture(bundle, CAPTURE)
    columns, _ = worker.feature_contract(adapter.CANDIDATE)
    frame = frame92[list(adapter.METADATA) + columns].copy()
    history_path = CAPTURE / "sources/history.parquet"
    history = pd.read_parquet(history_path, columns=["race_id", "race_date"])
    target_day = str(frame.race_date.iloc[0])
    if history.empty or not history.race_date.lt(target_day).all():
        raise ValueError("shadow-target-or-future-history-in-capture")
    canonical = read(CAPTURE / "sources/canonical_inputs.json")
    if (canonical.get("target_date") != target_day or canonical.get("history_rows") != len(history)
            or canonical.get("history_object_sha256") != worker.file_hash(history_path)):
        raise ValueError("shadow-canonical-history-binding-mismatch")
    history_audit = bundle.get("history_audit", {})
    minimum, maximum = str(history.race_date.min()), str(history.race_date.max())
    if (history_audit.get("history_max_source_date") != maximum
            or history_audit.get("history_rows") != len(history)
            or history_audit.get("history_races") != int(history.race_id.nunique())):
        raise ValueError("shadow-history-audit-actual-rows-mismatch")
    receipt = {"schema": adapter.SCHEMA, "race_id": bundle["race_id"], "race_date": target_day,
        "columns": columns, "generator_version": adapter.GENERATOR_VERSION,
        "frame_sha256": worker.frame_hash(frame), "identities": frame[["race_id", "race_date", "horse_number", "horse_id"]].to_dict("records"),
        "formula_receipt": generation["formula_receipt"],
        "generator_source_hashes": generation["formula_receipt"]["source_hashes"],
        "capture_manifest_sha256": worker.file_hash(CAPTURE / "artifact_manifest.json"),
        "capture_bundle_sha256": worker.file_hash(CAPTURE / "bundle.json"),
        "history_sha256": worker.file_hash(history_path), "canonical_sha256": generation["source_snapshot_sha256"],
        "history": {"rows": len(history), "races": int(history.race_id.nunique()), "min_date": minimum, "max_date": maximum,
            "target_or_future_rows": 0, "strict_prior_day_verified": True,
            "freshness_verified": False,
            "complete_career_coverage_verified": history_audit.get("complete_career_coverage_verified") is True,
            "capture_time_verified": history_audit.get("history_capture_time_verified") is True},
        "capture": {"source_mode": bundle["mode"], "captured_at": bundle["captured_at"],
            "feature_generated_at": bundle["feature_generated_at"], "post_time": bundle["post_time"],
            "window_opens_at": bundle["fixed_window"]["opens_at"], "window_deadline": bundle["fixed_window"]["deadline"],
            "point_in_time_verified": bundle["capture_validation"]["point_in_time_verified"],
            "odds_kind": bundle["odds_kind"], "odds_status": bundle["odds_status"],
            "quote_key_join_verified": generation["actual_quote_key_join_verified"],
            "source_receipts": deepcopy(bundle["source_receipts"])}}
    adapter.validate_shadow_input(frame, receipt, expected_input_sha256=worker.canonical_hash(receipt))
    return frame, receipt


def build_plan():
    """Read-only plan preview; no fitting, output writes, outcome/rule selection."""
    inventory = verify_study_inventory()
    request = read(STUDY / "2026-expanding_race_nll-request.json")
    result = read(STUDY / "2026-expanding_race_nll.json")
    v2.validate_worker_result(result, request, worker)
    worker.validate_source_pins(request)
    aggregate_manifest = read(AGGREGATION / "artifact_manifest.json")
    if aggregate_manifest.get("schema") != "db-shared-win-four-aggregation-manifest-v3":
        raise ValueError("shadow-completed-v3-aggregate-required")
    for name, expected in aggregate_manifest["files"].items():
        if name not in {"protocol.json", "results.json"} or worker.file_hash(AGGREGATION / name) != expected:
            raise ValueError("shadow-v3-summary-manifest-mismatch")
    if (set(aggregate_manifest["files"]) != {"protocol.json", "results.json"}
            or aggregate_manifest["files"]["results.json"] != SUMMARY_SHA256):
        raise ValueError("shadow-original-completed-v3-summary-required")
    summary = read(AGGREGATION / "results.json")
    if (summary.get("status") != "completed" or summary.get("source_hashes") != request["expected_source_hashes"]
            or summary.get("worker_results") is None or result not in summary["worker_results"]
            or summary.get("preservation_before") != summary.get("preservation_after")):
        raise ValueError("shadow-v3-original-worker-or-preservation-binding")
    proof = worker.validate_phase1_receipt(PROOF, request["phase1_receipt_sha256"])
    _frame, receipt = capture_input()
    artifacts = {"worker_result": file_receipt(STUDY / "2026-expanding_race_nll.json"),
        "worker_request": file_receipt(STUDY / "2026-expanding_race_nll-request.json"),
        "shared_input": file_receipt(STUDY / "shared50.parquet"),
        "model": file_receipt(result["artifacts"]["model"]["path"]),
        "cal_predictions": file_receipt(result["artifacts"]["calibration_predictions"]["path"]),
        "original_config": file_receipt(v2.CONFIG), "original_preregistration": file_receipt(v2.LOCK_PATH),
        "v2_amendment": file_receipt(v2.AMENDMENT_PATH),
        "v3_summary": file_receipt(AGGREGATION / "results.json"),
        "v3_summary_manifest": file_receipt(AGGREGATION / "artifact_manifest.json"),
        "phase1_proof": file_receipt(PROOF),
        "capture_bundle": file_receipt(CAPTURE / "bundle.json"),
        "capture_manifest": file_receipt(CAPTURE / "artifact_manifest.json")}
    if (artifacts["model"]["sha256"] != result["artifacts"]["model"]["sha256"]
            or artifacts["cal_predictions"]["sha256"] != result["artifacts"]["calibration_predictions"]["sha256"]
            or artifacts["shared_input"]["sha256"] != request["frame_sha256"]):
        raise ValueError("shadow-selected-artifact-original-hash-mismatch")
    before = preservation_receipt()
    if before != summary["preservation_after"]:
        raise ValueError("shadow-current-production-or-collector-drift")
    return {"schema": SCHEMA, "study_inventory": inventory, "artifacts": artifacts,
        "source_hashes": request["expected_source_hashes"], "shadow_source_hashes": source_pins(),
        "aggregation_source_hashes": summary["aggregation_source_hashes"],
        "models": ["market_only_calibrated", adapter.CANDIDATE], "candidate_fold": 2026,
        "train_period": ["20130101", "20241231"], "cal_period": list(CAL_BOUNDS),
        "candidate_model_retrained": False, "market_calibrator_fit_count_limit": 1,
        "calibration": {"function": "db_shared_win_four_worker.fit_conditioned_calibration",
            "kind": worker.direct.CALIBRATION_SCHEMA, "formula": result["calibration_audit"]["formula"],
            "bounds": [list(value) for value in worker.direct.CALIBRATION_BOUNDS],
            "ridge": worker.direct.CALIBRATION_RIDGE, "initial_parameters": [1., 0.],
            "market_input": "p_original=p_market on exactly the candidate's 2025 CAL cohort",
            "same_function_as_candidate": True, "historical_final_odds_to_prestart_transfer_unverified": True},
        "cal_partition_input_sha256": result["partition_input_hashes"]["CAL"],
        "cal_partition": result["partitions"]["CAL"], "candidate_encoder_sha256": result["encoder_sha256"],
        "input_receipt": receipt, "input_receipt_sha256": worker.canonical_hash(receipt),
        "phase1_proof_sha256": artifacts["phase1_proof"]["sha256"],
        "connection_test_mode": adapter.MODE, "retrospective_connection_only": True,
        "post_outcome_registration": True, "not_new_preregistered_holdout": True,
        "future_target": None, "future_target_status": "awaiting_user_selected_unstarted_race",
        "prospective_validation_pass": False, "history_freshness_verified": False,
        "ci_metadata_scope": "v3 retrospective study only; never transferred to a live prediction",
        "cal_and_test_separate": True, "test_outcomes_used_for_calibration": False,
        "optuna": False, "feature_or_rule_optimization": False,
        "research_only": True, "shadow_only": True, "eligible_for_ev": False,
        "purchase_authorized": False, "actual_purchases": 0,
        "model_activation": False, "forward_changes": False, "network_requests": 0,
        "preservation_before": before, "phase1_capture_before_post_verified": proof["capture_before_post_verified"]}


def register():
    if PLAN_PATH.exists() or CAL_PATH.exists() or CAL_ATTEMPT_PATH.exists():
        raise ValueError("shadow-registration-already-exists")
    plan = build_plan()
    plan["registered_at"] = datetime.now(timezone.utc).isoformat()
    write_sealed(PLAN_PATH, plan)
    return {"status": "registered_shadow_only", "plan_sha256": worker.file_hash(PLAN_PATH), "new_model_fits": 0}


def load_plan():
    plan = read_sealed(PLAN_PATH)
    current = build_plan()
    compare = dict(plan)
    registered = adapter.instant(compare.pop("registered_at", None))
    if registered > datetime.now(timezone.utc) or compare != current:
        raise ValueError("shadow-plan-or-immutable-input-drift")
    return plan


def load_candidate(plan):
    import joblib
    path = verify_file_receipt(plan["artifacts"]["model"])
    bundle = joblib.load(path)  # Only previously trusted repository artifact after all hashes pass.
    worker.validate_bundle(bundle)
    result = read(verify_file_receipt(plan["artifacts"]["worker_result"]))
    if (bundle.get("candidate_id") != adapter.CANDIDATE or bundle.get("fold_year") != 2026
            or bundle.get("source_hashes") != plan["source_hashes"]
            or bundle.get("frame_sha256") != plan["artifacts"]["shared_input"]["sha256"]
            or bundle.get("encoder_sha256") != plan["candidate_encoder_sha256"]
            or bundle.get("model_text_sha256") != result["model_text_sha256"]
            or bundle.get("calibration_audit") != result["calibration_audit"]
            or worker.file_hash(path) != plan["artifacts"]["model"]["sha256"]):
        raise ValueError("shadow-saved-model-encoder-and-calibration-binding")
    return bundle


def load_cal_input(plan):
    """Read exactly saved CAL rows; no TRAIN/TEST outcome reaches the fit API."""
    import numpy as np
    import pandas as pd
    columns, _ = worker.feature_contract(adapter.CANDIDATE)
    names = ["race_id", "race_date", "horse_number", "horse_id", "finish", "odds", "expected_runner_count", "venue", "surface"] + columns
    frame = pd.read_parquet(verify_file_receipt(plan["artifacts"]["shared_input"]),
        columns=names, filters=[("race_date", ">=", CAL_BOUNDS[0]), ("race_date", "<=", CAL_BOUNDS[1])])
    frame = frame.sort_values(["race_date", "race_id", "horse_number"], kind="stable").reset_index(drop=True)
    if (not frame.race_date.between(*CAL_BOUNDS).all()
            or worker.validate_frame(frame) != plan["cal_partition"]
            or worker.frame_hash(frame[columns]) != plan["cal_partition_input_sha256"]):
        raise ValueError("shadow-exact2025-cal-input-required")
    saved = pd.read_parquet(verify_file_receipt(plan["artifacts"]["cal_predictions"]))
    if (not saved.split.eq("CAL").all() or not saved.candidate_id.eq(adapter.CANDIDATE).all()
            or not saved.fold_year.eq(2026).all() or len(saved) != len(frame)):
        raise ValueError("shadow-candidate-cal-receipt-only-required")
    for name in ("race_id", "race_date", "horse_number", "finish", "odds", "expected_runner_count", "venue", "surface"):
        if not np.array_equal(frame[name].to_numpy(), saved[name].to_numpy()):
            raise ValueError("shadow-cal-key-order-and-outcome-receipt-mismatch")
    market = worker.market_values(frame)
    if not np.array_equal(market, saved.market_probability.to_numpy(float)):
        raise ValueError("shadow-market-cal-vector-not-identical")
    return frame, saved, market


def preflight():
    """One market CAL fit only after the immutable plan exists; never refit 44."""
    import numpy as np
    if (CAL_PATH.exists() or CAL_PATH.with_name(CAL_PATH.stem + "-manifest.json").exists()
            or CAL_ATTEMPT_PATH.exists() or CAL_ATTEMPT_PATH.with_name(CAL_ATTEMPT_PATH.stem + "-manifest.json").exists()):
        raise ValueError("shadow-market-calibration-already-exists-no-refit")
    plan = load_plan()
    frame, saved, market = load_cal_input(plan)
    bundle = load_candidate(plan)
    inference = frame.drop(columns=["finish"])
    replay = worker.predict_components(bundle, inference)
    if any(not np.array_equal(replay[name], saved[name].to_numpy(float)) for name in replay):
        raise ValueError("shadow-candidate-cal-replay-probability-mismatch")
    # Exclusive attempt seal precedes optimization. Even an interrupted/failed
    # call cannot silently retry or create a second market calibrator fit.
    write_sealed(CAL_ATTEMPT_PATH, {"schema": "win-shadow-calibration-attempt-v1",
        "plan_sha256": worker.file_hash(PLAN_PATH), "cal_period": list(CAL_BOUNDS),
        "cal_partition_input_sha256": plan["cal_partition_input_sha256"],
        "registered_at": plan["registered_at"], "started_at": datetime.now(timezone.utc).isoformat(),
        "model_fits_started": 0, "market_calibrator_attempt": 1, "retry_authorized": False})
    settings, audit = worker.fit_conditioned_calibration(frame, market, market, CAL_BOUNDS)
    adapter.validate_market_calibration(settings)
    if load_plan() != plan or preservation_receipt() != plan["preservation_before"]:
        raise ValueError("shadow-source-or-production-drift-during-calibration")
    result = {"schema": "win-shadow-market-calibration-v1", "status": "completed_shadow_only",
        "plan_sha256": worker.file_hash(PLAN_PATH), "settings": settings, "calibration_audit": audit,
        "attempt_sha256": worker.file_hash(CAL_ATTEMPT_PATH),
        "candidate_cal_replay_equal": True, "cal_partition_input_sha256": plan["cal_partition_input_sha256"],
        "same_cal_rows_and_procedure": True, "market_parameter_collinearity": "only a+b affects market softmax; frozen ridge resolves the parameter decomposition",
        "calibrator_fits_started": 1, "model_fits_started": 0, "saved_candidate_retrained": False,
        "test_outcomes_consumed": False, "network_requests": 0,
        "completed_at": datetime.now(timezone.utc).isoformat(), "preservation_after": preservation_receipt(),
        "eligible_for_ev": False, "actual_purchases": 0, "model_activated": False, "forward_modified": False}
    write_sealed(CAL_PATH, result)
    return {"status": result["status"], "calibrator_fits_started": 1, "model_fits_started": 0,
            "market_calibration": settings, "calibration_sha256": worker.file_hash(CAL_PATH)}


def read_calibration(plan):
    attempt = read_sealed(CAL_ATTEMPT_PATH)
    calibration = read_sealed(CAL_PATH)
    audit = calibration.get("calibration_audit", {})
    if (calibration.get("schema") != "win-shadow-market-calibration-v1"
            or calibration.get("status") != "completed_shadow_only"
            or calibration.get("plan_sha256") != worker.file_hash(PLAN_PATH)
            or calibration.get("same_cal_rows_and_procedure") is not True
            or calibration.get("test_outcomes_consumed") is not False
            or calibration.get("cal_partition_input_sha256") != plan["cal_partition_input_sha256"]
            or calibration.get("candidate_cal_replay_equal") is not True
            or calibration.get("calibrator_fits_started") != 1 or calibration.get("model_fits_started") != 0
            or calibration.get("saved_candidate_retrained") is not False
            or calibration.get("attempt_sha256") != worker.file_hash(CAL_ATTEMPT_PATH)
            or attempt.get("schema") != "win-shadow-calibration-attempt-v1"
            or attempt.get("plan_sha256") != worker.file_hash(PLAN_PATH)
            or attempt.get("cal_period") != list(CAL_BOUNDS)
            or attempt.get("cal_partition_input_sha256") != plan["cal_partition_input_sha256"]
            or attempt.get("registered_at") != plan["registered_at"]
            or attempt.get("market_calibrator_attempt") != 1 or attempt.get("model_fits_started") != 0
            or attempt.get("retry_authorized") is not False
            or not isinstance(audit, dict)
            or any(audit.get(key) != value for key, value in
                   {"scope": "CAL-only", "period": list(CAL_BOUNDS), "kind": worker.direct.CALIBRATION_SCHEMA,
                    "bounds": plan["calibration"]["bounds"], "ridge": plan["calibration"]["ridge"],
                    "formula": plan["calibration"]["formula"], "initial_parameters": [1., 0.],
                    "converged": True, "test_outcomes_consumed": False, "feature_selection": False,
                    "rule_selection": False, "odds_band_posthoc_multipliers": False}.items())
            or calibration.get("preservation_after") != plan["preservation_before"]):
        raise ValueError("shadow-registered-calibration-artifact-required")
    settings = calibration.get("settings")
    adapter.validate_market_calibration(settings)
    if (audit.get("a") != settings["a"] or audit.get("b") != settings["b"]
            or not adapter.instant(plan["registered_at"]) <= adapter.instant(attempt.get("started_at")) <= adapter.instant(calibration.get("completed_at"))):
        raise ValueError("shadow-calibration-settings-or-registration-time-binding")
    return calibration


def replay():
    plan = load_plan()
    calibration = read_calibration(plan)
    frame, receipt = capture_input()
    if receipt != plan["input_receipt"]:
        raise ValueError("shadow-replay-input-evidence-drift")
    bundle = load_candidate(plan)
    result = adapter.compare_shadow(frame, receipt, bundle, calibration["settings"], expected_input_sha256=plan["input_receipt_sha256"])
    second = adapter.compare_shadow(frame, receipt, load_candidate(plan), calibration["settings"], expected_input_sha256=plan["input_receipt_sha256"])
    if second != result or load_plan() != plan or preservation_receipt() != plan["preservation_before"]:
        raise ValueError("shadow-serialized-comparison-or-preservation-drift")
    result.update(plan_sha256=worker.file_hash(PLAN_PATH), market_calibration_sha256=worker.file_hash(CAL_PATH),
        candidate_model_sha256=plan["artifacts"]["model"]["sha256"],
        completed_at=datetime.now(timezone.utc).isoformat(), serialized_replay_equal=True,
        historical_ci_transferred_to_prediction=False, input_source_capture_before_post=True,
        shadow_prediction_generated_after_outcome=True, original_input_timestamp_preserved=True,
        future_target_status=plan["future_target_status"], additional_network_requests=0,
        additional_database_reads=0, preservation_before=plan["preservation_before"], preservation_after=preservation_receipt())
    destination = OUTPUT / "replays" / (datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ") + "-" + uuid.uuid4().hex[:8] + ".json")
    write_sealed(destination, result)
    return {"status": "retrospective_connection_verified_reference_only", "output": str(destination),
        "runners": len(frame), "history_max_source_date": result["validation"]["history_max_source_date"],
        "prospective_validation_pass": False, "eligible_for_ev": False, "new_model_fits": 0}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=("plan", "register", "preflight", "replay"))
    action = parser.parse_args(argv).action
    value = build_plan() if action == "plan" else {"register": register, "preflight": preflight, "replay": replay}[action]()
    print(json.dumps(value, ensure_ascii=False, allow_nan=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
