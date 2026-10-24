"""Track B development-only inputs from verified historical H04 OOT scores.

Never opens post-2022 outcomes, loads a serving model, retrains H04, or modifies
Track A. A new H1-2021-only temperature is distinct from the serving calibrator.
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path

import numpy as np
import pandas as pd
from threadpoolctl import threadpool_limits

from prospective.calibration import (_normalize, _outcomes, fit_calibrator,
                                     transform_probabilities)


SCHEMA = "market-edge-development-v1"
PHASES = ("calibration_seed", "meta_fit", "development", "validation")
REQUIRED = ["race_id", "race_date", "horse_id", "horse_number", "surface", "finish",
            "popularity", "odds", "tansho_payout", "expected_runner_count", "prediction", "fold_year"]
MARKET_FEATURES = ["odds", "implied_probability", "normalized_market_probability", "market_rank", "field_size"]
COMBINED_FEATURES = ["normalized_market_probability", "log_odds", "market_rank", "h04_score",
                     "h04_rank", "h04_probability", "rank_difference", "probability_difference", "field_size"]
OUTCOME_ONLY = ["finish", "is_win", "tansho_payout", "payout_confirmed_race"]


def _sha(path):
    digest = hashlib.sha256()
    with Path(path).open("rb") as stream:
        for part in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(part)
    return digest.hexdigest()


def _read(path):
    return json.loads(Path(path).read_text(encoding="utf-8"))


def _write(path, value):
    with Path(path).open("x", encoding="utf-8") as stream:
        json.dump(value, stream, ensure_ascii=False, sort_keys=True, indent=2, allow_nan=False)


def _bound(root, path, digest):
    candidate = Path(path).resolve()
    if not candidate.is_relative_to(root.resolve()) or candidate.is_symlink() or not candidate.is_file():
        raise ValueError("source artifact is outside the declared immutable experiment")
    if _sha(candidate) != digest:
        raise ValueError("source artifact SHA256 mismatch")
    return candidate


def _source(search_root):
    root = Path(search_root).resolve()
    result_path = root / "trials/winner-search/result.json"
    artifact_manifest = _read(result_path.parent / "artifact_manifest.json")
    _bound(root, result_path, artifact_manifest["result.json"])
    result = _read(result_path)
    if (result.get("status") != "completed" or result.get("validation_years") != [2021, 2022]
            or result.get("target") != "rank_regression" or result.get("loss") != "huber"
            or result.get("task") != "ranking"):
        raise ValueError("completed development-only H04 rank-regression source required")
    features = result.get("feature_columns", [])
    if len(features) != 99 or len(set(features)) != 99 or not all(x.startswith("safe_") for x in features):
        raise ValueError("frozen H04 safe 99-feature contract required")
    source = result["source_contract"]
    if source.get("source_contract", {}).get("history_boundary") != "source_race_date < target_race_date":
        raise ValueError("strict past-only historical feature proof required")
    info = result["artifacts"]["predictions"]
    predictions_path = _bound(root, info["path"], info["sha256"])
    folds = []
    for fold in result["folds"]:
        year, contract = fold["year"], fold["contract"]
        if year not in (2021, 2022):
            raise ValueError("post-development model not allowed")
        expected = ["20160101", f"{year-1}1231"]
        if (contract.get("train_period") != expected or contract.get("evaluation_year") != year
                or contract.get("validation_used_for_fit_or_early_stopping") is not False
                or contract.get("market_features") is not False
                or contract.get("feature_columns") != features
                or contract.get("dataset_sha256") != result["dataset_sha256"]):
            raise ValueError("fold chronology/features/source contract mismatch")
        path = _bound(root, fold["artifact"], fold["sha256"])
        folds.append({"year": year, "train_period": expected, "model_path": str(path),
                      "model_sha256": fold["sha256"], "validation_rows": fold["validation_rows"],
                      "validation_races": fold["validation_races"],
                      "training_end_is_contract_upper_bound": True})
    if sorted(f["year"] for f in folds) != [2021, 2022]:
        raise ValueError("exactly one OOT model for each development year required")
    return result, predictions_path, {"result_path": str(result_path), "result_sha256": _sha(result_path),
        "artifact_manifest_sha256": _sha(result_path.parent / "artifact_manifest.json"),
        "predictions_path": str(predictions_path), "predictions_sha256": info["sha256"], "folds": folds,
        "dataset_sha256": result["dataset_sha256"], "manifest_sha256": result["manifest_sha256"],
        "source_contract": source, "feature_columns": features}


def development_frame(frame, folds, *, reference_calibrator=None):
    """Pure preparation; rejects later years, incomplete races and model leakage.

    Structural corruption aborts. Missing outcomes/dead heats/missing market
    odds exclude whole races with receipts. Missing payouts do NOT become zero.
    """
    if not set(REQUIRED) <= set(frame) or frame.empty or frame.columns.duplicated().any():
        raise ValueError("required development columns missing or duplicate")
    value = frame.loc[:, REQUIRED].copy()
    dates = value.race_date.astype("string")
    if not dates.str.fullmatch(r"\d{8}").fillna(False).all() or not dates.str[:4].isin(["2021", "2022"]).all():
        raise ValueError("only canonical 2021 and 2022 dates may be inspected")
    pd.to_datetime(dates, format="%Y%m%d", errors="raise")
    if not value.fold_year.eq(dates.str[:4].astype(int)).all():
        raise ValueError("prediction fold must match target year")
    bounds = {f["year"]: f["train_period"][1] for f in folds}
    if bounds != {2021: "20201231", 2022: "20211231"}:
        raise ValueError("only prior-year-trained H04 models may supply scores")
    value["fold_train_date_to"] = value.fold_year.map(bounds)
    value = _normalize(value, require_oof=True)
    if (not value.race_id.str.fullmatch(r"\d{4}(?:0[1-9]|10)\d{6}").all()
            or not value.race_id.str[:4].eq(value.race_date.str[:4]).all()):
        raise ValueError("dated JRA race identifiers required")
    if not value.surface.isin(["turf", "dirt"]).all():
        raise ValueError("JRA flat surface contract required")
    for fold in folds:
        subset = value.loc[value.fold_year.eq(fold["year"])]
        if len(subset) != fold["validation_rows"] or subset.race_id.nunique() != fold["validation_races"]:
            raise ValueError("OOF prediction cohort does not match fold artifact")
    before_outcomes = value
    value, outcome_audit = _outcomes(value)
    outcome_excluded = sorted(set(before_outcomes.race_id) - set(value.race_id))
    odds = pd.to_numeric(value.odds, errors="coerce").astype(float)
    bad_market = ~np.isfinite(odds) | odds.lt(1)
    market_excluded = sorted(value.loc[bad_market, "race_id"].unique().tolist())
    value["odds"] = odds
    value = value.loc[~value.race_id.isin(market_excluded)].reset_index(drop=True)
    if value.empty:
        raise ValueError("no complete single-winner market races")
    value["phase"] = np.select([value.race_date.le("20210630"), value.race_date.le("20210930"),
                                value.race_date.le("20211231")], PHASES[:3], default="validation")
    if set(value.phase) != set(PHASES):
        raise ValueError("all predeclared chronological phases must be nonempty")
    seed = value.loc[value.phase.eq("calibration_seed")]
    with threadpool_limits(limits=2):
        calibrator = fit_calibrator(seed, "global_temperature")
        later = transform_probabilities(value.loc[~value.phase.eq("calibration_seed")], calibrator)
    if calibrator["fit_date_to"] > "20210630" or calibrator["fit_years"] != [2021]:
        raise ValueError("calibrator cannot consume meta-fit or validation outcomes")
    probabilities = later.set_index(["race_id", "horse_number"]).win_probability
    value["h04_probability"] = [probabilities.get((r, n), np.nan) for r, n in zip(value.race_id, value.horse_number)]
    value["h04_score"] = value.prediction
    value["h04_rank"] = value.groupby("race_id").h04_score.rank(ascending=False, method="average")
    value["field_size"] = value.expected_runner_count.astype(int)
    value["is_win"] = value.finish.eq(1).astype(int)
    value["implied_probability"] = 1 / value.odds
    value["normalized_market_probability"] = value.implied_probability / value.groupby("race_id").implied_probability.transform("sum")
    value["log_odds"] = np.log(value.odds)
    value["market_rank"] = value.groupby("race_id").odds.rank(method="average")
    value["rank_difference"] = value.h04_rank - value.market_rank
    value["probability_difference"] = value.h04_probability - value.normalized_market_probability
    payout = pd.to_numeric(value.tansho_payout, errors="coerce").astype(float)
    valid_payout = value.is_win.eq(1) & np.isfinite(payout) & payout.ge(100) & payout.mod(10).eq(0)
    value["tansho_payout"] = payout.where(valid_payout, np.nan)
    confirmed = value.loc[valid_payout, "race_id"]
    value["payout_confirmed_race"] = value.race_id.isin(confirmed)
    reference = None
    if reference_calibrator is not None:
        fitted = reference_calibrator.get("calibrator", reference_calibrator)
        if fitted.get("family") != "global_temperature" or fitted.get("fit_date_to", "") > "20221231":
            raise ValueError("fixed reference may not use post-2022 calibration outcomes")
        scored = transform_probabilities(value, reference_calibrator)
        if not scored[["race_id", "horse_number"]].equals(value[["race_id", "horse_number"]]):
            raise ValueError("fixed reference row order changed")
        value["h04_probability_fixed_reference"] = scored.win_probability.to_numpy()
        reference = {"role": "contaminated_calibration_reference_only_not_selection_or_features",
                     "fit_date_from": fitted["fit_date_from"], "fit_date_to": fitted["fit_date_to"],
                     "temperature": fitted["global"]["temperature"], "allowed_for_validation_gates": False}
    value = value.drop(columns="prediction")
    counts = {phase: {"rows": int(value.phase.eq(phase).sum()),
                      "races": int(value.loc[value.phase.eq(phase), "race_id"].nunique()),
                      "date_from": value.loc[value.phase.eq(phase), "race_date"].min(),
                      "date_to": value.loc[value.phase.eq(phase), "race_date"].max()}
              for phase in PHASES}
    return value, calibrator, {"phases": counts, "outcome_filter": outcome_audit,
        "outcome_excluded_race_ids": outcome_excluded, "incomplete_market_excluded_race_ids": market_excluded,
        "payout_confirmed_races": int(value.loc[value.payout_confirmed_race, "race_id"].nunique()),
        "payout_unconfirmed_races": int(value.loc[~value.payout_confirmed_race, "race_id"].nunique()),
        "fixed_calibration_reference": reference}


def prepare_development(search_root, output_dir, *, reference_calibrator_path=None):
    """Create exclusive development.parquet, seed-calibrator.json, provenance.json.

    Only a development-only source prediction file is opened; its metadata and
    model bytes are verified against existing hashes. Model pickles are not run.
    Caller performs collector/resource scheduling before invoking this function.
    """
    output, source_root = Path(output_dir).resolve(), Path(search_root).resolve()
    repo = Path(__file__).resolve().parents[2]
    protected = [repo / "python-api", repo / "keiba", source_root, repo / "reports/generated/forward-runtime"]
    if any(output == p or output.is_relative_to(p) for p in protected):
        raise ValueError("output must be isolated from original data, source, and Track A")
    names = ["development.parquet", "seed-calibrator.json", "provenance.json"]
    if any((output / name).exists() for name in names):
        raise FileExistsError("immutable development artifact already exists")
    result, prediction_path, source = _source(source_root)
    # The verified source contract contains exactly 2021/22. Later source files
    # are never opened; reject mislabeled rows rather than silently filtering.
    frame = pd.read_parquet(prediction_path, columns=REQUIRED, use_threads=False)
    reference = _read(reference_calibrator_path) if reference_calibrator_path else None
    prepared, calibrator, audit = development_frame(frame, source["folds"], reference_calibrator=reference)
    output.mkdir(parents=True, exist_ok=True)
    with (output / names[0]).open("xb") as stream:
        prepared.to_parquet(stream, index=False)
    _write(output / names[1], calibrator)
    provenance = {"schema": SCHEMA, "status": "completed", "track": "B", "source": source,
        "artifact": {"path": str(output / names[0]), "sha256": _sha(output / names[0]), "rows": len(prepared),
                     "races": int(prepared.race_id.nunique()), "columns": list(prepared.columns)},
        "seed_calibrator": {"path": str(output / names[1]), "sha256": _sha(output / names[1]),
                            "fit_date_from": calibrator["fit_date_from"], "fit_date_to": calibrator["fit_date_to"],
                            "temperature": calibrator["global"]["temperature"]},
        "audit": audit, "market_features": MARKET_FEATURES, "combined_features": COMBINED_FEATURES,
        "evaluation_only_columns": OUTCOME_ONLY + ["h04_probability_fixed_reference"],
        "chronology": {"calibration_seed": "2021-01-01..2021-06-30; probability left missing on fitted rows",
                       "meta_fit": "2021-07-01..2021-09-30", "development": "2021-10-01..2021-12-31",
                       "validation": "2022-01-01..2022-12-31",
                       "refit_policy": "after Q4 choices freeze, predeclared meta-model refit on H2-2021 only"},
        "historical_odds_timestamp_verified": False, "odds_role": "historical observed final/unspecified-time research proxy; not T-5 executable odds",
        "payout_policy": "confirmed single-winner JPY per 100 JPY; unknown remains NaN; race-level ROI cohort must be reported",
        "h04_configuration_reused_known_2021_2022_selection": True, "genuine_unseen_holdout": False,
        "h04_models_refitted": False, "serving_2026_weights_used_on_2021": False,
        "post_2022_outcomes_opened": False, "track_a_modified": False, "actual_bets": 0,
        "module_sha256": _sha(Path(__file__)),
        "calibration_module_sha256": _sha(repo / "python-api/prospective/calibration.py"),
        "limitations": ["H04 architecture/hyperparameters were already selected using 2021/22; fold OOT scores do not remove configuration-selection reuse.",
                        "2021/22 temperature used by Track A is contaminated for these years and must never supply Track B validation features or gates.",
                        "Complete runner target cohort excludes partially recorded results; not the full real-world race universe.",
                        "Unknown payout is not a losing ticket; odds cannot replace a missing confirmed dividend."]}
    if reference_calibrator_path:
        provenance["fixed_reference_artifact"] = {"path": str(Path(reference_calibrator_path).resolve()),
                                                  "sha256": _sha(reference_calibrator_path)}
    _write(output / names[2], provenance)
    return provenance
