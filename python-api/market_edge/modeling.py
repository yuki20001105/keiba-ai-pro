"""Small predeclared residual models, isolated from application model stores."""
from __future__ import annotations

import json
from pathlib import Path
import time

import numpy as np
import pandas as pd
from scipy.optimize import minimize
from scipy.special import expit, logit

FEATURES = ["normalized_market_probability", "log_odds", "market_rank", "h04_score",
            "h04_rank", "h04_probability", "rank_difference", "probability_difference", "field_size"]
MARKET_FEATURES = ["normalized_market_probability", "log_odds", "market_rank", "field_size"]
PARAMS = {"objective": "binary", "learning_rate": .03, "num_leaves": 7,
          "min_data_in_leaf": 200, "lambda_l2": 10, "max_bin": 63,
          "seed": 42, "num_threads": 2, "verbosity": -1, "boost_from_average": False}


def matrix(frame, features=FEATURES):
    values = frame[features].to_numpy(dtype=float)
    if not np.isfinite(values).all():
        raise ValueError("Nonfinite research features; missing values are not silently filled")
    return values


def market_offset(frame):
    p = frame.normalized_market_probability.to_numpy(float)
    if not (np.isfinite(p).all() and ((p > 0) & (p < 1)).all()):
        raise ValueError("Invalid market probability")
    return logit(p)


def normalize(frame, values):
    values = np.asarray(values, dtype=float)
    if len(values) != len(frame) or not np.isfinite(values).all() or (values <= 0).any():
        raise ValueError("Invalid positive race probabilities")
    totals = pd.Series(values).groupby(frame.race_id.reset_index(drop=True), sort=False).transform("sum").to_numpy()
    return values / totals


def logistic_loss(beta, design, offset, labels, weights, l2=.01):
    score = offset + design @ beta
    loss = np.dot(weights, np.logaddexp(0, score) - labels * score) + .5 * l2 * np.dot(beta[1:], beta[1:])
    gradient = design.T @ (weights * (expit(score) - labels))
    gradient[1:] += l2 * beta[1:]
    return float(loss), gradient


def fit_model(train, model, device, output, *, rounds=150):
    """No validation labels, early stopping, ROI tuning or model promotion."""
    if model not in {"logistic", "market_logistic", "lightgbm"} or device not in {"cpu", "gpu"}:
        raise ValueError("Unsupported predeclared model/device")
    if train.empty or train.race_date.astype(str).max() > "20211231":
        raise ValueError("Research meta fit restricted to 2021")
    output = Path(output)
    output.mkdir(parents=True, exist_ok=True)
    if (output / "model.json").exists():
        raise FileExistsError("Research model artifact already exists")
    features = MARKET_FEATURES if model == "market_logistic" else FEATURES
    x, offset = matrix(train, features), market_offset(train)
    y = train.finish.eq(1).to_numpy(float)
    weights = 1 / train.groupby("race_id").race_id.transform("size").to_numpy()
    started = time.perf_counter()
    artifact = {"model": model, "device": device, "features": features,
                "train_date_from": str(train.race_date.min()), "train_date_to": str(train.race_date.max()),
                "train_rows": len(train), "train_races": int(train.race_id.nunique()),
                "offset": "logit(normalized reciprocal odds); add explicitly at inference",
                "postprocess": "sigmoid then race normalization", "rounds": rounds if model == "lightgbm" else None}
    if model in {"logistic", "market_logistic"}:
        if device != "cpu":
            raise ValueError("Logistic uses CPU only")
        mean, scale = x.mean(axis=0), x.std(axis=0)
        scale[scale < 1e-12] = 1
        design = np.column_stack([np.ones(len(x)), (x - mean) / scale])
        solution = minimize(logistic_loss, np.zeros(design.shape[1]),
                            args=(design, offset, y, weights / weights.sum()),
                            jac=True, method="L-BFGS-B", options={"maxiter": 500, "ftol": 1e-12, "gtol": 1e-8})
        if not solution.success or not np.isfinite(solution.x).all():
            raise ValueError(f"Logistic fit failed: {solution.message}")
        artifact.update(mean=mean.tolist(), scale=scale.tolist(), beta=solution.x.tolist(),
                        iterations=int(solution.nit), objective=float(solution.fun), l2=.01)
    else:
        import lightgbm as lgb
        params = {**PARAMS, "device_type": device}
        if device == "cpu":
            params.update(deterministic=True, force_col_wise=True)
        dataset = lgb.Dataset(x, label=y, init_score=offset, weight=weights, feature_name=FEATURES)
        booster = lgb.train(params, dataset, num_boost_round=rounds)
        booster.save_model(str(output / "residual.txt"))
        artifact["params"] = params
    artifact["train_seconds"] = time.perf_counter() - started
    (output / "model.json").write_text(json.dumps(artifact, indent=2), encoding="utf-8")
    return artifact


def predict_model(frame, output):
    output = Path(output)
    artifact = json.loads((output / "model.json").read_text(encoding="utf-8"))
    features = MARKET_FEATURES if artifact["model"] == "market_logistic" else FEATURES
    if artifact["features"] != features:
        raise ValueError("Feature spec mismatch")
    x, offset = matrix(frame, features), market_offset(frame)
    if artifact["model"] in {"logistic", "market_logistic"}:
        scaled = (x - np.array(artifact["mean"])) / np.array(artifact["scale"])
        residual = np.column_stack([np.ones(len(x)), scaled]) @ np.array(artifact["beta"])
    else:
        import lightgbm as lgb
        booster = lgb.Booster(model_file=str(output / "residual.txt"))
        # LightGBM does not store row-specific init_score in the model.
        residual = booster.predict(x, raw_score=True, num_threads=2)
    return normalize(frame, np.maximum(expit(offset + residual), np.finfo(float).tiny))


def run_worker(data, output, *, model, device="cpu", fold="development", rounds=150):
    if fold not in {"development", "validation"}:
        raise ValueError("Unknown predeclared chronological fold")
    data = pd.read_parquet(data)
    train_phases = ["meta_fit"] if fold == "development" else ["meta_fit", "development"]
    test_phase = "development" if fold == "development" else "validation"
    train = data.loc[data.phase.isin(train_phases)].reset_index(drop=True)
    test = data.loc[data.phase.eq(test_phase)].reset_index(drop=True)
    if train.empty or test.empty or str(train.race_date.max()) >= str(test.race_date.min()):
        raise ValueError("Chronological training/evaluation overlap")
    artifact = fit_model(train, model, device, output, rounds=rounds)
    start = time.perf_counter()
    probabilities = predict_model(test.drop(columns=[c for c in ["finish", "is_win", "tansho_payout"] if c in test]), output)
    duration = time.perf_counter() - start
    pd.DataFrame({"race_id": test.race_id, "horse_number": test.horse_number,
                  "probability": probabilities}).to_parquet(Path(output) / "predictions.parquet", index=False)
    result = {"model": model, "device": device, "fold": fold, "train_rows": len(train),
              "evaluation_rows": len(test), "feature_count": len(artifact["features"]), "rounds": rounds,
              "train_seconds": artifact["train_seconds"], "inference_seconds": duration,
              "max_probability_sum_error": float(abs(pd.Series(probabilities).groupby(test.race_id).sum() - 1).max())}
    (Path(output) / "result.json").write_text(json.dumps(result, indent=2), encoding="utf-8")
    return result
