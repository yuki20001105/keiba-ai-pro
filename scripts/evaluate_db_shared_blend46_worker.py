"""One immutable, chronological offline fixed46 global/venue/blend50 fold.

All three strategies share TRAIN-only feature encoding and speed units. The
specialist's TRAIN cohort changes, never the race cohort being evaluated. Each
strategy gets its own CAL-only temperature, fixed before TEST. Research-only
artifacts cannot activate a model, enter normal model discovery, or authorize
expected-value bets. No database connections or network requests are made.
"""
from __future__ import annotations

import argparse
import gc
import hashlib
import json
from pathlib import Path
import re
import sys
import time
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parents[1]
OUTPUT_ROOT = ROOT / "reports/generated"
SCHEMA = "db-shared-blend46-worker-v1"
MODEL_SCHEMA = "research-db-shared-blend46-v1"
STRATEGIES = ("global", "venue_only", "blend50")
OMITTED_FEATURE = "safe_past5_top3_rate"
REQUEST_KEYS = frozenset({"schema", "job_id", "cache_dir", "cache_manifest_sha256",
    "expected_source_hashes", "protocol_sha256", "feature_columns", "fold", "device",
    "n_threads", "execution_receipt", "save_models", "expected_worker_source_hashes"})
FOLD_KEYS = frozenset({"fold_id", "test_year", "train_date_from", "train_date_to",
    "cal_date_from", "cal_date_to", "test_date_from", "test_date_to"})
ID_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9_-]{0,79}")
SHA_RE = re.compile(r"[0-9a-f]{64}")
REQUIRED_HELPERS = frozenset({"scripts/evaluate_db_shared_blend46_worker.py",
    "scripts/optimize_db_shared_features_worker.py", "scripts/benchmark_db_shared_full_worker.py",
    "scripts/evaluate_db_shared_venues.py", "python-api/training/db_shared_acceleration.py"})


def _helper():
    if str(ROOT / "scripts") not in sys.path:
        sys.path.insert(0, str(ROOT / "scripts"))
    import optimize_db_shared_features_worker as helper
    return helper


def file_hash(path):
    digest = hashlib.sha256()
    with Path(path).open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def canonical_hash(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False,
        separators=(",", ":"), allow_nan=False).encode()).hexdigest()


def validate_helper_pins(request):
    pins = request.get("expected_worker_source_hashes") if isinstance(request, dict) else None
    if not isinstance(pins, dict) or not REQUIRED_HELPERS.issubset(pins):
        raise ValueError("blend46-worker-helper-pins-required")
    for relative, expected in pins.items():
        if (not isinstance(relative, str) or "\\" in relative or Path(relative).is_absolute()
                or ".." in Path(relative).parts or not isinstance(expected, str) or SHA_RE.fullmatch(expected) is None):
            raise ValueError("blend46-worker-helper-pin-schema-invalid")
        path = ROOT / relative
        if (not path.resolve().is_relative_to(ROOT.resolve()) or path.is_symlink() or not path.is_file()
                or file_hash(path) != expected):
            raise ValueError("blend46-worker-helper-source-mismatch")
    return pins.copy()


def validate_request(request, output, fit, cache):
    if not isinstance(request, dict) or frozenset(request) != REQUEST_KEYS or request.get("schema") != SCHEMA:
        raise ValueError("blend46-worker-request-schema-invalid")
    if (not isinstance(request["job_id"], str) or ID_RE.fullmatch(request["job_id"]) is None
            or request["device"] not in {"cpu", "gpu"} or type(request["n_threads"]) is not int
            or request["n_threads"] != 2 or type(request["save_models"]) is not bool):
        raise ValueError("blend46-fixed-execution-options-required")
    for name in ("protocol_sha256", "cache_manifest_sha256"):
        if not isinstance(request[name], str) or SHA_RE.fullmatch(request[name]) is None:
            raise ValueError("blend46-pinned-hash-required")
    hashes = request["expected_source_hashes"]
    if (not isinstance(hashes, dict) or not hashes or any(not isinstance(key, str)
            or not isinstance(value, str) or SHA_RE.fullmatch(value) is None for key, value in hashes.items())):
        raise ValueError("blend46-source-hashes-required")
    columns = [name for name in fit.FEATURE_COLUMNS if name != OMITTED_FEATURE]
    if len(fit.FEATURE_COLUMNS) != 47 or len(columns) != 46 or request["feature_columns"] != columns:
        raise ValueError("blend46-exact-optimized46-ordered-contract-required")
    if request["device"] == "cpu" and request["execution_receipt"] is not None:
        raise ValueError("blend46-canonical-cpu-runtime-required")
    if request["device"] == "gpu" and not isinstance(request["execution_receipt"], dict):
        raise ValueError("blend46-gpu-receipt-required")
    fold = request["fold"]
    if (not isinstance(fold, dict) or frozenset(fold) != FOLD_KEYS
            or not isinstance(fold["fold_id"], str) or ID_RE.fullmatch(fold["fold_id"]) is None
            or type(fold["test_year"]) is not int or not 2016 <= fold["test_year"] <= 2026):
        raise ValueError("blend46-fold-schema-invalid")
    year = fold["test_year"]
    expected = {"train_date_from": "20130101", "train_date_to": f"{year - 2}1231",
        "cal_date_from": f"{year - 1}0101", "cal_date_to": f"{year - 1}1231",
        "test_date_from": f"{year}0101", "test_date_to": f"{year}1231" if year < 2026 else "20260913"}
    if any(fold[name] != value for name, value in expected.items()):
        raise ValueError("blend46-fixed-disjoint-whole-year-fold-required")
    destination = cache._plain(Path(output), directory=False)
    boundary = OUTPUT_ROOT.resolve()
    if (destination.suffix != ".json" or destination.exists() or not destination.parent.is_dir()
            or not destination.resolve().is_relative_to(boundary) or destination.parent.resolve() == boundary):
        raise ValueError("blend46-new-research-output-required")
    raw = cache._plain(Path(request["cache_dir"]), directory=True)
    if not raw.is_dir() or destination.resolve().is_relative_to(raw.resolve()):
        raise ValueError("blend46-readonly-cache-output-separation-required")
    names = [f"{request['job_id']}-races.parquet", f"{request['job_id']}-runners.parquet"]
    if request["save_models"]:
        names.append(f"{request['job_id']}.research.joblib")
    if any((destination.parent / name).exists() for name in names):
        raise ValueError("blend46-artifact-already-exists")
    return raw, destination


def fold_positions(target, fold):
    """Whole dates and whole races; no overlapping labels or evaluation refits."""
    import numpy as np
    dates = target.race_date.astype(str)
    positions = {name: np.flatnonzero(dates.between(fold[f"{name.lower()}_date_from"],
        fold[f"{name.lower()}_date_to"]).to_numpy()) for name in ("TRAIN", "CAL", "TEST")}
    if any(len(value) < 2 for value in positions.values()):
        raise ValueError("blend46-nonempty-train-cal-test-required")
    if not (dates.iloc[positions["TRAIN"]].max() < dates.iloc[positions["CAL"]].min()
            and dates.iloc[positions["CAL"]].max() < dates.iloc[positions["TEST"]].min()):
        raise ValueError("blend46-chronological-order-required")
    groups = [set(target.iloc[value].race_id.astype(str)) for value in positions.values()]
    if any(groups[left] & groups[right] for left, right in ((0, 1), (0, 2), (1, 2))):
        raise ValueError("blend46-race-cohort-overlap")
    return positions


def blend_scores(global_scores, specialist_scores, available):
    import numpy as np
    global_scores, specialist_scores = map(lambda value: np.asarray(value, dtype=float),
        (global_scores, specialist_scores))
    available = np.asarray(available)
    if (global_scores.ndim != 1 or specialist_scores.shape != global_scores.shape
            or available.shape != global_scores.shape or available.dtype != bool
            or not np.isfinite(global_scores).all() or not np.isfinite(specialist_scores).all()):
        raise ValueError("blend46-aligned-finite-raw-score-vectors-required")
    specialist = np.where(available, specialist_scores, global_scores)
    return {"global": global_scores.copy(), "venue_only": specialist,
            "blend50": (global_scores + specialist) * .5}


def probability_records(target, probabilities):
    """Additive per-race calibration evidence; dead heats never zero-filled."""
    import numpy as np
    import pandas as pd
    probabilities = np.asarray(probabilities, dtype=float)
    if (probabilities.shape != (len(target),) or not np.isfinite(probabilities).all()
            or (probabilities < 0).any() or (probabilities > 1).any()):
        raise ValueError("blend46-probability-values-invalid")
    frame = target[["race_id", "race_date", "horse_number", "finish"]].copy().reset_index(drop=True)
    frame["probability"] = probabilities
    if not np.allclose(frame.groupby("race_id").probability.sum().to_numpy(), 1., rtol=0, atol=1e-10):
        raise ValueError("blend46-probabilities-not-race-normalized")
    records = []
    for race_id, race in frame.groupby("race_id", sort=True):
        outcome = race.finish.eq(1).to_numpy(dtype=float)
        eligible = int(outcome.sum()) == 1
        p = race.probability.to_numpy()
        top = race.sort_values(["probability", "horse_number"], ascending=[False, True], kind="stable").iloc[0]
        confidence = float(top.probability) if eligible else np.nan
        records.append({"race_id": str(race_id), "probability_eligible": eligible,
            "nll": float(-np.log(max(float(p[outcome.astype(bool)][0]), 1e-15))) if eligible else np.nan,
            "brier": float(np.sum((p - outcome) ** 2)) if eligible else np.nan,
            "uniform_nll": float(np.log(len(race))) if eligible else np.nan,
            "uniform_brier": float(1 - 1 / len(race)) if eligible else np.nan,
            "top1_confidence": confidence, "probability_top1_correct": int(top.finish == 1) if eligible else np.nan,
            "selection_bin": min(9, int(confidence * 10)) if eligible else -1})
    return pd.DataFrame(records)


def _new_json(path, value):
    with Path(path).open("x", encoding="utf-8") as stream:
        json.dump(value, stream, ensure_ascii=False, sort_keys=True, indent=2, allow_nan=False)


def run_worker(request, output):
    pinned_helpers = validate_helper_pins(request)
    helper = _helper()
    helper._bootstrap_gpu(request)
    import numpy as np
    import pandas as pd
    import joblib
    from threadpoolctl import threadpool_limits
    started = time.perf_counter()
    fit, acceleration, cache, benchmark, venue = helper._modules()
    raw, destination = validate_request(request, output, fit, cache)
    runtime = helper._runtime(request, acceleration)
    source_helpers = {"worker": file_hash(__file__), "worker_helpers": file_hash(helper.__file__),
        "benchmark_cache_verifier": file_hash(benchmark.__file__), "gpu_acceleration": file_hash(acceleration.__file__),
        "race_metrics": file_hash(venue.__file__)}
    clock = time.perf_counter()
    target, features, manifest = helper.load_prepared(request, fit, cache, benchmark)
    if manifest["period"] != ["2013-01", "2026-09"]:
        raise ValueError("blend46-2013-history-warmup-cache-required")
    cache_seconds = time.perf_counter() - clock
    positions = fold_positions(target, request["fold"])
    parts = {name: target.iloc[value].reset_index(drop=True) for name, value in positions.items()}
    raws = {name: features.iloc[value].reset_index(drop=True) for name, value in positions.items()}
    columns = request["feature_columns"]
    clock = time.perf_counter()
    encoder, encoded_train, _, diagnostics = helper.encode_subset(raws["TRAIN"], raws["CAL"], columns, fit)
    encoded = {"TRAIN": encoded_train, "CAL": encoder.transform(raws["CAL"].loc[:, columns]),
        "TEST": encoder.transform(raws["TEST"].loc[:, columns])}
    baseline = fit.fit_speed_deviation_baseline(parts["TRAIN"])
    labels = fit.apply_speed_deviation_baseline(parts["TRAIN"], baseline).to_numpy()
    categorical = [name for name in fit.CATEGORICAL_COLUMNS if name in columns]
    preset = dict(fit.PRESETS[1])
    if preset != {"id": "standard", "num_boost_round": 200, "num_leaves": 31, "min_data_in_leaf": 30, "lambda_l2": 1.}:
        raise ValueError("blend46-fixed-standard-preset-changed")
    prepared_seconds = time.perf_counter() - clock
    facade = SimpleNamespace(_resource_limits=fit._resource_limits, CATEGORICAL_COLUMNS=tuple(categorical),
        _train_booster=lambda values, outcomes, preset, *, n_threads, **_: helper._cpu_train(values,
            outcomes, preset, categorical, n_threads))
    fits, coverage, models, calibrators, strategy_reports, artifacts = [], [], {}, {}, {}, {}
    global_scores, specialist_scores, available = {}, {}, {}
    def train_model(role, matrix, outcome):
        clock = time.perf_counter()
        gpu_records = []
        with threadpool_limits(limits=request["n_threads"]):
            with acceleration._fit_device(facade, request["device"], gpu_records):
                model = facade._train_booster(matrix, outcome, preset, n_threads=request["n_threads"])
        receipt = {"role": role, "runner_count": len(matrix), "encoded_training_sha256": fit._frame_digest(matrix),
            "labels_sha256": hashlib.sha256(np.asarray(outcome).tobytes()).hexdigest(),
            "model_text_sha256": hashlib.sha256(model.model_to_string().encode()).hexdigest(),
            "fit_seconds": time.perf_counter() - clock, "device": request["device"],
            "native_threads": request["n_threads"], "gpu_fit_records": gpu_records}
        fits.append(receipt)
        print(json.dumps({"job_id": request["job_id"], "stage": "fit_completed", "role": role,
            "fit_seconds": receipt["fit_seconds"]}), flush=True)
        return model
    models["global"] = train_model("global", encoded["TRAIN"], labels)
    for name in ("CAL", "TEST"):
        global_scores[name] = fit._finite_predictions(models["global"], encoded[name], request["n_threads"])
        specialist_scores[name] = global_scores[name].copy()
        available[name] = np.zeros(len(parts[name]), dtype=bool)
    models["specialists"] = {}
    for code, venue_name in venue.VENUES.items():
        train_positions = np.flatnonzero(parts["TRAIN"].venue.eq(venue_name).to_numpy())
        eligibility = venue.specialist_eligibility(parts["TRAIN"].iloc[train_positions], 500, 40, 2500)
        evaluators = {name: np.flatnonzero(parts[name].venue.eq(venue_name).to_numpy()) for name in ("CAL", "TEST")}
        receipt = {"venue_code": code, "venue": venue_name, **eligibility,
            "cal_race_count": int(parts["CAL"].iloc[evaluators["CAL"]].race_id.nunique()),
            "cal_runner_count": len(evaluators["CAL"]),
            "test_race_count": int(parts["TEST"].iloc[evaluators["TEST"]].race_id.nunique()),
            "test_runner_count": len(evaluators["TEST"]), "fallback_to_global": not eligibility["available"]}
        coverage.append(receipt)
        # Availability depends only on TRAIN. Lack of evaluation races does not
        # change fitting or silently delete a venue from the study.
        if eligibility["available"]:
            model = train_model("specialist_" + code, encoded["TRAIN"].iloc[train_positions], labels[train_positions])
            models["specialists"][venue_name] = model
            for name, selected in evaluators.items():
                if len(selected):
                    specialist_scores[name][selected] = fit._finite_predictions(model, encoded[name].iloc[selected], request["n_threads"])
                    available[name][selected] = True
    scores = {name: blend_scores(global_scores[name], specialist_scores[name], available[name]) for name in ("CAL", "TEST")}
    race_frames, runner_frames = [], []
    for strategy in STRATEGIES:
        clock = time.perf_counter()
        calibrator, calibration_audit = fit.fit_temperature(parts["CAL"], scores["CAL"][strategy])
        calibrators[strategy] = calibrator
        probabilities = {name: calibrator.predict_proba(scores[name][strategy], parts[name].race_id)
            if calibrator is not None else None for name in ("CAL", "TEST")}
        metrics = {name: fit.probability_metrics(parts[name], probabilities[name]) if probabilities[name] is not None
            else None for name in ("CAL", "TEST")}
        validation = fit.validate_probabilities(calibration_audit, metrics["TEST"], parts["CAL"], parts["TEST"])
        validation.update(eligible_for_ev=False, research_only=True, known_retrospective_not_fresh_forward_validation=True)
        strategy_reports[strategy] = {"calibration_audit": calibration_audit, "probability_metrics": metrics,
            "probability_validation": validation,
            "accuracy_metrics": {name: fit.accuracy_metrics(parts[name], scores[name][strategy], baseline)
                for name in ("CAL", "TEST")}, "calibration_evaluation_seconds": time.perf_counter() - clock}
        for name in ("CAL", "TEST"):
            frame = venue.race_metric_records(parts[name], scores[name][strategy], baseline).rename(
                columns={"squared_error_sum": "squared_speed_error_sum"})
            runner_flags = parts[name].assign(_available=available[name]).groupby("race_id")._available.all()
            frame["specialist_available"] = frame.race_id.map(runner_flags).astype(bool)
            frame["fallback_to_global"] = False if strategy == "global" else ~frame.specialist_available
            if probabilities[name] is not None:
                frame = frame.merge(probability_records(parts[name], probabilities[name]), on="race_id", validate="one_to_one")
            else:
                frame["probability_eligible"] = False
                for column in ("nll", "brier", "uniform_nll", "uniform_brier", "top1_confidence", "probability_top1_correct"):
                    frame[column] = np.nan
                frame["selection_bin"] = -1
            # top1_correct in accuracy is always defined, including dead heats.
            # Probability records use a separately named outcome to preserve
            # their explicit single-winner denominator.
            for column, value in (("fold_id", request["fold"]["fold_id"]), ("test_year", request["fold"]["test_year"]),
                    ("strategy", strategy), ("split", name)):
                frame[column] = value
            race_frames.append(frame)
            runners = parts[name][["race_id", "race_date", "horse_id", "horse_number", "finish", "venue", "surface"]].copy()
            runners["raw_score"] = scores[name][strategy]
            runners["predicted_speed_mps"] = fit._inverse_speed(scores[name][strategy], parts[name], baseline)
            runners["actual_speed_mps"] = fit.raw_speed_mps(parts[name]).to_numpy()
            runners["win_probability"] = probabilities[name] if probabilities[name] is not None else np.nan
            runners["specialist_available"] = available[name]
            for column, value in (("fold_id", request["fold"]["fold_id"]), ("test_year", request["fold"]["test_year"]),
                    ("strategy", strategy), ("split", name)):
                runners[column] = value
            runner_frames.append(runners)
        print(json.dumps({"job_id": request["job_id"], "stage": "calibration_completed", "strategy": strategy,
            "validated": validation["validated"], "temperature": calibration_audit["temperature"]}), flush=True)
    for label, values in (("races", race_frames), ("runners", runner_frames)):
        path = destination.parent / f"{request['job_id']}-{label}.parquet"
        frame = pd.concat(values, ignore_index=True)
        with path.open("xb") as stream:
            frame.to_parquet(stream, index=False)
        artifacts[label] = {"path": str(path), "sha256": file_hash(path), "rows": len(frame)}
    if request["save_models"]:
        path = destination.parent / f"{request['job_id']}.research.joblib"
        bundle = {"schema": MODEL_SCHEMA, "models": models, "encoder": encoder, "baseline": baseline,
            "calibrators": calibrators, "feature_columns": columns, "categorical_columns": categorical,
            "specialist_weight": .5, "fallback": "global", "protocol_sha256": request["protocol_sha256"],
            "source_hashes": request["expected_source_hashes"], "fold": request["fold"],
            "research_only": True, "artifact_published": False, "eligible_for_ev": False,
            "known_retrospective_not_fresh_forward_validation": True}
        with path.open("xb") as stream:
            joblib.dump(bundle, stream)
        digest = file_hash(path)
        restored = joblib.load(path)
        check_encoded = encoded["TEST"].iloc[:32]
        parity = []
        pairs = [("global", models["global"], restored["models"]["global"])]
        pairs.extend((name, model, restored["models"]["specialists"][name]) for name, model in models["specialists"].items())
        for name, original_model, restored_model in pairs:
            left = fit._finite_predictions(original_model, check_encoded, request["n_threads"])
            right = fit._finite_predictions(restored_model, check_encoded, request["n_threads"])
            if not np.allclose(left, right, rtol=0, atol=1e-10):
                raise ValueError("blend46-research-model-reload-parity-failed")
            parity.append({"model": name, "sample_rows": len(right),
                "max_abs_prediction_difference": float(np.max(np.abs(left - right)))})
        if (canonical_hash(restored["baseline"]) != canonical_hash(baseline)
                or restored["feature_columns"] != columns or restored["encoder"].categories != encoder.categories
                or any((restored["calibrators"][name].temperature if restored["calibrators"][name] else None)
                    != (calibrators[name].temperature if calibrators[name] else None) for name in STRATEGIES)
                or digest != file_hash(path)):
            raise ValueError("blend46-research-pipeline-reload-parity-failed")
        artifacts["model"] = {"path": str(path), "sha256": digest, "research_schema": MODEL_SCHEMA,
            "global_reload_sample": len(check_encoded), "reload_parity": parity,
            "max_abs_reload_difference": max(row["max_abs_prediction_difference"] for row in parity)}
    if (validate_helper_pins(request) != pinned_helpers or fit.source_hashes() != request["expected_source_hashes"] or file_hash(raw / "manifest.json") != request["cache_manifest_sha256"]
            or source_helpers != {"worker": file_hash(__file__), "worker_helpers": file_hash(helper.__file__),
                "benchmark_cache_verifier": file_hash(benchmark.__file__), "gpu_acceleration": file_hash(acceleration.__file__),
                "race_metrics": file_hash(venue.__file__)}):
        raise ValueError("blend46-cache-source-or-helper-mutated")
    report = {"schema": SCHEMA, "status": "completed", "job_id": request["job_id"], "fold": request["fold"],
        "protocol_sha256": request["protocol_sha256"], "request_sha256": canonical_hash(request),
        "feature_columns": columns, "feature_count": len(columns), "device": request["device"], "runtime": runtime,
        "source_hashes": request["expected_source_hashes"], "helper_source_hashes": source_helpers,
        "pinned_worker_source_hashes": pinned_helpers,
        "cache_manifest_sha256": request["cache_manifest_sha256"], "snapshot_sha256": manifest["snapshot_sha256"],
        "partitions": {name: fit._partition_receipt(parts[name]) for name in parts},
        "target_hashes": {name: fit._frame_digest(parts[name]) for name in parts},
        "raw_feature_hashes": {name: fit._frame_digest(raws[name]) for name in parts},
        "encoded_feature_hashes": {name: fit._frame_digest(encoded[name]) for name in parts},
        "encoder_diagnostics": diagnostics, "categories": encoder.categories,
        "baseline_sha256": canonical_hash(baseline), "labels_sha256": hashlib.sha256(labels.tobytes()).hexdigest(),
        "fit_receipts": fits, "specialist_coverage": coverage, "strategies": strategy_reports,
        "artifacts": artifacts, "timings": {"cache_read_verify_seconds": cache_seconds,
            "fold_prepare_seconds": prepared_seconds, "worker_wall_seconds": time.perf_counter() - started},
        "proofs": {"train_only_global_encoder": True, "train_only_global_speed_baseline": True,
            "specialist_eligibility_train_only": True, "mixes_raw_scores_before_calibration": True,
            "same_whole_race_cohort_all_strategies": True, "temperature_cal_only": True,
            "test_used_for_fit_or_selection": False, "zero_imputation": False, "roi_evaluated": False,
            "artifact_published": False, "active_model_changed": False, "production_changed": False,
            "forward_collector_accessed": False, "eligible_for_ev": False}}
    _new_json(destination, report)
    del models, features, target, labels, encoded
    gc.collect()
    print(json.dumps({"job_id": request["job_id"], "status": "completed", "output": str(destination)}), flush=True)
    return report


def main():
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--request", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()
    run_worker(json.loads(args.request.read_text(encoding="utf-8")), args.output)


if __name__ == "__main__":
    main()
