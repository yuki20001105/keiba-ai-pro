"""Isolated fixed-46, annual walk-forward winner-probability workers.

This module never activates a model or touches a live database. The worker CLI
requires a sealed Phase 1 proof before fitting. It reuses the original frozen
TRAIN-only encoder and ordered 46-feature contract, without altering them.
Race NLL uses the exact softmax gradient but a diagonal Hessian approximation;
the cross-runner Hessian terms are deliberately not supplied to LightGBM.
"""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import sys
import time

import db_shared_win_direct_worker as direct

ROOT = direct.ROOT
OUTPUT_ROOT = ROOT / "reports/generated"
SCHEMA = "db-shared-win-four-worker-v1"
MODEL_SCHEMA = "research-db-shared-win-four-v1"
CANDIDATES = ("expanding_binary", "rolling5_binary", "expanding_race_nll", "rolling5_race_nll")
FOLD_YEARS = tuple(range(2016, 2027))
HESSIAN_FLOOR = 1e-6
PROBABILITY_FLOOR = 1e-12
BOOST_ROUNDS = direct.BOOST_ROUNDS
BASE_PARAMETERS = dict(direct.PARAMETERS)
REQUEST_KEYS = frozenset({"schema", "job_id", "candidate_id", "fold_year", "frame_path", "frame_sha256",
    "protocol_sha256", "expected_source_hashes", "feature_columns", "categorical_columns", "device", "n_threads",
    "phase1_receipt_path", "phase1_receipt_sha256"})
PROTOCOL_SOURCE = "config/research/db-shared-win-four-20261012.json"
REQUIRED_SOURCES = direct.REQUIRED_SOURCES | {"scripts/db_shared_win_four_worker.py", "scripts/run_db_shared_win_pipeline.py", PROTOCOL_SOURCE}
file_hash = direct.file_hash
canonical_hash = direct.canonical_hash
frame_hash = direct.frame_hash
TrainEncoder = direct.TrainEncoder
market_values = direct.market_values
conditioned_probabilities = direct.conditioned_probabilities


def feature_contract(candidate_id="expanding_binary"):
    if candidate_id not in CANDIDATES:
        raise ValueError("win-four-fixed-candidate-required")
    return direct.feature_contract("market46")


def fold_periods(fold_year, candidate_id):
    feature_contract(candidate_id)
    if type(fold_year) is not int or fold_year not in FOLD_YEARS:
        raise ValueError("win-four-fixed-fold-year-required")
    first = 2013 if candidate_id.startswith("expanding_") else max(2013, fold_year - 6)
    return {"TRAIN": (f"{first}0101", f"{fold_year - 2}1231"),
            "CAL": (f"{fold_year - 1}0101", f"{fold_year - 1}1231"),
            "TEST": (f"{fold_year}0101", f"{fold_year}1231")}


def objective_kind(candidate_id):
    feature_contract(candidate_id)
    return "race_nll" if candidate_id.endswith("race_nll") else "binary"


def objective_description(candidate_id):
    if objective_kind(candidate_id) == "binary":
        return direct.OBJECTIVE
    return "race NLL with log(p_market) offset; exact softmax gradient; diagonal Hessian approximation floor=1e-6"


def parameters(candidate_id):
    result = dict(BASE_PARAMETERS)
    result["objective"] = "binary" if objective_kind(candidate_id) == "binary" else "custom_race_nll"
    return result


def validate_frame(frame, require_results=True, require_payout=False):
    """Payout availability never selects TRAIN/CAL; inference ignores outcomes."""
    import numpy as np
    import pandas as pd
    receipt = direct.validate_frame(frame, require_results=False)
    if require_results:
        if "finish" not in frame:
            raise ValueError("win-four-confirmed-results-required")
        finish = pd.to_numeric(frame.finish, errors="coerce").to_numpy(float)
        counts = frame.expected_runner_count.to_numpy(float)
        if (not np.isfinite(finish).all() or (finish < 1).any() or (finish > counts).any()
                or not np.equal(finish, np.floor(finish)).all()
                or pd.Series(finish == 1).groupby(frame.race_id.reset_index(drop=True)).sum().ne(1).any()):
            raise ValueError("win-four-single-confirmed-winner-required")
        if require_payout:
            direct.validate_frame(frame, require_results=True)
    return receipt


def contiguous_groups(frame, require_results=False):
    import numpy as np
    validate_frame(frame, require_results=require_results)
    ids = frame.race_id.to_numpy()
    starts = np.r_[0, np.flatnonzero(ids[1:] != ids[:-1]) + 1].astype(int)
    sizes = np.diff(np.r_[starts, len(frame)]).astype(int)
    if len(set(ids[starts])) != len(starts):
        raise ValueError("win-four-contiguous-whole-race-groups-required")
    return sizes


def _group_contract(scores, sizes, labels=None):
    import numpy as np
    values = np.asarray(scores, dtype=float)
    groups = np.asarray(sizes)
    if (values.ndim != 1 or not len(values) or not np.isfinite(values).all()
            or groups.ndim != 1 or not len(groups) or groups.dtype.kind not in "iu"
            or (groups < 2).any() or (groups > 18).any() or int(groups.sum()) != len(values)):
        raise ValueError("win-four-valid-contiguous-group-vector-required")
    starts = np.r_[0, np.cumsum(groups)[:-1]].astype(int)
    if labels is not None:
        target = np.asarray(labels, dtype=float)
        if (target.shape != values.shape or not np.isfinite(target).all()
                or not np.isin(target, [0., 1.]).all() or not np.equal(np.add.reduceat(target, starts), 1.).all()):
            raise ValueError("win-four-one-winner-per-group-required")
    return values, groups, starts


def race_softmax(scores, sizes):
    import numpy as np
    values, groups, starts = _group_contract(scores, sizes)
    centered = values - np.repeat(np.maximum.reduceat(values, starts), groups)
    exponentials = np.exp(centered)
    return exponentials / np.repeat(np.add.reduceat(exponentials, starts), groups)


def race_nll(scores, labels, sizes):
    """Sum, not mean, matches unscaled per-race objective gradients."""
    import numpy as np
    values, groups, starts = _group_contract(scores, sizes, labels)
    maxima = np.maximum.reduceat(values, starts)
    centered = values - np.repeat(maxima, groups)
    normalizers = np.log(np.add.reduceat(np.exp(centered), starts)) + maxima
    return float(normalizers.sum() - np.dot(values, np.asarray(labels, float)))


def race_nll_gradient_hessian(scores, labels, sizes):
    import numpy as np
    _group_contract(scores, sizes, labels)
    probability = race_softmax(scores, sizes)
    gradient = probability - np.asarray(labels, float)
    # Exact diagonal of the softmax Hessian, with a fixed numerical floor.
    # Off-diagonal -p_i*p_j is omitted: this is NOT the full Hessian.
    hessian = np.maximum(probability * (1. - probability), HESSIAN_FLOOR)
    return gradient, hessian


def make_race_objective(sizes, expected_labels):
    import numpy as np
    sizes, expected = np.asarray(sizes, int).copy(), np.asarray(expected_labels, float).copy()
    _group_contract(np.zeros(len(expected)), sizes, expected)
    def objective(raw_predictions, dataset):
        labels = np.asarray(dataset.get_label(), float)
        if not np.array_equal(labels, expected):
            raise ValueError("win-four-objective-dataset-label-order-changed")
        # LightGBM raw predictions include Dataset.init_score during fitting.
        return race_nll_gradient_hessian(raw_predictions, labels, sizes)
    return objective


def split_positions(frame, fold_year, candidate_id):
    import numpy as np
    validate_frame(frame)
    periods = fold_periods(fold_year, candidate_id)
    positions = {name: np.flatnonzero(frame.race_date.between(*bounds).to_numpy()) for name, bounds in periods.items()}
    if any(len(values) < 2 for values in positions.values()):
        raise ValueError("win-four-nonempty-fixed-fold-partitions-required")
    selected = {name: frame.iloc[values] for name, values in positions.items()}
    for left, right in (("TRAIN", "CAL"), ("CAL", "TEST")):
        a, b = selected[left], selected[right]
        if a.race_date.max() >= b.race_date.min() or set(a.race_id) & set(b.race_id):
            raise ValueError("win-four-disjoint-chronological-periods-required")
    return positions


def fit_conditioned_calibration(frame, probabilities, market, bounds):
    import numpy as np
    from scipy.optimize import minimize
    validate_frame(frame)
    if (not isinstance(bounds, (tuple, list)) or len(bounds) != 2
            or not frame.race_date.between(*bounds).all()):
        raise ValueError("win-four-cal-only-fold-boundaries-required")
    probabilities, market = map(lambda value: np.asarray(value, float), (probabilities, market))
    conditioned_probabilities(frame, probabilities, market, 1., 0.)
    logp, logm = np.log(probabilities), np.log(market)
    winners = frame.finish.eq(1).to_numpy()
    count = int(winners.sum())
    def loss(parameters):
        a, b = map(float, parameters)
        calibrated = conditioned_probabilities(frame, probabilities, market, a, b)
        nll = float(-np.log(calibrated[winners]).mean())
        ridge = direct.CALIBRATION_RIDGE * ((a - 1.) ** 2 + b ** 2)
        gradient = np.array([(np.sum(calibrated * logp) - np.sum(logp[winners])) / count,
                             (np.sum(calibrated * logm) - np.sum(logm[winners])) / count])
        gradient += 2. * direct.CALIBRATION_RIDGE * np.array([a - 1., b])
        return nll + ridge, gradient
    optimized = minimize(loss, np.array([1., 0.]), jac=True, method="L-BFGS-B",
        bounds=direct.CALIBRATION_BOUNDS, options={"maxiter": 200, "ftol": 1e-12, "gtol": 1e-7})
    if not optimized.success or not np.isfinite(optimized.fun) or not np.isfinite(optimized.x).all():
        raise ValueError("win-four-calibration-not-converged")
    a, b = map(float, optimized.x)
    probability = conditioned_probabilities(frame, probabilities, market, a, b)
    return {"kind": direct.CALIBRATION_SCHEMA, "a": a, "b": b}, {
        "scope": "CAL-only", "period": list(bounds), "kind": direct.CALIBRATION_SCHEMA,
        "a": a, "b": b, "converged": True, "iterations": int(optimized.nit),
        "formula": "softmax_within_race(a*log(p_uncalibrated)+b*log(p_market))",
        "bounds": [list(value) for value in direct.CALIBRATION_BOUNDS],
        "ridge": direct.CALIBRATION_RIDGE, "initial_parameters": [1., 0.],
        "fit_objective": float(optimized.fun), "race_nll": float(-np.log(probability[winners]).mean()),
        "nll_before": float(-np.log(probabilities[winners]).mean()),
        "market_nll": float(-np.log(market[winners]).mean()), "analytic_gradient": True,
        "at_bound": bool(min(abs(a - x) for x in direct.CALIBRATION_BOUNDS[0]) < 1e-5
                         or min(abs(b - x) for x in direct.CALIBRATION_BOUNDS[1]) < 1e-5),
        "feature_selection": False, "rule_selection": False, "test_outcomes_consumed": False,
        "odds_band_posthoc_multipliers": False}


def validate_bundle(bundle, calibrated=True):
    if (not isinstance(bundle, dict) or bundle.get("schema") != MODEL_SCHEMA
            or bundle.get("research_only") is not True or bundle.get("artifact_published") is not False
            or bundle.get("eligible_for_ev") is not False or bundle.get("actual_purchase_authorized") is not False):
        raise ValueError("win-four-research-only-bundle-required")
    candidate = bundle.get("candidate_id")
    columns, categorical = feature_contract(candidate)
    periods = fold_periods(bundle.get("fold_year"), candidate)
    encoder = bundle.get("encoder")
    if (bundle.get("periods") != periods or bundle.get("feature_columns") != columns
            or bundle.get("categorical_columns") != categorical or not isinstance(encoder, TrainEncoder)
            or encoder.columns != columns or encoder.categorical_columns != categorical or not encoder.fitted
            or bundle.get("parameters") != parameters(candidate) or bundle.get("rounds") != BOOST_ROUNDS
            or bundle.get("objective_description") != objective_description(candidate)
            or not isinstance(bundle.get("model_text"), str)
            or hashlib.sha256(bundle["model_text"].encode()).hexdigest() != bundle.get("model_text_sha256")):
        raise ValueError("win-four-fixed-bundle-contract-required")
    if calibrated:
        settings = bundle.get("conditioned_calibration")
        if (not isinstance(settings, dict) or set(settings) != {"kind", "a", "b"}
                or settings["kind"] != direct.CALIBRATION_SCHEMA or type(settings["a"]) not in (int, float)
                or type(settings["b"]) not in (int, float) or not .5 <= settings["a"] <= 2.
                or not -1. <= settings["b"] <= 1.):
            raise ValueError("win-four-fixed-calibration-required")


def predict_components(bundle, frame, calibrated=True):
    import lightgbm as lgb
    import numpy as np
    from scipy.special import expit
    validate_bundle(bundle, calibrated)
    market = market_values(frame)  # Does not access finish or payout.
    encoded = bundle["encoder"].transform(frame.loc[:, bundle["feature_columns"]])
    model = lgb.Booster(model_str=bundle["model_text"])
    if model.num_feature() != 50 or model.feature_name() != bundle["feature_columns"]:
        raise ValueError("win-four-serialized-model-feature-contract-invalid")
    residual = np.asarray(model.predict(encoded, raw_score=True, num_threads=2), float)
    if residual.shape != (len(frame),) or not np.isfinite(residual).all():
        raise ValueError("win-four-finite-residual-required")
    if objective_kind(bundle["candidate_id"]) == "binary":
        scores = np.log(market) - np.log1p(-market) + residual
        original = direct._normalize(frame, np.clip(expit(scores), *direct.SIGMOID_CLAMP))
    else:
        scores = np.log(market) + residual
        sizes = contiguous_groups(frame)
        original = direct._normalize(frame, np.clip(race_softmax(scores, sizes),
                                                    PROBABILITY_FLOOR, 1. - PROBABILITY_FLOOR))
    if calibrated:
        settings = bundle["conditioned_calibration"]
        probability = conditioned_probabilities(frame, original, market, settings["a"], settings["b"])
    else:
        probability = original.copy()
    return {"probability": probability, "p_original": original, "market_probability": market,
            "residual_score": residual, "direct_logit": scores}


def predict_model(bundle, frame, calibrated=True):
    return predict_components(bundle, frame, calibrated)["probability"]


def fit_model(train, cal, candidate_id, fold_year):
    """Only TRAIN/CAL exist in the fit API; CLI gate precedes every real fit."""
    import lightgbm as lgb
    import numpy as np
    from threadpoolctl import threadpool_limits
    periods = fold_periods(fold_year, candidate_id)
    train_receipt, cal_receipt = validate_frame(train), validate_frame(cal)
    if (not train.race_date.between(*periods["TRAIN"]).all() or not cal.race_date.between(*periods["CAL"]).all()
            or train.race_date.max() >= cal.race_date.min() or set(train.race_id) & set(cal.race_id)):
        raise ValueError("win-four-train-cal-fixed-disjoint-periods-required")
    columns, categorical = feature_contract(candidate_id)
    market_values(cal)
    market = market_values(train)
    offset = np.log(market) if objective_kind(candidate_id) == "race_nll" else np.log(market) - np.log1p(-market)
    labels = train.finish.eq(1).to_numpy(float)
    sizes = contiguous_groups(train, require_results=True)
    clock = time.perf_counter()
    encoder = TrainEncoder(columns, categorical).fit(train.loc[:, columns])
    matrix = encoder.transform(train.loc[:, columns])
    encoding_seconds = time.perf_counter() - clock
    clock = time.perf_counter()
    dataset = lgb.Dataset(matrix, label=labels, init_score=offset, group=sizes.tolist(),
        feature_name=columns, categorical_feature=categorical, free_raw_data=True)
    settings = dict(BASE_PARAMETERS)
    if objective_kind(candidate_id) == "race_nll":
        settings["objective"] = make_race_objective(sizes, labels)
    with threadpool_limits(limits=2):
        model = lgb.train(settings, dataset, num_boost_round=BOOST_ROUNDS)
    fit_seconds = time.perf_counter() - clock
    model_text = model.model_to_string()
    bundle = {"schema": MODEL_SCHEMA, "candidate_id": candidate_id, "fold_year": fold_year, "periods": periods,
        "feature_columns": columns, "categorical_columns": categorical, "encoder": encoder, "model_text": model_text,
        "model_text_sha256": hashlib.sha256(model_text.encode()).hexdigest(), "parameters": parameters(candidate_id),
        "rounds": BOOST_ROUNDS, "objective_description": objective_description(candidate_id),
        "research_only": True, "artifact_published": False, "eligible_for_ev": False, "actual_purchase_authorized": False,
        "independent_future_validation": False, "quote_scope": "historical-results-odds-not-executable-pre-race-quotes",
        "train": train_receipt, "cal": cal_receipt,
        "train_input_sha256": frame_hash(train.loc[:, columns]), "encoded_train_sha256": frame_hash(matrix),
        "train_labels_sha256": hashlib.sha256(labels.tobytes()).hexdigest(),
        "train_market_offset_sha256": hashlib.sha256(offset.tobytes()).hexdigest(),
        "train_group_sizes_sha256": hashlib.sha256(sizes.tobytes()).hexdigest(),
        "encoder_sha256": canonical_hash({"columns": columns, "categories": encoder.categories,
            "means": encoder.means, "standard_deviations": encoder.standard_deviations}),
        "fit_audit": {"fit_seconds": fit_seconds, "encoding_seconds": encoding_seconds,
            "actual_device": "cpu", "actual_native_threads": 2, "offset_in_training": True,
            "offset_explicitly_added_in_prediction": True, "objective_kind": objective_kind(candidate_id),
            "hessian_approximation": "diagonal; cross-runner terms omitted" if objective_kind(candidate_id) == "race_nll" else None,
            "hessian_floor": HESSIAN_FLOOR if objective_kind(candidate_id) == "race_nll" else None,
            "standardization_scope": "TRAIN-only; constants retained; missing remain NaN", "feature_selection": False,
            "cal_outcomes_consumed_in_fit": False, "test_outcomes_consumed_in_fit": False}}
    original = predict_model(bundle, cal.drop(columns=[name for name in ("finish", "tansho_payout") if name in cal]), False)
    clock = time.perf_counter()
    bundle["conditioned_calibration"], bundle["calibration_audit"] = fit_conditioned_calibration(
        cal, original, market_values(cal), periods["CAL"])
    bundle["calibration_audit"]["cal_seconds"] = time.perf_counter() - clock
    validate_bundle(bundle)
    return bundle


def validate_source_pins(request):
    pins = request.get("expected_source_hashes") if isinstance(request, dict) else None
    if not isinstance(pins, dict) or not REQUIRED_SOURCES <= pins.keys():
        raise ValueError("win-four-required-source-pins-missing")
    validated = direct.validate_source_pins(request)
    if request.get("protocol_sha256") != validated[PROTOCOL_SOURCE]:
        raise ValueError("win-four-request-protocol-hash-not-bound-to-pinned-config")
    return validated


def validate_phase1_receipt(path, expected_hash):
    path = direct._plain(path)
    if (not path.is_file() or path.suffix != ".json" or not path.resolve().is_relative_to(OUTPUT_ROOT.resolve())
            or file_hash(path) != expected_hash):
        raise ValueError("win-four-sealed-phase1-proof-hash-required")
    receipt = json.loads(path.read_text(encoding="utf-8"))
    # Recompute literal input, prediction, decision, timing and actual payout
    # evidence. A user-written JSON true flag is never sufficient authority.
    import run_db_shared_win_pipeline as pipeline
    recomputed = pipeline.verify_gate_proof(path)
    if (recomputed.get("proof") != receipt or recomputed.get("proof_sha256") != expected_hash):
        raise ValueError("win-four-phase1-proof-readback-mismatch")
    required_true = ("phase1_pass", "capture_before_post_verified", "prediction_before_post_verified",
                     "decision_before_post_verified", "actual_settlement_verified", "research_only")
    if (receipt.get("schema") != "db-shared-win-pipeline-proof-v1" or receipt.get("status") != "completed"
            or any(receipt.get(name) is not True for name in required_true)
            or receipt.get("purchase_authorized") is not False or receipt.get("actual_purchases") != 0
            or receipt.get("existing_forward_changed") is not False):
        raise ValueError("win-four-phase1-actual-pipeline-pass-required")
    return receipt


def validate_request(request, output):
    if not isinstance(request, dict) or frozenset(request) != REQUEST_KEYS or request.get("schema") != SCHEMA:
        raise ValueError("win-four-request-schema-invalid")
    if (not isinstance(request["job_id"], str) or direct.ID_RE.fullmatch(request["job_id"]) is None
            or request["device"] != "cpu" or type(request["n_threads"]) is not int or request["n_threads"] != 2):
        raise ValueError("win-four-fixed-execution-options-required")
    columns, categorical = feature_contract(request["candidate_id"])
    fold_periods(request["fold_year"], request["candidate_id"])
    if request["feature_columns"] != columns or request["categorical_columns"] != categorical:
        raise ValueError("win-four-exact-ordered-feature-contract-required")
    for name in ("frame_sha256", "protocol_sha256", "phase1_receipt_sha256"):
        if not isinstance(request[name], str) or direct.SHA_RE.fullmatch(request[name]) is None:
            raise ValueError("win-four-pinned-hash-required")
    path, destination = direct._plain(request["frame_path"]), direct._plain(output)
    boundary = OUTPUT_ROOT.resolve()
    if not path.is_file() or path.suffix != ".parquet" or not path.resolve().is_relative_to(boundary):
        raise ValueError("win-four-pinned-research-frame-required")
    if (destination.exists() or destination.suffix != ".json" or not destination.parent.is_dir()
            or not destination.resolve().is_relative_to(boundary) or destination.parent.resolve() == boundary):
        raise ValueError("win-four-new-nested-research-output-required")
    if any((destination.parent / f"{request['job_id']}-{suffix}").exists()
           for suffix in ("model.research.joblib", "predictions.parquet", "CAL.parquet")):
        raise ValueError("win-four-artifact-output-already-exists")
    if file_hash(path) != request["frame_sha256"]:
        raise ValueError("win-four-input-frame-hash-mismatch")
    return path, destination


def run_worker(request, output):
    started = time.perf_counter()
    sources = validate_source_pins(request)
    path, destination = validate_request(request, output)
    phase1 = validate_phase1_receipt(request["phase1_receipt_path"], request["phase1_receipt_sha256"])
    import joblib
    import lightgbm as lgb
    import numpy as np
    import pandas as pd
    frame = pd.read_parquet(path)
    positions = split_positions(frame, request["fold_year"], request["candidate_id"])
    columns = request["feature_columns"]
    if not set(columns) <= set(frame):
        raise ValueError("win-four-input-frame-model-columns-missing")
    # Stable ordering establishes one contiguous block per race for group NLL.
    parts = {name: frame.iloc[selected].sort_values(["race_date", "race_id", "horse_number"], kind="stable").reset_index(drop=True)
             for name, selected in positions.items()}
    bundle = fit_model(parts["TRAIN"], parts["CAL"], request["candidate_id"], request["fold_year"])
    bundle.update(protocol_sha256=request["protocol_sha256"], source_hashes=sources,
        frame_sha256=request["frame_sha256"], job_id=request["job_id"],
        phase1_receipt_sha256=request["phase1_receipt_sha256"], phase1_race_id=phase1.get("race_id"))
    model_path = destination.parent / f"{request['job_id']}-model.research.joblib"
    with model_path.open("xb") as stream:
        joblib.dump(bundle, stream, compress=3)
    reloaded = joblib.load(model_path)
    validate_bundle(reloaded)
    predictions, parity = {}, {}
    for name in ("CAL", "TEST"):
        inference = parts[name].drop(columns=[column for column in ("finish", "tansho_payout") if column in parts[name]])
        outputs, replay = predict_components(bundle, inference), predict_components(reloaded, inference)
        parity[name] = {key: float(np.max(np.abs(outputs[key] - replay[key]))) for key in outputs}
        if any(value != 0. for value in parity[name].values()):
            raise ValueError("win-four-model-serialization-parity-failed")
        result = parts[name][[column for column in ("race_id", "race_date", "horse_number", "finish", "odds",
            "tansho_payout", "expected_runner_count", "venue", "surface") if column in parts[name]]].copy()
        result["split"], result["candidate_id"], result["fold_year"] = name, request["candidate_id"], request["fold_year"]
        for key, values in outputs.items():
            result[key] = values
        predictions[name] = result
    artifact_paths = {"predictions": destination.parent / f"{request['job_id']}-predictions.parquet",
                      "calibration_predictions": destination.parent / f"{request['job_id']}-CAL.parquet"}
    for name, split in (("predictions", "TEST"), ("calibration_predictions", "CAL")):
        with artifact_paths[name].open("xb") as stream:
            predictions[split].to_parquet(stream, index=False, compression="zstd")
    if file_hash(path) != request["frame_sha256"] or validate_source_pins(request) != sources:
        raise ValueError("win-four-input-or-source-mutated-during-run")
    validate_phase1_receipt(request["phase1_receipt_path"], request["phase1_receipt_sha256"])
    result = {"schema": SCHEMA, "status": "completed", "job_id": request["job_id"],
        "candidate_id": request["candidate_id"], "fold_year": request["fold_year"],
        "protocol_sha256": request["protocol_sha256"], "request_sha256": canonical_hash(request),
        "frame_sha256": request["frame_sha256"], "source_hashes": sources,
        "phase1_receipt_sha256": request["phase1_receipt_sha256"],
        "feature_columns": columns, "feature_count": len(columns), "categorical_columns": request["categorical_columns"],
        "periods": fold_periods(request["fold_year"], request["candidate_id"]),
        "partitions": {name: validate_frame(part) for name, part in parts.items()},
        "rows_outside_fold": len(frame) - sum(len(part) for part in parts.values()),
        "partition_input_hashes": {name: frame_hash(part.loc[:, columns]) for name, part in parts.items()},
        "fit_audit": bundle["fit_audit"], "calibration_audit": bundle["calibration_audit"],
        "train_labels_sha256": bundle["train_labels_sha256"], "train_market_offset_sha256": bundle["train_market_offset_sha256"],
        "train_group_sizes_sha256": bundle["train_group_sizes_sha256"], "encoder_sha256": bundle["encoder_sha256"],
        "encoded_train_sha256": bundle["encoded_train_sha256"], "encoder_diagnostics": bundle["encoder"].diagnostics,
        "train_categories": bundle["encoder"].categories, "model_text_sha256": bundle["model_text_sha256"],
        "reload_parity": parity, "elapsed_seconds": time.perf_counter() - started,
        "runtime": {"device": "cpu", "native_threads": 2, "python_executable": sys.executable,
                    "lightgbm_version": lgb.__version__, "parameters": parameters(request["candidate_id"]), "rounds": BOOST_ROUNDS},
        "artifacts": {"model": {"path": str(model_path.resolve()), "sha256": file_hash(model_path), "schema": MODEL_SCHEMA},
            **{name: {"path": str(artifact_path.resolve()), "sha256": file_hash(artifact_path),
                      "rows": len(predictions["TEST" if name == "predictions" else "CAL"])}
               for name, artifact_path in artifact_paths.items()}},
        "proofs": {"phase1_actual_pipeline_pass_required": True, "fixed_feature_contract": True,
            "train_only_categories_and_scaling": True, "missing_values_zero_imputed": False,
            "test_labels_used_in_prediction": False, "all_partitions_disjoint_whole_races": True,
            "offset_train_predict_consistent": True, "model_reload_probability_parity": True,
            "cal_only_market_conditioned_calibration": True, "research_only": True, "artifact_published": False,
            "model_activated": False, "production_changed": False, "forward_collection_changed": False,
            "eligible_for_ev": False, "actual_purchase_authorized": False}}
    direct._new_json(destination, result)
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--request", required=True)
    parser.add_argument("--output", required=True)
    args = parser.parse_args()
    result = run_worker(json.loads(Path(args.request).read_text(encoding="utf-8")), args.output)
    print(json.dumps({"job_id": result["job_id"], "status": result["status"],
                      "elapsed_seconds": result["elapsed_seconds"]}), flush=True)


if __name__ == "__main__":
    main()
