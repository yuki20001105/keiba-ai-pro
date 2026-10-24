"""Fixed-score categorical win-probability calibration; never optimizes ROI.

Four predeclared families, fitted on 2021 and selected on 2022 NLL then
multiclass Brier. Refit 2021-22 is frozen before later known-reused diagnostics.
No application imports, model training, promotion, purchases, or network I/O.
JSON is the portable calibrator artifact; no executable pickle is necessary.
"""
from __future__ import annotations

import hashlib
import json
import math
import os
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd


SCHEMA = "fixed-score-race-probability-calibration-v1"
FAMILIES = ("uniform", "softmax_t1", "global_temperature", "surface_temperature")
TEMPERATURE_BOUNDS = (0.01, 100.0)
NUMERICAL_FLOOR = np.finfo(np.float64).tiny
_REQUIRED = ("race_id", "race_date", "horse_number", "surface", "expected_runner_count", "prediction")
_FIT_COLUMNS = _REQUIRED + ("finish", "fold_train_date_to")
_REPO_ROOT = Path(__file__).resolve().parents[2]


def _json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False)


def _sha(value: Any) -> str:
    return hashlib.sha256(_json(value).encode("utf-8")).hexdigest()


def _write(path: Path, value: str) -> None:
    with path.open("x", encoding="utf-8", newline="\n") as stream:
        stream.write(value)
        stream.flush()
        os.fsync(stream.fileno())


def _dates(values: pd.Series) -> pd.Series:
    if pd.api.types.is_datetime64_any_dtype(values.dtype):
        result = values.dt.strftime("%Y%m%d")
    else:
        result = values.astype("string").str.strip().str.replace("-", "", regex=False).str.replace("/", "", regex=False)
    if result.isna().any() or not result.str.fullmatch(r"\d{8}").all():
        raise ValueError("Complete canonical calendar dates are required")
    pd.to_datetime(result, format="%Y%m%d", errors="raise")
    return result.astype(str)


def _normalize(frame: pd.DataFrame, *, require_oof: bool = False) -> pd.DataFrame:
    if not isinstance(frame, pd.DataFrame) or frame.empty or frame.columns.duplicated().any():
        raise ValueError("Nonempty frame with unique columns is required")
    if not set(_REQUIRED).issubset(frame):
        raise ValueError(f"Missing race-complete score columns: {sorted(set(_REQUIRED) - set(frame))}")
    value = frame.copy().reset_index(drop=True)
    value["race_date"] = _dates(value["race_date"])
    for column in ("race_id", "surface"):
        if value[column].isna().any() or value[column].astype(str).str.strip().eq("").any():
            raise ValueError(f"Missing {column}")
        value[column] = value[column].astype(str).str.strip()
    for column in ("horse_number", "expected_runner_count", "prediction"):
        value[column] = pd.to_numeric(value[column], errors="raise")
        if not np.isfinite(value[column]).all():
            raise ValueError(f"Nonfinite {column}")
    number = value["horse_number"]
    if not (number.between(1, 18).all() and number.eq(np.floor(number)).all()):
        raise ValueError("Invalid runner number")
    if value.duplicated(["race_id", "horse_number"]).any():
        raise ValueError("Duplicate runner")
    if "horse_id" in value:
        identity = value["horse_id"].astype("string").fillna("").str.strip()
        if value.loc[identity.ne(""), ["race_id"]].assign(horse_id=identity[identity.ne("")]).duplicated().any():
            raise ValueError("Duplicate horse identity in race")
    grouped = value.groupby("race_id", sort=False)
    counts = grouped.horse_number.transform("size")
    if not counts.between(2, 18).all() or not value.expected_runner_count.eq(counts).all():
        raise ValueError("Declared complete runner count mismatch")
    if grouped.race_date.nunique().ne(1).any() or grouped.surface.nunique().ne(1).any():
        raise ValueError("Conflicting race date or surface")
    if require_oof:
        if "fold_train_date_to" not in value:
            raise ValueError("OOF fold_train_date_to proof is required")
        value["fold_train_date_to"] = _dates(value["fold_train_date_to"])
        if not value.fold_train_date_to.lt(value.race_date).all():
            raise ValueError("OOF training must precede the prediction date")
        if value.groupby("race_id").fold_train_date_to.nunique().ne(1).any():
            raise ValueError("Mixed OOF fold provenance within race")
    if "safe_prior_starts" in value:
        starts = pd.to_numeric(value["safe_prior_starts"], errors="coerce")
        known = starts.notna()
        if not (np.isfinite(starts[known]).all() and starts[known].ge(0).all()
                and starts[known].eq(np.floor(starts[known])).all()):
            raise ValueError("Invalid strictly-prior starts count")
        value["safe_prior_starts"] = starts
    return value.sort_values(["race_date", "race_id", "horse_number"], kind="stable").reset_index(drop=True)


def _outcomes(frame: pd.DataFrame) -> tuple[pd.DataFrame, dict]:
    if "finish" not in frame:
        raise ValueError("Confirmed finish column is required for fitting or evaluation")
    value = frame.copy()
    finish = pd.to_numeric(value["finish"], errors="coerce")
    valid = np.isfinite(finish) & finish.gt(0) & finish.le(value.expected_runner_count) & finish.eq(np.floor(finish))
    complete = valid.groupby(value.race_id).all()
    winners = finish.eq(1).groupby(value.race_id).sum()
    incomplete = ~complete | winners.eq(0)
    dead_heat = complete & winners.gt(1)
    eligible = complete & winners.eq(1)
    value["finish"] = finish
    kept = value.loc[value.race_id.isin(eligible.index[eligible])].copy()
    audit = {"input_races": len(complete), "input_runners": len(value),
             "included_races": int(eligible.sum()), "included_runners": len(kept),
             "excluded_outcome_incomplete_races": int(incomplete.sum()),
             "excluded_dead_heat_races": int(dead_heat.sum()),
             "excluded_runners": len(value) - len(kept),
             "dead_heat_policy": "exclude whole race; probabilities describe exactly one winner"}
    return kept.reset_index(drop=True), audit


def _hash_frame(frame: pd.DataFrame, columns: tuple | list) -> str:
    columns = [column for column in columns if column in frame]
    digest = hashlib.sha256(_json(columns).encode())
    for start in range(0, len(frame), 4096):
        digest.update(frame.iloc[start:start + 4096][columns].to_csv(
            index=False, header=False, na_rep="<missing>", float_format="%.17g", lineterminator="\n").encode())
    return digest.hexdigest()


def _arrays(frame: pd.DataFrame) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    codes, identifiers = pd.factorize(frame.race_id, sort=False)
    scores = frame.prediction.to_numpy(dtype=float)
    maxima = np.full(len(identifiers), -np.inf)
    np.maximum.at(maxima, codes, scores)
    with np.errstate(over="ignore", invalid="ignore"):
        centered = scores - maxima[codes]
    if not np.isfinite(centered).all():
        raise ValueError("Score range exceeds finite arithmetic")
    counts = np.bincount(codes, minlength=len(identifiers))
    return codes, centered, counts


def _probabilities(codes: np.ndarray, centered: np.ndarray, beta: float | np.ndarray) -> np.ndarray:
    with np.errstate(over="ignore", under="ignore"):
        weights = np.exp(centered * beta)
    sums = np.bincount(codes, weights=weights)
    probability = np.maximum(weights / sums[codes], NUMERICAL_FLOOR)
    # Explicit numerical-floor normalization; no statistical odds adjustment.
    return probability / np.bincount(codes, weights=probability)[codes]


def _fit_temperature(frame: pd.DataFrame) -> dict:
    codes, centered, counts = _arrays(frame)
    winners = frame.finish.eq(1).to_numpy()
    winning_score = float(centered[winners].sum())
    if not math.isfinite(winning_score):
        raise ValueError("Score range exceeds finite likelihood arithmetic")
    low, high = 1 / TEMPERATURE_BOUNDS[1], 1 / TEMPERATURE_BOUNDS[0]

    def derivative(beta):
        probability = _probabilities(codes, centered, beta)
        value = (float(np.dot(probability, centered)) - winning_score) / len(counts)
        if not math.isfinite(value):
            raise ValueError("Nonfinite temperature likelihood derivative")
        return value

    if np.all(centered == 0):
        beta, reason = 1.0, "constant_scores_no_identifiable_temperature"
    elif derivative(low) >= 0:
        beta, reason = low, "upper_temperature_boundary"
    elif derivative(high) <= 0:
        beta, reason = high, "lower_temperature_boundary"
    else:
        for _ in range(64):
            middle = (low + high) / 2
            if derivative(middle) < 0:
                low = middle
            else:
                high = middle
        beta, reason = (low + high) / 2, "bounded_convex_inverse_temperature_optimum"
    return {"temperature": float(1 / beta), "inverse_temperature": float(beta),
            "fit_races": len(counts), "fit_runners": len(frame), "fit_status": reason}


def fit_calibrator(frame: pd.DataFrame, family: str = "global_temperature", *, min_surface_races: int = 100) -> dict:
    """Fit only supplied OOF scores/finishes. Odds/popularity/payout are ignored.

    Likelihood is one categorical event per complete, single-winner race.
    Temperature is bounded [0.01,100], with fixed 64-step derivative bisection.
    """
    if family not in FAMILIES:
        raise ValueError("Unknown predeclared calibration family")
    if type(min_surface_races) is not int or min_surface_races < 1:
        raise ValueError("min_surface_races must be positive integer")
    normalized = _normalize(frame, require_oof=True)
    eligible, audit = _outcomes(normalized)
    if eligible.empty:
        raise ValueError("No complete single-winner races remain for calibration")
    global_fit = _fit_temperature(eligible) if family in FAMILIES[2:] else None
    by_surface = {}
    if family == "surface_temperature":
        for surface in ("turf", "dirt"):
            subset = eligible.loc[eligible.surface.eq(surface)]
            count = subset.race_id.nunique()
            by_surface[surface] = (_fit_temperature(subset) if count >= min_surface_races else
                                   {"fallback": "global", "fit_races": int(count), "reason": "insufficient_surface_races"})
    return {"schema": SCHEMA, "family": family, "temperature_bounds": list(TEMPERATURE_BOUNDS),
            "global": global_fit, "by_surface": by_surface, "unknown_surface_fallback": "global",
            "min_surface_races": min_surface_races, "score_column": "prediction",
            "fit_years": sorted(eligible.race_date.str[:4].astype(int).unique().tolist()),
            "fit_date_from": eligible.race_date.min(), "fit_date_to": eligible.race_date.max(),
            "input_sha256": _hash_frame(normalized, _FIT_COLUMNS), "cohort_audit": audit,
            "fit_objective": "mean categorical race NLL", "market_data_used": False,
            "numerical_floor": float(NUMERICAL_FLOOR), "renormalized_within_race": True}


def _check_calibrator(calibrator: dict) -> None:
    if calibrator.get("schema") != SCHEMA or calibrator.get("family") not in FAMILIES:
        raise ValueError("Invalid calibrator schema/family")
    if calibrator.get("family") in FAMILIES[2:]:
        parameters = [calibrator.get("global", {})] + [value for value in calibrator.get("by_surface", {}).values() if "temperature" in value]
        for parameter in parameters:
            temperature = parameter.get("temperature")
            if (not isinstance(temperature, (int, float)) or not math.isfinite(temperature)
                    or not TEMPERATURE_BOUNDS[0] <= temperature <= TEMPERATURE_BOUNDS[1]):
                raise ValueError("Invalid bounded temperature")


def transform_probabilities(frame: pd.DataFrame, calibrator: dict) -> pd.DataFrame:
    """Apply frozen parameters without labels or market data; preserve input columns.

    The returned frame is sorted by date/race/horse number (not caller index).
    JSON may be a calibrator directly or the persisted frozen artifact envelope.
    """
    if "calibrator" in calibrator:
        if calibrator.get("calibrator_sha256") != _sha(calibrator["calibrator"]):
            raise ValueError("Frozen calibrator hash mismatch")
        calibrator = calibrator["calibrator"]
    _check_calibrator(calibrator)
    value = _normalize(frame)
    codes, centered, counts = _arrays(value)
    family = calibrator["family"]
    if family == "uniform":
        probability = 1 / counts[codes]
    else:
        beta = 1.0 if family == "softmax_t1" else 1 / calibrator["global"]["temperature"]
        if family == "surface_temperature":
            temperatures = {name: parameter["temperature"] for name, parameter in calibrator["by_surface"].items() if "temperature" in parameter}
            beta = 1 / value.surface.map(temperatures).fillna(calibrator["global"]["temperature"]).to_numpy()
        probability = _probabilities(codes, centered, beta)
    value["win_probability"] = probability
    value["calibration_family"] = family
    value["calibrator_sha256"] = _sha(calibrator)
    return value


def _reliability(probability: np.ndarray, label: np.ndarray, n_bins: int) -> dict:
    count = len(probability)
    index = np.minimum((probability * n_bins).astype(int), n_bins - 1)
    rows, total_error = [], 0.0
    for bin_id in range(n_bins):
        selected = index == bin_id
        size = int(selected.sum())
        predicted = float(probability[selected].mean()) if size else None
        observed = float(label[selected].mean()) if size else None
        if size:
            total_error += size * abs(predicted - observed)
        rows.append({"lower": bin_id / n_bins, "upper": (bin_id + 1) / n_bins,
                     "upper_inclusive": bin_id == n_bins - 1, "runner_count": size,
                     "mean_probability": predicted, "observed_win_rate": observed})
    return {"equal_width_bins": n_bins, "weighting": "each runner equally weighted",
            "ece": total_error / count if count else None, "bins": rows}


def _metrics(frame: pd.DataFrame, probability_column: str, n_bins: int, *, complete_races: bool = True) -> dict:
    probability = frame[probability_column].to_numpy(dtype=float)
    label = frame.finish.eq(1).to_numpy(dtype=float)
    squared = (probability - label) ** 2
    result = {"runner_count": len(frame), "race_count": int(frame.race_id.nunique()),
              "runner_brier": float(squared.mean()) if len(frame) else None,
              "race_nll": None, "race_multiclass_brier": None, "race_normalized_brier": None,
              "reliability": _reliability(probability, label, n_bins), "complete_races": complete_races}
    if len(frame) and complete_races:
        winners = label == 1
        daily = pd.DataFrame({"race_id": frame.race_id.to_numpy(), "squared": squared}).groupby("race_id").squared.agg(["sum", "size"])
        result.update(race_nll=float(-np.log(np.maximum(probability[winners], NUMERICAL_FLOOR)).mean()),
                      race_multiclass_brier=float(daily["sum"].mean()),
                      race_normalized_brier=float((daily["sum"] / daily["size"]).mean()))
    return result


def evaluate_calibration(frame: pd.DataFrame, *, probability_column: str = "win_probability", n_bins: int = 10) -> dict:
    """Single-winner NLL, Brier and equal-width reliability; never ROI.

    race_multiclass_brier = mean_race(sum_runner((p-y)^2)); runner_brier
    weights each runner equally. Prior-start strata are runner subsets: their
    race NLL/multiclass Brier are deliberately null, with no renormalization.
    """
    if type(n_bins) is not int or not 2 <= n_bins <= 50:
        raise ValueError("n_bins must be an integer from 2 to 50")
    value = _normalize(frame)
    if probability_column not in value:
        raise ValueError("Probability column missing")
    probability = pd.to_numeric(value[probability_column], errors="raise")
    if not np.isfinite(probability).all() or not probability.between(0, 1).all():
        raise ValueError("Invalid win probability")
    value[probability_column] = probability
    sums = value.groupby("race_id")[probability_column].sum()
    if not np.allclose(sums.to_numpy(), 1, rtol=0, atol=1e-12):
        raise ValueError("Race probabilities must sum to one")
    eligible, audit = _outcomes(value)
    prior = eligible["safe_prior_starts"] if "safe_prior_starts" in eligible else pd.Series(np.nan, index=eligible.index)
    strata = {"0-2": prior.between(0, 2), "3+": prior.ge(3), "unknown": prior.isna()}
    return {"cohort_audit": audit, "overall": _metrics(eligible, probability_column, n_bins),
            "by_surface": {surface: _metrics(group, probability_column, n_bins) for surface, group in eligible.groupby("surface", sort=True)},
            "by_year": {year: _metrics(group, probability_column, n_bins) for year, group in eligible.groupby(eligible.race_date.str[:4], sort=True)},
            "by_prior_starts": {name: _metrics(eligible.loc[mask], probability_column, n_bins, complete_races=False) for name, mask in strata.items()},
            "definitions": {"race_nll": "mean_race(-log(p of sole winner)); nats",
                            "race_multiclass_brier": "mean_race(sum_runner((p-y)^2))",
                            "runner_brier": "mean_runner((p-y)^2)",
                            "race_normalized_brier": "mean_race(mean_runner_in_race((p-y)^2))",
                            "prior_start_strata": "runner subsets only; no renormalization or subset race NLL",
                            "ece_warning": "runner-weighted, bins and class imbalance affect interpretation"}}


def _provenance(value: dict) -> dict:
    if not isinstance(value, dict) or not isinstance(value.get("model_id"), str) or not value["model_id"].strip():
        raise ValueError("Fixed model identity is required")
    def digest(value):
        return isinstance(value, str) and len(value) == 64 and all(character in "0123456789abcdef" for character in value)
    models = value.get("model_artifact_sha256_by_year")
    if not digest(value.get("model_artifact_sha256")) and not (isinstance(models, dict) and models and all(digest(item) for item in models.values())):
        raise ValueError("Source model artifact SHA-256 provenance is required")
    predictions = value.get("prediction_artifacts")
    if not digest(value.get("prediction_sha256")) and not (isinstance(predictions, list) and predictions and all(isinstance(item, dict) and digest(item.get("sha256")) for item in predictions)):
        raise ValueError("Source prediction artifact SHA-256 provenance is required")
    _json(value)
    return value


def _years(values, name):
    result = tuple(values)
    if not result or any(type(year) is not int for year in result) or list(result) != sorted(set(result)):
        raise ValueError(f"{name} must be ascending unique integer years")
    return result


def run_calibration_experiment(predictions: pd.DataFrame, output_dir: str | Path, *, model_provenance: dict,
                               fit_years=(2021,), selection_years=(2022,), refit_years=(2021, 2022),
                               confirmation_years=(2023, 2024, 2025), retrospective_years=(2026,),
                               min_surface_races: int = 100, n_bins: int = 10) -> dict:
    """Run the predeclared fixed-model calibration protocol on supplied frames.

    Only date metadata of later frames is examined before the calibrator freeze.
    Inputs must have fold_train_date_to and expected_runner_count, added from
    verified model/dataset contracts by the caller. Source hashes are supplied
    caller provenance; normalized consumed-frame hashes are computed here.
    Existing output directories are never reused or overwritten.
    """
    provenance = _provenance(model_provenance)
    fit_years, selection_years = _years(fit_years, "fit_years"), _years(selection_years, "selection_years")
    refit_years = _years(refit_years, "refit_years")
    confirmation_years, retrospective_years = _years(confirmation_years, "confirmation_years"), _years(retrospective_years, "retrospective_years")
    if not (max(fit_years) < min(selection_years) < min(confirmation_years)
            and max(selection_years) < min(confirmation_years) and max(confirmation_years) < min(retrospective_years)
            and refit_years == tuple(sorted(set(fit_years + selection_years)))):
        raise ValueError("Fit/selection/refit/confirmation/retrospective chronology is invalid")
    if not isinstance(predictions, pd.DataFrame) or "race_date" not in predictions:
        raise ValueError("Supplied predictions need race_date")
    dates = _dates(predictions.race_date)
    years = dates.str[:4].astype(int)
    expected_years = set(refit_years + confirmation_years + retrospective_years)
    if set(years.unique()) != expected_years:
        raise ValueError("Supplied years differ from the predeclared complete protocol")
    year_hashes = provenance.get("model_artifact_sha256_by_year")
    if year_hashes is not None and not set(map(str, expected_years)).issubset(year_hashes):
        raise ValueError("Source model artifact SHA-256 is missing a declared year")
    if "model_id" in predictions and not predictions.model_id.eq(provenance["model_id"]).all():
        raise ValueError("Mixed or unexpected base model identity")
    output = Path(output_dir).resolve()
    for protected in ("data", "models", "keiba", "python-api", "src"):
        if output == _REPO_ROOT / protected or output.is_relative_to(_REPO_ROOT / protected):
            raise ValueError("Calibration artifacts must not modify application data/source/model directories")
    if output == _REPO_ROOT:
        raise ValueError("Use a new isolated report directory")
    output.mkdir(parents=True, exist_ok=False)
    protocol = {"schema": SCHEMA, "families": list(FAMILIES), "fit_years": list(fit_years),
                "selection_years": list(selection_years), "refit_years": list(refit_years),
                "confirmation_years": list(confirmation_years), "retrospective_years": list(retrospective_years),
                "selection_order": ["lowest race NLL", "lowest race multiclass Brier", "predeclared family order"],
                "temperature_bounds": list(TEMPERATURE_BOUNDS), "min_surface_races": min_surface_races,
                "reliability_bins": n_bins, "roi_used_for_selection": False, "base_model_provenance": provenance,
                "module_sha256": hashlib.sha256(Path(__file__).read_bytes()).hexdigest()}
    _write(output / "protocol.json", _json(protocol) + "\n")
    fit = predictions.loc[years.isin(fit_years)].copy()
    selection = _normalize(predictions.loc[years.isin(selection_years)].copy(), require_oof=True)
    fit_models, evaluated = {}, {}
    for family in FAMILIES:
        fit_models[family] = fit_calibrator(fit, family, min_surface_races=min_surface_races)
        evaluated[family] = evaluate_calibration(transform_probabilities(selection, fit_models[family]), n_bins=n_bins)
    if any(evaluation["overall"]["race_nll"] is None for evaluation in evaluated.values()):
        raise ValueError("No eligible selection races")
    selected = min(FAMILIES, key=lambda family: (evaluated[family]["overall"]["race_nll"],
                   evaluated[family]["overall"]["race_multiclass_brier"], FAMILIES.index(family)))
    decision = {"selected_family": selected, "selection_years": list(selection_years),
                "selection_metrics": {family: evaluated[family]["overall"] for family in FAMILIES},
                "fitted_candidates": fit_models, "selection_input_sha256": _hash_frame(selection, _FIT_COLUMNS),
                "confirmation_used_for_selection": False, "protocol_sha256": _sha(protocol)}
    _write(output / "selection.json", _json(decision) + "\n")
    refit = fit_calibrator(predictions.loc[years.isin(refit_years)].copy(), selected, min_surface_races=min_surface_races)
    frozen = {"schema": SCHEMA, "calibrator": refit, "calibrator_sha256": _sha(refit),
              "selection_sha256": _sha(decision), "protocol_sha256": _sha(protocol),
              "model_provenance": provenance, "frozen_before_later_diagnostics": True,
              "probabilities_sum_to_one_per_race": True, "market_data_used": False,
              "selection_reused_known_period": True, "automatic_model_promotion": False}
    frozen_text = _json(frozen) + "\n"
    _write(output / "frozen-calibrator.json", frozen_text)
    frozen_hash = hashlib.sha256(frozen_text.encode()).hexdigest()
    _write(output / "frozen-calibrator.sha256", frozen_hash + "\n")
    # No later score/finish validation, transformation, or evaluation above here.
    diagnostics, diagnostic_parts = {}, []
    for band, band_years in (("confirmation", confirmation_years), ("retrospective", retrospective_years)):
        later = _normalize(predictions.loc[years.isin(band_years)].copy(), require_oof=True)
        transformed = transform_probabilities(later, frozen)
        transformed["calibration_diagnostic_period"] = band
        diagnostics[band] = evaluate_calibration(transformed, n_bins=n_bins)
        diagnostics[band]["input_sha256"] = _hash_frame(later, _FIT_COLUMNS + ("safe_prior_starts",))
        diagnostics[band]["references"] = {
            family: evaluate_calibration(transform_probabilities(later, fit_models[family]), n_bins=n_bins)
            for family in ("uniform", "softmax_t1")
        }
        diagnostic_parts.append(transformed)
    probability_path = output / "diagnostic-probabilities.parquet"
    pd.concat(diagnostic_parts, ignore_index=True).to_parquet(probability_path, index=False)
    report = {"schema": SCHEMA, "selected_family": selected, "protocol": protocol,
              "selection_2022": evaluated, "selection_period_label": "known reused selection, not unbiased holdout",
              "frozen_calibrator_sha256": frozen_hash, **diagnostics,
              "artifacts": {"frozen_calibrator": str(output / "frozen-calibrator.json"),
                            "probabilities": {"path": str(probability_path), "sha256": hashlib.sha256(probability_path.read_bytes()).hexdigest()}},
              "limitations": ["The fixed base model was already selected using 2021-22; calibration separation does not erase that selection.",
                              "2023-25 and 2026 are known reused diagnostics, not unseen validation. No later fitting or selection.",
                              "A fitted transformation produces probability estimates, not guaranteed calibrated true probabilities.",
                              "Dead heats and incomplete outcomes are excluded from likelihood evaluation with explicit counts.",
                              "Only complete declared runner cohorts are evaluated; not all real-world races.",
                              "Odds, popularity, payout, expected value and ROI are never calibration inputs or selection criteria.",
                              "Source artifact hashes are caller-attested; this module hashes the consumed normalized frames.",
                              "ECE is runner-weighted with predeclared equal-width bins; class imbalance may make it appear small."],
              "reselected_after_freeze": False, "model_training_performed": False, "automatic_purchase": False}
    _write(output / "calibration-report.json", _json(report) + "\n")
    return report
