"""Offline fixed-preset raw47-subset experiments; never publish or activate.

Read one immutable, pinned raw point-in-time benchmark cache. All fitting
dictionaries and speed baselines belong solely to each split's TRAIN dates.
The caller owns the preregistered candidate/fold plan and bounded scheduling.
This worker cannot fetch, modify a DB, write normal models, or claim calibrated
win probabilities, independent future validation or betting profitability.
"""
from __future__ import annotations

import argparse
import gc
import hashlib
import json
import math
from pathlib import Path
import re
import sys
import time
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parents[1]
OUTPUT_ROOT = ROOT / "reports/generated"
SCHEMA = "db-shared-feature-optimization-worker-v1"
MODEL_SCHEMA = "research-db-shared-feature-subset-v1"
REQUEST_KEYS = frozenset({"schema", "candidate_id", "phase", "cache_dir", "cache_manifest_sha256",
    "expected_source_hashes", "protocol_sha256", "feature_columns", "splits", "device",
    "n_threads", "execution_receipt", "save_models"})
SPLIT_KEYS = frozenset({"split_id", "train_date_from", "train_date_to", "eval_date_from", "eval_date_to"})
ID_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9_-]{0,79}")
SHA_RE = re.compile(r"[0-9a-f]{64}")


def file_hash(path):
    digest = hashlib.sha256()
    with Path(path).open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def canonical_hash(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False,
        separators=(",", ":"), allow_nan=False).encode()).hexdigest()


def _modules():
    for directory in (ROOT / "python-api", ROOT / "keiba", ROOT / "scripts"):
        if str(directory) not in sys.path:
            sys.path.insert(0, str(directory))
    from training import db_shared_fit as fit, db_shared_acceleration as acceleration
    from training import db_shared_cache as cache
    import benchmark_db_shared_full_worker as benchmark
    import evaluate_db_shared_venues as venue
    return fit, acceleration, cache, benchmark, venue


def _bootstrap_gpu(request):
    """Supplement the isolated GPU environment before numerical/fit imports.

    db_shared_acceleration itself is stdlib-only at import time. The configured
    GPU environment always retains its own numerical libraries first, and the
    immutable receipt is checked before importing the experiment fit pipeline.
    """
    if not isinstance(request, dict):
        raise ValueError("feature-study-request-schema-invalid")
    if request.get("device") != "gpu":
        return
    for directory in (ROOT / "python-api", ROOT / "keiba"):
        if str(directory) not in sys.path:
            sys.path.insert(0, str(directory))
    from training import db_shared_acceleration as acceleration
    acceleration.configure_worker_imports(request.get("execution_receipt"))
    acceleration.validate_execution_receipt(request.get("execution_receipt"))


def _date(value):
    from datetime import datetime
    if not isinstance(value, str) or re.fullmatch(r"[0-9]{8}", value) is None:
        raise ValueError("feature-study-whole-date-required")
    try:
        datetime.strptime(value, "%Y%m%d")
    except ValueError as exc:
        raise ValueError("feature-study-date-invalid") from exc
    return value


def validate_request(request, output, fit, cache):
    if not isinstance(request, dict) or frozenset(request) != REQUEST_KEYS or request.get("schema") != SCHEMA:
        raise ValueError("feature-study-request-schema-invalid")
    if (not isinstance(request["candidate_id"], str) or ID_RE.fullmatch(request["candidate_id"]) is None
            or request["phase"] not in {"development", "retrospective", "full_fit"}
            or request["device"] not in {"cpu", "gpu"} or type(request["n_threads"]) is not int
            or request["n_threads"] != 2 or type(request["save_models"]) is not bool):
        raise ValueError("feature-study-fixed-execution-options-invalid")
    for name in ("protocol_sha256", "cache_manifest_sha256"):
        if not isinstance(request[name], str) or SHA_RE.fullmatch(request[name]) is None:
            raise ValueError("feature-study-pinned-hash-required")
    sources = request["expected_source_hashes"]
    if (not isinstance(sources, dict) or not sources or any(not isinstance(key, str)
            or not isinstance(value, str) or SHA_RE.fullmatch(value) is None for key, value in sources.items())):
        raise ValueError("feature-study-source-hashes-invalid")
    columns = request["feature_columns"]
    if (not isinstance(columns, list) or not columns or not all(isinstance(name, str) for name in columns)
            or len(set(columns)) != len(columns) or not set(columns).issubset(fit.FEATURE_COLUMNS)
            or columns != [name for name in fit.FEATURE_COLUMNS if name in columns]):
        raise ValueError("feature-study-ordered-raw47-subset-required")
    if request["device"] == "gpu" and not isinstance(request["execution_receipt"], dict):
        raise ValueError("feature-study-gpu-receipt-required")
    if request["device"] == "cpu" and request["execution_receipt"] is not None:
        raise ValueError("feature-study-cpu-canonical-runtime-only")
    splits = request["splits"]
    if not isinstance(splits, list) or not 1 <= len(splits) <= 20:
        raise ValueError("feature-study-splits-required")
    ids = []
    for split in splits:
        if (not isinstance(split, dict) or frozenset(split) != SPLIT_KEYS
                or not isinstance(split["split_id"], str) or ID_RE.fullmatch(split["split_id"]) is None):
            raise ValueError("feature-study-split-schema-invalid")
        ids.append(split["split_id"])
        lower, upper = _date(split["train_date_from"]), _date(split["train_date_to"])
        if lower > upper:
            raise ValueError("feature-study-training-range-invalid")
        if request["phase"] == "full_fit":
            if split["eval_date_from"] is not None or split["eval_date_to"] is not None or len(splits) != 1:
                raise ValueError("feature-study-full-fit-must-not-claim-evaluation")
        else:
            eval_lower, eval_upper = _date(split["eval_date_from"]), _date(split["eval_date_to"])
            if upper >= eval_lower or eval_lower > eval_upper:
                raise ValueError("feature-study-train-must-precede-evaluation")
            if request["phase"] == "development" and eval_upper > "20220521":
                raise ValueError("feature-study-development-must-not-use-later-evaluation")
    if len(ids) != len(set(ids)):
        raise ValueError("feature-study-unique-split-ids-required")
    destination = cache._plain(Path(output), directory=False)
    boundary = OUTPUT_ROOT.resolve()
    if (destination.suffix != ".json" or destination.exists() or not destination.parent.is_dir()
            or not destination.resolve().is_relative_to(boundary) or destination.parent.resolve() == boundary):
        raise ValueError("feature-study-new-research-output-required")
    raw = cache._plain(Path(request["cache_dir"]), directory=True)
    if not raw.is_dir() or destination.resolve().is_relative_to(raw.resolve()):
        raise ValueError("feature-study-readonly-cache-output-separation-required")
    candidate = request["candidate_id"]
    names = [f"{candidate}-races.parquet", f"{candidate}-predictions.npz"]
    if request["save_models"]:
        names.extend(f"{candidate}-{split_id}.research.joblib" for split_id in ids)
    if any((destination.parent / name).exists() for name in names):
        raise ValueError("feature-study-artifact-already-exists")
    return raw, destination


def load_prepared(request, fit, cache, benchmark):
    path = Path(request["cache_dir"])
    manifest_path = cache._plain(path / "manifest.json", directory=False)
    if file_hash(manifest_path) != request["cache_manifest_sha256"]:
        raise ValueError("feature-study-cache-manifest-hash-mismatch")
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    if manifest.get("schema") != benchmark.CACHE_SCHEMA:
        raise ValueError("feature-study-benchmark-raw47-cache-required")
    if manifest.get("source_hashes") != request["expected_source_hashes"] or fit.source_hashes() != request["expected_source_hashes"]:
        raise ValueError("feature-study-frozen-source-mismatch")
    for name in ("target", "features"):
        cache._plain(path / f"{name}.parquet", directory=False)
    adapted = {"snapshot_sha256": manifest["snapshot_sha256"], "period": manifest["period"],
               "expected_source_hashes": request["expected_source_hashes"]}
    target, features, verified = benchmark.read_cache(path, adapted, fit)
    cache._validate_audit(verified["point_in_time_audit"])
    if (verified["runner_count"] != len(target) or verified["race_count"] != int(target.race_id.nunique())
            or verified["actual_date_from"] != str(target.race_date.min())
            or verified["actual_date_to"] != str(target.race_date.max())
            or file_hash(manifest_path) != request["cache_manifest_sha256"]):
        raise ValueError("feature-study-cache-cohort-or-mutation-mismatch")
    return target, features, verified


def split_positions(target, split):
    import numpy as np
    dates = target.race_date.astype(str)
    train = np.flatnonzero(dates.between(split["train_date_from"], split["train_date_to"]).to_numpy())
    evaluate = (np.array([], dtype=int) if split["eval_date_from"] is None else
                np.flatnonzero(dates.between(split["eval_date_from"], split["eval_date_to"]).to_numpy()))
    if len(train) < 2 or (split["eval_date_from"] is not None and len(evaluate) < 2):
        raise ValueError("feature-study-nonempty-whole-cohort-splits-required")
    if len(evaluate) and (dates.iloc[train].max() >= dates.iloc[evaluate].min()
                         or set(target.iloc[train].race_id) & set(target.iloc[evaluate].race_id)):
        raise ValueError("feature-study-split-leakage-or-overlap")
    return train, evaluate


def encode_subset(training, evaluation, columns, fit):
    categorical = [name for name in fit.CATEGORICAL_COLUMNS if name in columns]
    encoder = fit.FoldEncoder(list(columns), categorical)
    encoder.fit_transform(training.loc[:, columns])
    diagnostics = dict(encoder.dropped_columns)
    # Selection is the locked research candidate, not TEST- or missingness-led
    # implicit column pruning. Preserve all requested fields and missing NaN.
    encoder.feature_columns = list(columns)
    train_encoded = encoder.transform(training.loc[:, columns])
    eval_encoded = encoder.transform(evaluation.loc[:, columns]) if len(evaluation) else None
    return encoder, train_encoded, eval_encoded, diagnostics


def _cpu_train(encoded, labels, preset, categorical, threads):
    import lightgbm as lgb
    params = {"objective": "regression", "metric": "rmse", "learning_rate": .05,
        "num_leaves": preset["num_leaves"], "min_data_in_leaf": preset["min_data_in_leaf"],
        "lambda_l2": preset["lambda_l2"], "max_bin": 63, "feature_fraction": 1., "bagging_fraction": 1.,
        "seed": 42, "data_random_seed": 42, "feature_fraction_seed": 42, "bagging_seed": 42,
        "deterministic": True, "force_col_wise": True, "device_type": "cpu",
        "num_threads": threads, "verbosity": -1}
    return lgb.train(params, lgb.Dataset(encoded, label=labels, categorical_feature=categorical,
        free_raw_data=True), num_boost_round=preset["num_boost_round"])


def _runtime(request, acceleration):
    if request["device"] == "gpu":
        verified = acceleration.current_execution_receipt()
        if (verified is None or verified["receipt_sha256"] != request["execution_receipt"]["receipt_sha256"]):
            raise ValueError("feature-study-gpu-bootstrap-verification-required")
        if request["execution_receipt"]["resolved_device"] != "gpu":
            raise ValueError("feature-study-gpu-actual-runtime-required")
        return request["execution_receipt"]["probe"]
    identity = acceleration._current_identity("cpu")
    if (Path(sys.executable).resolve() != acceleration.APP_PYTHON.resolve()
            or identity["lightgbm_dll_sha256"] != acceleration.DLL_PINS["cpu"]):
        raise ValueError("feature-study-canonical-app-cpu-runtime-required")
    return identity


def _new_json(path, value):
    with Path(path).open("x", encoding="utf-8") as stream:
        json.dump(value, stream, ensure_ascii=False, sort_keys=True, indent=2, allow_nan=False)


def run_worker(request, output):
    _bootstrap_gpu(request)
    import numpy as np
    import pandas as pd
    import joblib
    started = time.perf_counter()
    fit, acceleration, cache, benchmark, venue = _modules()
    raw, destination = validate_request(request, output, fit, cache)
    runtime = _runtime(request, acceleration)
    sources = fit.source_hashes()
    prepared_start = time.perf_counter()
    target, features, manifest = load_prepared(request, fit, cache, benchmark)
    prepared_seconds = time.perf_counter() - prepared_start
    source_helpers = {"worker": file_hash(__file__), "benchmark_cache_verifier": file_hash(benchmark.__file__),
                      "gpu_acceleration": file_hash(acceleration.__file__), "race_metrics": file_hash(venue.__file__)}
    preset = dict(fit.PRESETS[1])
    if preset != {"id": "standard", "num_boost_round": 200, "num_leaves": 31, "min_data_in_leaf": 30, "lambda_l2": 1.}:
        raise ValueError("feature-study-fixed-standard-preset-changed")
    categorical = [name for name in fit.CATEGORICAL_COLUMNS if name in request["feature_columns"]]
    facade = SimpleNamespace(_resource_limits=fit._resource_limits, CATEGORICAL_COLUMNS=tuple(categorical),
        _train_booster=lambda encoded, labels, preset, *, n_threads, **_: _cpu_train(encoded, labels, preset, categorical, n_threads))
    split_results, race_records, artifacts, vectors, train_cache = [], [], {}, {}, {}
    for split in request["splits"]:
        clock = time.perf_counter()
        train_positions, eval_positions = split_positions(target, split)
        train = target.iloc[train_positions].reset_index(drop=True)
        evaluate = target.iloc[eval_positions].reset_index(drop=True)
        train_raw = features.iloc[train_positions].reset_index(drop=True)
        eval_raw = features.iloc[eval_positions].reset_index(drop=True)
        train_key = (split["train_date_from"], split["train_date_to"], tuple(request["feature_columns"]))
        reused = train_key in train_cache
        if reused:
            cached = train_cache[train_key]
            model, encoder, baseline = cached["model"], cached["encoder"], cached["baseline"]
            diagnostics, gpu_records = cached["diagnostics"], cached["gpu_fit_records"]
            encoded = None
            eval_encoded = encoder.transform(eval_raw.loc[:, request["feature_columns"]]) if len(eval_raw) else None
            prep_seconds, fit_seconds = time.perf_counter() - clock, 0.
        else:
            encoder, encoded, eval_encoded, diagnostics = encode_subset(train_raw, eval_raw, request["feature_columns"], fit)
            baseline = fit.fit_speed_deviation_baseline(train)
            labels = fit.apply_speed_deviation_baseline(train, baseline).to_numpy()
            prep_seconds = time.perf_counter() - clock
            clock = time.perf_counter()
            gpu_records = []
            with acceleration._fit_device(facade, request["device"], gpu_records):
                model = facade._train_booster(encoded, labels, preset, n_threads=request["n_threads"])
            fit_seconds = time.perf_counter() - clock
            train_cache[train_key] = {"model": model, "encoder": encoder, "baseline": baseline,
                "diagnostics": diagnostics, "gpu_fit_records": gpu_records, "origin_split_id": split["split_id"],
                "fit_seconds": fit_seconds, "target_sha256": fit._frame_digest(train),
                "raw_features_sha256": fit._frame_digest(train_raw)}
            del labels
        cached = train_cache[train_key]
        if (cached["target_sha256"] != fit._frame_digest(train)
                or cached["raw_features_sha256"] != fit._frame_digest(train_raw)):
            raise ValueError("feature-study-reused-train-cohort-or-input-changed")
        clock = time.perf_counter()
        metrics = None
        if len(evaluate):
            raw_scores = fit._finite_predictions(model, eval_encoded, request["n_threads"])
            metrics = fit.accuracy_metrics(evaluate, raw_scores, baseline)
            records = venue.race_metric_records(evaluate, raw_scores, baseline).rename(
                columns={"squared_error_sum": "squared_speed_error_sum"})
            records.insert(0, "split_id", split["split_id"])
            race_records.append(records)
            prefix = split["split_id"]
            vectors[prefix + "__race_id"] = evaluate.race_id.astype(str).to_numpy(dtype=str)
            vectors[prefix + "__horse_number"] = evaluate.horse_number.to_numpy(dtype=np.int16)
            vectors[prefix + "__raw_scores"] = raw_scores
            vectors[prefix + "__predicted_speed_mps"] = fit._inverse_speed(raw_scores, evaluate, baseline)
            vectors[prefix + "__actual_speed_mps"] = fit.raw_speed_mps(evaluate).to_numpy()
        evaluation_seconds = time.perf_counter() - clock
        receipt = {"split_id": split["split_id"], "requested_dates": split,
            "train": fit._partition_receipt(train), "evaluation": fit._partition_receipt(evaluate) if len(evaluate) else None,
            "train_target_sha256": fit._frame_digest(train), "train_raw_features_sha256": fit._frame_digest(train_raw),
            "evaluation_target_sha256": fit._frame_digest(evaluate) if len(evaluate) else None,
            "evaluation_raw_features_sha256": fit._frame_digest(eval_raw) if len(evaluate) else None,
            "metrics": metrics, "encoder_diagnostics": diagnostics, "categories": encoder.categories,
            "baseline_sha256": canonical_hash(baseline), "device": request["device"],
            "gpu_fit_records": gpu_records, "native_threads": request["n_threads"],
            "training_fit_reused": reused, "fit_origin_split_id": cached["origin_split_id"],
            "original_fit_seconds": cached["fit_seconds"],
            "timings": {"prepare": prep_seconds, "fit": fit_seconds, "evaluation": evaluation_seconds}}
        if request["save_models"]:
            path = destination.parent / f"{request['candidate_id']}-{split['split_id']}.research.joblib"
            bundle = {"schema": MODEL_SCHEMA, "model": model, "encoder": encoder, "baseline": baseline,
                "feature_columns": request["feature_columns"], "categorical_columns": categorical,
                "source_hashes": sources, "protocol_sha256": request["protocol_sha256"],
                "candidate_id": request["candidate_id"], "phase": request["phase"], "split": receipt,
                "calibrated": False, "eligible_for_ev": False, "artifact_published": False,
                "retrospective_only": True, "active_model_changed": False}
            with path.open("xb") as stream:
                joblib.dump(bundle, stream)
            artifacts[path.name] = {"path": str(path), "sha256": file_hash(path), "kind": "quarantined_research_model"}
        split_results.append(receipt)
        print(json.dumps({"candidate_id": request["candidate_id"], "split_id": split["split_id"],
                          "device": request["device"], "fit_seconds": fit_seconds, "metrics": metrics}, ensure_ascii=False), flush=True)
        del model, encoded, eval_encoded, train_raw, eval_raw, encoder
        gc.collect()
    if race_records:
        path = destination.parent / f"{request['candidate_id']}-races.parquet"
        records = pd.concat(race_records, ignore_index=True)
        if records.duplicated(["split_id", "race_id"]).any():
            raise ValueError("feature-study-duplicate-split-race-metrics")
        with path.open("xb") as stream:
            records.to_parquet(stream, index=False)
        artifacts[path.name] = {"path": str(path), "sha256": file_hash(path), "rows": len(records), "kind": "race_metrics"}
        path = destination.parent / f"{request['candidate_id']}-predictions.npz"
        with path.open("xb") as stream:
            np.savez_compressed(stream, **vectors)
        artifacts[path.name] = {"path": str(path), "sha256": file_hash(path), "kind": "aligned_predictions"}
    if (fit.source_hashes() != sources or file_hash(raw / "manifest.json") != request["cache_manifest_sha256"]
            or source_helpers != {"worker": file_hash(__file__), "benchmark_cache_verifier": file_hash(benchmark.__file__),
                "gpu_acceleration": file_hash(acceleration.__file__), "race_metrics": file_hash(venue.__file__)}):
        raise ValueError("feature-study-frozen-source-or-cache-mutated")
    report = {"schema": SCHEMA, "status": "completed", "candidate_id": request["candidate_id"],
        "phase": request["phase"], "protocol_sha256": request["protocol_sha256"],
        "request_sha256": canonical_hash(request), "feature_columns": request["feature_columns"],
        "feature_count": len(request["feature_columns"]), "device": request["device"], "runtime": runtime,
        "preset": preset, "source_hashes": sources, "helper_source_hashes": source_helpers,
        "raw_input_hashes": {name: manifest["files"][name]["frame_digest"] for name in ("target", "features")},
        "cache_manifest_sha256": request["cache_manifest_sha256"], "snapshot_sha256": manifest["snapshot_sha256"],
        "prepared_cache_seconds": prepared_seconds, "splits": split_results, "artifacts": artifacts,
        "elapsed_seconds": time.perf_counter() - started,
        "proofs": {"point_in_time_validated": True, "train_only_encoder": True, "train_only_speed_baseline": True,
            "whole_race_cohorts": True, "chronological_train_evaluation": True, "zero_imputation": False,
            "final_period_used_for_feature_selection": False, "calibration_claimed": False,
            "win_probability_claimed": False, "roi_evaluated": False, "artifact_published": False,
            "active_model_changed": False, "production_changed": False, "forward_collector_accessed": False},
        "evaluation_label": "Known-data retrospective evaluation; no independent future validation"}
    _new_json(destination, report)
    return report


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--request", required=True)
    parser.add_argument("--output", required=True)
    arguments = parser.parse_args()
    try:
        run_worker(json.loads(Path(arguments.request).read_text(encoding="utf-8")), arguments.output)
    except Exception as exc:
        print(json.dumps({"status": "failed", "error_type": type(exc).__name__, "error": str(exc)}, ensure_ascii=False), file=sys.stderr, flush=True)
        raise SystemExit(1)
