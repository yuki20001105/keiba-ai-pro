"""Frozen, direct winner-probability experiments with a market logit offset.

TRAIN fits one deterministic CPU LightGBM binary model. CAL fits a bounded,
market-conditioned two-parameter calibration using race-level NLL and a fixed
ridge penalty. A temperature-only diagnostic is fitted separately. SELECT and
TEST labels are never passed to
prediction, category learning, feature selection or fitting. These are reused
retrospective research artifacts, not production/EV/purchase authority. There
are no database, network, model activation or forward-collector APIs here.
"""
from __future__ import annotations

import argparse
from dataclasses import dataclass, field
import hashlib
import json
from pathlib import Path
import re
import sys
import time

ROOT = Path(__file__).resolve().parents[1]
OUTPUT_ROOT = ROOT / "reports/generated"
SCHEMA = "db-shared-win-direct-worker-v1"
MODEL_SCHEMA = "research-db-shared-win-direct-v1"
MARKET_COLUMNS = ("normalized_market_probability", "log_odds", "market_rank", "field_size")
PERIODS = {"TRAIN": ("20160101", "20201231"), "CAL": ("20210101", "20221231"),
           "SELECT": ("20230101", "20241231"), "TEST": ("20250101", "20260913")}
CANDIDATES = ("market_only", "market46", "market46_extended")
REQUEST_KEYS = frozenset({"schema", "job_id", "candidate_id", "frame_path", "frame_sha256",
    "protocol_sha256", "expected_source_hashes", "feature_columns", "categorical_columns", "device", "n_threads"})
REQUIRED_SOURCES = frozenset({"scripts/db_shared_win_direct_worker.py",
    "python-api/experiments/features.py", "python-api/experiments/search_features.py"})
SHA_RE = re.compile(r"[0-9a-f]{64}")
ID_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9_-]{0,79}")
TEMPERATURE_BOUNDS = (.5, 2.)
CALIBRATION_BOUNDS = ((.5, 2.), (-1., 1.))
CALIBRATION_RIDGE = .01
CALIBRATION_SCHEMA = "market-conditioned-log-linear-v1"
SIGMOID_CLAMP = (1e-12, 1. - 1e-12)
SEED = 20261012
PARAMETERS = {"objective": "binary", "learning_rate": .03, "num_leaves": 15,
    "min_data_in_leaf": 200, "lambda_l2": 10., "max_bin": 63,
    "num_threads": 2, "device_type": "cpu", "deterministic": True,
    "force_col_wise": True, "seed": SEED, "data_random_seed": SEED,
    "feature_fraction_seed": SEED, "bagging_seed": SEED, "verbosity": -1,
    "feature_fraction": 1., "bagging_fraction": 1., "boost_from_average": False}
BOOST_ROUNDS = 150
OBJECTIVE = "runner-weighted binary logit-offset + sigmoid + race normalization; not multinomial fitting"


def file_hash(path):
    digest = hashlib.sha256()
    with Path(path).open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def canonical_hash(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False,
        separators=(",", ":"), allow_nan=False).encode()).hexdigest()


def frame_hash(frame):
    import pandas as pd
    header = canonical_hash({"columns": list(frame), "dtypes": [str(value) for value in frame.dtypes]})
    return hashlib.sha256(header.encode() + pd.util.hash_pandas_object(frame, index=False).to_numpy().tobytes()).hexdigest()


def feature_contract(candidate_id):
    for directory in (ROOT / "python-api", ROOT / "keiba"):
        if str(directory) not in sys.path:
            sys.path.insert(0, str(directory))
    from experiments.features import APTITUDE_COLUMNS, BASE_CATEGORICAL, FEATURE_GROUPS, HISTORY_COLUMNS
    from experiments.search_features import SEARCH_FEATURES
    raw47 = FEATURE_GROUPS["base"] + HISTORY_COLUMNS
    safe46 = [name for name in raw47 if name != "safe_past5_top3_rate"]
    if len(raw47) != 47 or len(safe46) != 46 or len(APTITUDE_COLUMNS) != 13 or len(SEARCH_FEATURES) != 29:
        raise ValueError("win-direct-source-feature-contract-changed")
    if candidate_id not in CANDIDATES:
        raise ValueError("win-direct-fixed-candidate-required")
    columns = list(MARKET_COLUMNS)
    if candidate_id != "market_only":
        columns += safe46
    if candidate_id == "market46_extended":
        columns += list(APTITUDE_COLUMNS) + list(SEARCH_FEATURES)
    categorical = [name for name in BASE_CATEGORICAL if name in columns]
    if len(columns) != {"market_only": 4, "market46": 50, "market46_extended": 92}[candidate_id]:
        raise ValueError("win-direct-ordered-feature-count-invalid")
    return columns, categorical


def validate_source_pins(request):
    pins = request.get("expected_source_hashes") if isinstance(request, dict) else None
    if not isinstance(pins, dict) or not REQUIRED_SOURCES <= pins.keys():
        raise ValueError("win-direct-required-source-pins-missing")
    for relative, expected in pins.items():
        if (not isinstance(relative, str) or "\\" in relative or Path(relative).is_absolute()
                or ".." in Path(relative).parts or not isinstance(expected, str) or SHA_RE.fullmatch(expected) is None):
            raise ValueError("win-direct-source-pin-schema-invalid")
        source = _plain(ROOT / relative)
        if not source.is_file() or not source.resolve().is_relative_to(ROOT.resolve()) or file_hash(source) != expected:
            raise ValueError("win-direct-source-pin-mismatch")
    return pins.copy()


def _plain(path):
    path = Path(path)
    if any(value.is_symlink() for value in (path, *path.parents)):
        raise ValueError("win-direct-symlink-path-forbidden")
    return path


def validate_request(request, output):
    if not isinstance(request, dict) or frozenset(request) != REQUEST_KEYS or request.get("schema") != SCHEMA:
        raise ValueError("win-direct-request-schema-invalid")
    if (not isinstance(request["job_id"], str) or ID_RE.fullmatch(request["job_id"]) is None
            or request["candidate_id"] not in CANDIDATES or request["device"] != "cpu"
            or type(request["n_threads"]) is not int or request["n_threads"] != 2):
        raise ValueError("win-direct-fixed-execution-options-required")
    for name in ("frame_sha256", "protocol_sha256"):
        if not isinstance(request[name], str) or SHA_RE.fullmatch(request[name]) is None:
            raise ValueError("win-direct-pinned-hash-required")
    columns, categorical = feature_contract(request["candidate_id"])
    if request["feature_columns"] != columns or request["categorical_columns"] != categorical:
        raise ValueError("win-direct-exact-ordered-feature-contract-required")
    frame_path, destination = _plain(request["frame_path"]), _plain(output)
    boundary = OUTPUT_ROOT.resolve()
    if (not frame_path.is_file() or frame_path.suffix != ".parquet"
            or not frame_path.resolve().is_relative_to(boundary)):
        raise ValueError("win-direct-pinned-research-frame-required")
    if (destination.suffix != ".json" or destination.exists() or not destination.parent.is_dir()
            or not destination.resolve().is_relative_to(boundary) or destination.parent.resolve() == boundary):
        raise ValueError("win-direct-new-nested-research-output-required")
    names = (f"{request['job_id']}-model.research.joblib", f"{request['job_id']}-predictions.parquet",
             f"{request['job_id']}-CAL.parquet")
    if destination == frame_path or any((destination.parent / name).exists() for name in names):
        raise ValueError("win-direct-output-already-exists-or-input-overlap")
    if file_hash(frame_path) != request["frame_sha256"]:
        raise ValueError("win-direct-input-frame-hash-mismatch")
    return frame_path, destination


def validate_frame(frame, require_results=True):
    """Fail closed on whole race validity; inference never reads result fields."""
    import numpy as np
    import pandas as pd
    required = {"race_id", "race_date", "horse_number", "odds", "expected_runner_count", "venue", "surface"}
    if require_results:
        required |= {"finish", "tansho_payout"}
    if (not isinstance(frame, pd.DataFrame) or frame.empty or frame.columns.duplicated().any()
            or not required <= set(frame.columns)):
        raise ValueError("win-direct-required-unique-frame-columns")
    for column in ("race_id", "race_date", "venue", "surface"):
        if not frame[column].map(lambda value: isinstance(value, str) and bool(value.strip())).all():
            raise ValueError("win-direct-valid-identities-required")
    if not frame.race_id.str.fullmatch(r"\d{12}").all() or not frame.race_date.str.fullmatch(r"\d{8}").all():
        raise ValueError("win-direct-race-id-date-format-invalid")
    dates = pd.to_datetime(frame.race_date, format="%Y%m%d", errors="coerce")
    if dates.isna().any() or not dates.dt.strftime("%Y%m%d").equals(frame.race_date.astype(str)):
        raise ValueError("win-direct-valid-dates-required")
    values = {name: pd.to_numeric(frame[name], errors="coerce").to_numpy(float)
              for name in ("horse_number", "odds", "expected_runner_count")}
    if (not all(np.isfinite(value).all() for value in values.values())
            or not np.equal(values["horse_number"], np.floor(values["horse_number"])).all()
            or not np.equal(values["expected_runner_count"], np.floor(values["expected_runner_count"])).all()
            or not ((values["horse_number"] >= 1) & (values["horse_number"] <= 18)).all()
            or not ((values["expected_runner_count"] >= 2) & (values["expected_runner_count"] <= 18)).all()
            or (values["odds"] < 1).any() or frame.duplicated(["race_id", "horse_number"]).any()):
        raise ValueError("win-direct-runner-key-odds-invalid")
    grouped = frame.groupby("race_id", sort=False, observed=True)
    if any(grouped[name].nunique(dropna=False).ne(1).any()
           for name in ("race_date", "venue", "surface", "expected_runner_count")):
        raise ValueError("win-direct-inconsistent-race-metadata")
    if not grouped.size().eq(grouped.expected_runner_count.first()).all():
        raise ValueError("win-direct-whole-race-cohort-required")
    if require_results:
        finish = pd.to_numeric(frame.finish, errors="coerce").to_numpy(float)
        if (not np.isfinite(finish).all() or (finish < 1).any()
                or not np.equal(finish, np.floor(finish)).all() or (finish > values["expected_runner_count"]).any()
                or pd.Series(finish == 1).groupby(frame.race_id.reset_index(drop=True)).sum().ne(1).any()):
            raise ValueError("win-direct-single-confirmed-winner-required")
        payout = pd.to_numeric(frame.tansho_payout, errors="coerce").to_numpy(float)
        winner = finish == 1
        winners = payout[winner]
        losers = payout[~winner]
        if (not np.isfinite(winners).all() or (winners < 100).any()
                or not np.equal(winners, np.floor(winners)).all() or (winners % 10 != 0).any()
                or not (np.isnan(losers) | (losers == 0)).all()):
            raise ValueError("win-direct-official-single-winner-payout-required")
    return {"runner_count": len(frame), "race_count": int(frame.race_id.nunique()),
            "date_count": int(frame.race_date.nunique()), "date_from": frame.race_date.min(), "date_to": frame.race_date.max()}


def split_positions(frame):
    import numpy as np
    validate_frame(frame, require_results=True)
    positions = {name: np.flatnonzero(frame.race_date.between(*bounds).to_numpy()) for name, bounds in PERIODS.items()}
    if any(len(selected) < 2 for selected in positions.values()):
        raise ValueError("win-direct-nonempty-fixed-periods-required")
    names = list(PERIODS)
    for left, right in zip(names[:-1], names[1:]):
        a, b = frame.iloc[positions[left]], frame.iloc[positions[right]]
        if a.race_date.max() >= b.race_date.min() or set(a.race_id) & set(b.race_id):
            raise ValueError("win-direct-disjoint-chronological-periods-required")
    assigned = sum(len(value) for value in positions.values())
    if assigned != len(frame):
        raise ValueError("win-direct-input-rows-outside-fixed-periods")
    return positions


def market_values(frame):
    import numpy as np
    import pandas as pd
    validate_frame(frame, require_results=False)
    odds = frame.odds.to_numpy(float)
    groups = pd.factorize(frame.race_id, sort=False)[0]
    inverse = 1. / odds
    market = inverse / np.bincount(groups, weights=inverse)[groups]
    # Equal market quotes share the minimum rank. Stable horse-number tie
    # breaking belongs to bet selection, not this explanatory model input.
    rank = frame.odds.groupby(frame.race_id, sort=False).rank(method="min", ascending=True).to_numpy(float)
    expected = {"normalized_market_probability": market, "log_odds": np.log(odds),
                "market_rank": rank, "field_size": frame.expected_runner_count.to_numpy(float)}
    for name, values in expected.items():
        if name not in frame or not np.allclose(frame[name].to_numpy(float), values, rtol=1e-7, atol=1e-8):
            raise ValueError("win-direct-market-inputs-not-derived-from-quotes")
    if not np.isfinite(market).all() or (market <= 0).any() or (market >= 1).any():
        raise ValueError("win-direct-market-probability-invalid")
    return market


@dataclass
class TrainEncoder:
    """Fixed columns, TRAIN-only scaling/categories, unknowns remain NaN."""
    columns: list[str]
    categorical_columns: list[str]
    categories: dict = field(default_factory=dict)
    means: dict = field(default_factory=dict)
    standard_deviations: dict = field(default_factory=dict)
    diagnostics: dict = field(default_factory=dict)
    fitted: bool = False

    def fit(self, frame):
        import numpy as np
        import pandas as pd
        if self.fitted or len(set(self.columns)) != len(self.columns) or not set(self.categorical_columns) <= set(self.columns):
            raise ValueError("win-direct-encoder-new-fixed-contract-required")
        for name in self.columns:
            if name not in frame:
                raise ValueError("win-direct-missing-model-input")
            if name in self.categorical_columns:
                values = frame[name].astype("string").str.strip().replace("", pd.NA)
                self.categories[name] = sorted(values.dropna().unique().tolist())
                self.diagnostics[name] = {"observed_categories": len(self.categories[name])}
            else:
                values = pd.to_numeric(frame[name], errors="coerce").replace([np.inf, -np.inf], np.nan)
                present = values.dropna()
                mean = float(present.mean()) if len(present) else 0.
                std = float(present.std(ddof=0)) if len(present) else 1.
                self.means[name], self.standard_deviations[name] = mean, std if std > 0 else 1.
                self.diagnostics[name] = {"missing_count": int(values.isna().sum()), "all_missing": not len(present),
                                          "constant_observed": bool(len(present) and present.nunique() == 1), "retained": True}
        self.fitted = True
        return self

    def transform(self, frame):
        import numpy as np
        import pandas as pd
        if not self.fitted or frame.columns.duplicated().any():
            raise ValueError("win-direct-fitted-encoder-required")
        result = pd.DataFrame(index=frame.index)
        for name in self.columns:
            if name not in frame:
                raise ValueError("win-direct-missing-model-input")
            if name in self.categorical_columns:
                mapping = {value: index for index, value in enumerate(self.categories[name])}
                result[name] = frame[name].astype("string").str.strip().map(mapping).astype(float)
            else:
                values = pd.to_numeric(frame[name], errors="coerce").replace([np.inf, -np.inf], np.nan)
                result[name] = (values - self.means[name]) / self.standard_deviations[name]
        return result.loc[:, self.columns]


def _normalize(frame, values):
    import numpy as np
    import pandas as pd
    vector = np.asarray(values, float)
    if vector.shape != (len(frame),) or not np.isfinite(vector).all() or (vector <= 0).any():
        raise ValueError("win-direct-finite-positive-probabilities-required")
    groups = pd.factorize(frame.race_id, sort=False)[0]
    result = vector / np.bincount(groups, weights=vector)[groups]
    if not np.isfinite(result).all() or (result <= 0).any() or (result >= 1).any():
        raise ValueError("win-direct-race-probabilities-invalid")
    return result


def temperature_probabilities(frame, probabilities, temperature):
    import numpy as np
    import pandas as pd
    if not isinstance(temperature, (int, float)) or isinstance(temperature, bool) or not .5 <= temperature <= 2.:
        raise ValueError("win-direct-fixed-temperature-bounds-required")
    vector = np.asarray(probabilities, float)
    if vector.shape != (len(frame),) or not np.isfinite(vector).all() or (vector <= 0).any() or (vector >= 1).any():
        raise ValueError("win-direct-temperature-input-invalid")
    logp = np.log(vector) / temperature
    groups = pd.factorize(frame.race_id, sort=False)[0]
    maxima = np.full(groups.max() + 1, -np.inf)
    np.maximum.at(maxima, groups, logp)
    return _normalize(frame, np.exp(logp - maxima[groups]))


def conditioned_probabilities(frame, probabilities, market, a, b):
    import numpy as np
    import pandas as pd
    if (type(a) not in (int, float) or type(b) not in (int, float)
            or not .5 <= a <= 2. or not -1. <= b <= 1.):
        raise ValueError("win-direct-fixed-conditioned-bounds-required")
    vectors = [np.asarray(value, float) for value in (probabilities, market)]
    if any(value.shape != (len(frame),) or not np.isfinite(value).all()
           or (value <= 0).any() or (value >= 1).any() for value in vectors):
        raise ValueError("win-direct-conditioned-input-invalid")
    groups = pd.factorize(frame.race_id, sort=False)[0]
    if any(not np.allclose(np.bincount(groups, weights=value), 1., rtol=0, atol=1e-10) for value in vectors):
        raise ValueError("win-direct-conditioned-race-normalization-required")
    scores = a * np.log(vectors[0]) + b * np.log(vectors[1])
    maxima = np.full(groups.max() + 1, -np.inf)
    np.maximum.at(maxima, groups, scores)
    return _normalize(frame, np.exp(scores - maxima[groups]))


def fit_conditioned_calibration(frame, probabilities, market):
    """One locked CAL-only smooth market-conditioned rule, never odds-band fits."""
    import numpy as np
    from scipy.optimize import minimize
    validate_frame(frame, require_results=True)
    if not frame.race_date.between(*PERIODS["CAL"]).all():
        raise ValueError("win-direct-conditioned-cal-period-required")
    probabilities, market = (np.asarray(value, float) for value in (probabilities, market))
    # Validate input vectors before optimizing, including whole race sums.
    conditioned_probabilities(frame, probabilities, market, 1., 0.)
    logp, logm = np.log(probabilities), np.log(market)
    winners = frame.finish.eq(1).to_numpy()
    races = int(winners.sum())
    def objective(parameters):
        a, b = map(float, parameters)
        calibrated = conditioned_probabilities(frame, probabilities, market, a, b)
        nll = float(-np.log(calibrated[winners]).mean())
        ridge = CALIBRATION_RIDGE * ((a - 1.) ** 2 + b ** 2)
        gradient = np.array([(np.sum(calibrated * logp) - np.sum(logp[winners])) / races,
                             (np.sum(calibrated * logm) - np.sum(logm[winners])) / races])
        gradient += 2. * CALIBRATION_RIDGE * np.array([a - 1., b])
        return nll + ridge, gradient
    optimized = minimize(objective, np.array([1., 0.]), jac=True, method="L-BFGS-B",
                         bounds=CALIBRATION_BOUNDS, options={"maxiter": 200, "ftol": 1e-12, "gtol": 1e-7})
    if not optimized.success or not np.isfinite(optimized.fun) or not np.isfinite(optimized.x).all():
        raise ValueError("win-direct-conditioned-calibration-not-converged")
    a, b = map(float, optimized.x)
    calibrated = conditioned_probabilities(frame, probabilities, market, a, b)
    near_a = min(abs(a - bound) for bound in CALIBRATION_BOUNDS[0]) < 1e-5
    near_b = min(abs(b - bound) for bound in CALIBRATION_BOUNDS[1]) < 1e-5
    return {"kind": CALIBRATION_SCHEMA, "a": a, "b": b}, {
        "scope": "CAL-only", "kind": CALIBRATION_SCHEMA, "converged": True,
        "formula": "softmax_within_race(a*log(p_uncalibrated)+b*log(p_market))",
        "a": a, "b": b, "bounds": [list(value) for value in CALIBRATION_BOUNDS],
        "ridge": CALIBRATION_RIDGE, "ridge_formula": "0.01*((a-1)^2+b^2)",
        "fit_objective": float(optimized.fun), "race_nll": float(-np.log(calibrated[winners]).mean()),
        "nll_before": float(-np.log(probabilities[winners]).mean()),
        "market_nll": float(-np.log(market[winners]).mean()), "iterations": int(optimized.nit),
        "analytic_gradient": True, "initial_parameters": [1., 0.],
        "at_bound": bool(near_a or near_b), "at_a_bound": bool(near_a), "at_b_bound": bool(near_b),
        "bounds_are_warning_not_cohort_filter": True, "odds_band_posthoc_multipliers": False,
        "feature_selection": False, "rule_selection": False,
        "select_outcomes_consumed": False, "test_outcomes_consumed": False}


def validate_bundle(bundle, calibrated=True):
    if (not isinstance(bundle, dict) or bundle.get("schema") != MODEL_SCHEMA
            or bundle.get("research_only") is not True or bundle.get("artifact_published") is not False
            or bundle.get("eligible_for_ev") is not False or bundle.get("actual_purchase_authorized") is not False
            or bundle.get("objective_description") != OBJECTIVE):
        raise ValueError("win-direct-research-only-bundle-required")
    columns, categorical = feature_contract(bundle.get("candidate_id"))
    encoder = bundle.get("encoder")
    if (bundle.get("feature_columns") != columns or bundle.get("categorical_columns") != categorical
            or not isinstance(encoder, TrainEncoder) or encoder.columns != columns
            or encoder.categorical_columns != categorical or not encoder.fitted
            or bundle.get("parameters") != PARAMETERS or bundle.get("rounds") != BOOST_ROUNDS
            or not isinstance(bundle.get("model_text"), str)):
        raise ValueError("win-direct-fixed-bundle-contract-required")
    if calibrated:
        value = bundle.get("temperature")
        if type(value) not in (int, float) or not .5 <= value <= 2.:
            raise ValueError("win-direct-calibrated-temperature-required")
        calibrator = bundle.get("conditioned_calibration")
        if (not isinstance(calibrator, dict) or set(calibrator) != {"kind", "a", "b"}
                or calibrator["kind"] != CALIBRATION_SCHEMA or type(calibrator["a"]) not in (int, float)
                or type(calibrator["b"]) not in (int, float) or not .5 <= calibrator["a"] <= 2.
                or not -1. <= calibrator["b"] <= 1.):
            raise ValueError("win-direct-conditioned-calibration-required")


def predict_components(bundle, frame, calibrated=True):
    import lightgbm as lgb
    import numpy as np
    from scipy.special import expit
    validate_bundle(bundle, calibrated=calibrated)
    # No slicing, validation or computation touches finish/payout/result columns.
    market = market_values(frame)
    encoded = bundle["encoder"].transform(frame.loc[:, bundle["feature_columns"]])
    booster = lgb.Booster(model_str=bundle["model_text"])
    if booster.num_feature() != len(bundle["feature_columns"]) or booster.feature_name() != bundle["feature_columns"]:
        raise ValueError("win-direct-serialized-booster-schema-invalid")
    residual = np.asarray(booster.predict(encoded, raw_score=True, num_threads=2), float)
    if residual.shape != (len(frame),) or not np.isfinite(residual).all():
        raise ValueError("win-direct-residual-prediction-invalid")
    logits = np.log(market) - np.log1p(-market) + residual
    original = _normalize(frame, np.clip(expit(logits), *SIGMOID_CLAMP))
    if calibrated:
        settings = bundle["conditioned_calibration"]
        probabilities = conditioned_probabilities(frame, original, market, settings["a"], settings["b"])
        temperature_only = temperature_probabilities(frame, original, bundle["temperature"])
    else:
        probabilities, temperature_only = original.copy(), original.copy()
    return {"probability": probabilities, "p_original": original, "market_probability": market,
            "temperature_probability": temperature_only, "residual_score": residual, "direct_logit": logits}


def predict_model(bundle, frame, calibrated=True):
    return predict_components(bundle, frame, calibrated=calibrated)["probability"]


def fit_model(train, cal, candidate_id):
    """Only TRAIN and CAL arguments exist; SELECT/TEST cannot train this API."""
    import lightgbm as lgb
    import numpy as np
    from scipy.optimize import minimize_scalar
    from threadpoolctl import threadpool_limits
    train_receipt, cal_receipt = validate_frame(train), validate_frame(cal)
    if (not train.race_date.between(*PERIODS["TRAIN"]).all() or not cal.race_date.between(*PERIODS["CAL"]).all()
            or train.race_date.max() >= cal.race_date.min() or set(train.race_id) & set(cal.race_id)):
        raise ValueError("win-direct-train-cal-fixed-disjoint-periods-required")
    columns, categorical = feature_contract(candidate_id)
    market_values(cal)
    market = market_values(train)
    offset = np.log(market) - np.log1p(-market)
    labels = train.finish.eq(1).to_numpy(float)
    clock = time.perf_counter()
    encoder = TrainEncoder(columns, categorical).fit(train.loc[:, columns])
    matrix = encoder.transform(train.loc[:, columns])
    encoding_seconds = time.perf_counter() - clock
    clock = time.perf_counter()
    dataset = lgb.Dataset(matrix, label=labels, init_score=offset, feature_name=columns,
                          categorical_feature=categorical, free_raw_data=True)
    with threadpool_limits(limits=2):
        model = lgb.train(dict(PARAMETERS), dataset, num_boost_round=BOOST_ROUNDS)
    fit_seconds = time.perf_counter() - clock
    bundle = {"schema": MODEL_SCHEMA, "candidate_id": candidate_id, "feature_columns": columns,
        "categorical_columns": categorical, "encoder": encoder, "model_text": model.model_to_string(),
        "parameters": dict(PARAMETERS), "rounds": BOOST_ROUNDS, "objective_description": OBJECTIVE,
        "research_only": True, "artifact_published": False, "eligible_for_ev": False,
        "actual_purchase_authorized": False, "independent_future_validation": False,
        "quote_scope": "historical-results-odds-not-executable-pre-race-quotes",
        "train": train_receipt, "cal": cal_receipt,
        "train_input_sha256": frame_hash(train.loc[:, columns]), "encoded_train_sha256": frame_hash(matrix),
        "train_labels_sha256": hashlib.sha256(labels.tobytes()).hexdigest(),
        "train_market_offset_sha256": hashlib.sha256(offset.tobytes()).hexdigest(),
        "model_text_sha256": hashlib.sha256(model.model_to_string().encode()).hexdigest(),
        "encoder_sha256": canonical_hash({"columns": columns, "categories": encoder.categories,
            "means": encoder.means, "standard_deviations": encoder.standard_deviations}),
        "fit_audit": {"fit_seconds": fit_seconds, "encoding_seconds": encoding_seconds,
            "actual_device": model.params["device_type"], "actual_native_threads": model.params["num_threads"],
            "offset_in_training": True, "offset_explicitly_added_in_prediction": True,
            "standardization_scope": "TRAIN-only; constants retained; missing values remain NaN",
            "feature_selection": False, "cal_outcomes_consumed_in_fit": False,
            "select_outcomes_consumed_in_fit": False, "test_outcomes_consumed_in_fit": False}}
    clock = time.perf_counter()
    original = predict_model(bundle, cal.drop(columns=["finish", "tansho_payout"]), calibrated=False)
    winners = cal.finish.eq(1).to_numpy()
    def loss(value):
        return float(-np.log(temperature_probabilities(cal, original, float(value))[winners]).mean())
    optimized = minimize_scalar(loss, bounds=TEMPERATURE_BOUNDS, method="bounded",
                                options={"xatol": 1e-7, "maxiter": 200})
    if not optimized.success or not np.isfinite(optimized.fun) or not np.isfinite(optimized.x):
        raise ValueError("win-direct-calibration-not-converged")
    bundle["temperature"] = float(optimized.x)
    bundle["temperature_calibration_audit"] = {"scope": "CAL-only", "converged": True,
        "temperature_bounds": list(TEMPERATURE_BOUNDS), "temperature": float(optimized.x),
        "race_nll": float(optimized.fun), "nll_before": loss(1.),
        "at_bound": bool(min(abs(optimized.x - value) for value in TEMPERATURE_BOUNDS) < 1e-5),
        "cal_seconds": time.perf_counter() - clock, "rule_selection": False,
        "feature_selection": False, "select_outcomes_consumed": False, "test_outcomes_consumed": False}
    clock = time.perf_counter()
    bundle["conditioned_calibration"], bundle["calibration_audit"] = fit_conditioned_calibration(cal, original, market_values(cal))
    bundle["calibration_audit"]["cal_seconds"] = time.perf_counter() - clock
    validate_bundle(bundle)
    return bundle


def _new_json(path, value):
    with Path(path).open("x", encoding="utf-8") as stream:
        json.dump(value, stream, ensure_ascii=False, sort_keys=True, indent=2, allow_nan=False)


def run_worker(request, output):
    started = time.perf_counter()
    sources = validate_source_pins(request)
    path, destination = validate_request(request, output)
    import joblib
    import numpy as np
    import pandas as pd
    import lightgbm as lgb
    frame = pd.read_parquet(path)
    positions = split_positions(frame)
    columns = request["feature_columns"]
    if not set(columns) <= set(frame):
        raise ValueError("win-direct-input-frame-model-columns-missing")
    parts = {name: frame.iloc[selected].reset_index(drop=True) for name, selected in positions.items()}
    bundle = fit_model(parts["TRAIN"], parts["CAL"], request["candidate_id"])
    bundle.update(protocol_sha256=request["protocol_sha256"], source_hashes=sources,
                  frame_sha256=request["frame_sha256"], job_id=request["job_id"])
    predictions, parity = {}, {}
    model_path = destination.parent / f"{request['job_id']}-model.research.joblib"
    # Exclusive creation never replaces any previous experiment/artifact.
    with model_path.open("xb") as stream:
        joblib.dump(bundle, stream, compress=3)
    reloaded = joblib.load(model_path)
    validate_bundle(reloaded)
    for name in ("CAL", "SELECT", "TEST"):
        inference = parts[name].drop(columns=["finish", "tansho_payout"])
        outputs = predict_components(bundle, inference)
        replay = predict_components(reloaded, inference)
        parity[name] = {key: float(np.max(np.abs(outputs[key] - replay[key]))) for key in outputs}
        if any(value != 0. for value in parity[name].values()):
            raise ValueError("win-direct-model-serialization-parity-failed")
        result = parts[name][[column for column in ("race_id", "race_date", "horse_number", "finish", "odds",
            "tansho_payout", "expected_runner_count", "raw_score", "venue", "surface") if column in parts[name]]].copy()
        result["split"], result["candidate_id"] = name, request["candidate_id"]
        for key, vector in outputs.items():
            result[key] = vector
        predictions[name] = result
    prediction_path = destination.parent / f"{request['job_id']}-predictions.parquet"
    cal_path = destination.parent / f"{request['job_id']}-CAL.parquet"
    for artifact_path, values in ((prediction_path, pd.concat([predictions["SELECT"], predictions["TEST"]], ignore_index=True)),
                                  (cal_path, predictions["CAL"])):
        with artifact_path.open("xb") as stream:
            values.to_parquet(stream, index=False, compression="zstd")
    if file_hash(path) != request["frame_sha256"] or validate_source_pins(request) != sources:
        raise ValueError("win-direct-input-or-source-mutated-during-run")
    result = {"schema": SCHEMA, "status": "completed", "job_id": request["job_id"],
        "candidate_id": request["candidate_id"], "protocol_sha256": request["protocol_sha256"],
        "request_sha256": canonical_hash(request), "frame_sha256": request["frame_sha256"],
        "source_hashes": sources, "feature_columns": columns, "feature_count": len(columns),
        "categorical_columns": request["categorical_columns"], "periods": PERIODS,
        "partitions": {name: validate_frame(part) for name, part in parts.items()},
        "partition_input_hashes": {name: frame_hash(part.loc[:, columns]) for name, part in parts.items()},
        "fit_audit": bundle["fit_audit"], "calibration_audit": bundle["calibration_audit"],
        "temperature_calibration_audit": bundle["temperature_calibration_audit"],
        "train_labels_sha256": bundle["train_labels_sha256"], "train_market_offset_sha256": bundle["train_market_offset_sha256"],
        "encoded_train_sha256": bundle["encoded_train_sha256"], "encoder_sha256": bundle["encoder_sha256"],
        "encoder_diagnostics": bundle["encoder"].diagnostics, "train_categories": bundle["encoder"].categories,
        "model_text_sha256": bundle["model_text_sha256"], "reload_parity": parity,
        "runtime": {"device": "cpu", "native_threads": 2, "python_executable": sys.executable,
                    "lightgbm_version": lgb.__version__, "parameters": dict(PARAMETERS), "rounds": BOOST_ROUNDS},
        "artifacts": {"model": {"path": str(model_path.resolve()), "sha256": file_hash(model_path), "schema": MODEL_SCHEMA},
            "predictions": {"path": str(prediction_path.resolve()), "sha256": file_hash(prediction_path),
                            "rows": len(predictions["SELECT"]) + len(predictions["TEST"])},
            "calibration_predictions": {"path": str(cal_path.resolve()), "sha256": file_hash(cal_path), "rows": len(predictions["CAL"])}},
        "elapsed_seconds": time.perf_counter() - started,
        "proofs": {"fixed_feature_contract": True, "train_only_categories_and_scaling": True,
            "missing_values_zero_imputed": False, "select_test_labels_used_in_prediction": False,
            "all_partitions_disjoint_whole_races": True, "offset_train_predict_consistent": True,
            "cal_only_temperature_diagnostic": True, "cal_only_market_conditioned_calibration": True,
            "model_reload_probability_parity": True,
            "research_only": True, "artifact_published": False, "model_activated": False,
            "production_changed": False, "forward_collection_changed": False,
            "eligible_for_ev": False, "actual_purchase_authorized": False}}
    _new_json(destination, result)
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--request", required=True)
    parser.add_argument("--output", required=True)
    args = parser.parse_args()
    request = json.loads(Path(args.request).read_text(encoding="utf-8"))
    result = run_worker(request, args.output)
    print(json.dumps({"job_id": result["job_id"], "status": result["status"],
                      "elapsed_seconds": result["elapsed_seconds"]}), flush=True)


if __name__ == "__main__":
    # Joblib records the importable module name rather than ephemeral __main__.
    sys.modules.setdefault("db_shared_win_direct_worker", sys.modules[__name__])
    TrainEncoder.__module__ = "db_shared_win_direct_worker"
    main()
