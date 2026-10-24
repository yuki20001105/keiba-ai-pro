"""Research-only, repaired-snapshot context comparisons; no publication or DB IO.

The caller owns snapshot preparation and the bounded process pool.  This helper
fits the existing speed-regression implementation without modifying its source,
and reports ranking, physical speed error and probability calibration separately.
Saved odds are diagnostic strata only, never learner inputs or executable odds.
"""
from __future__ import annotations

import hashlib
import json
import math
import os
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
SCHEMA = "repaired-context-evaluation-v1"
GROUP_NAMES = ("baseline46", "condition_weight60", "condition_weight_gate_style73", "pedigree53", "full80")
ODDS_BANDS = ((1., 3., "1-<3"), (3., 5., "3-<5"), (5., 10., "5-<10"),
              (10., 20., "10-<20"), (20., 30., "20-<30"), (30., math.inf, "30+"))


def modules():
    for directory in (ROOT / "python-api", ROOT / "keiba", ROOT / "scripts"):
        if str(directory) not in sys.path:
            sys.path.insert(0, str(directory))
    from training import db_context_fit as fit, db_context_features as features
    return fit, features


def sha(path):
    digest = hashlib.sha256()
    with Path(path).open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def save(path, value):
    with Path(path).open("x", encoding="utf-8") as stream:
        json.dump(value, stream, ensure_ascii=False, indent=2, allow_nan=False)


def groups():
    _, features = modules()
    fixed = features.FEATURE_GROUPS
    base = list(fixed["baseline46"])
    conditional = base + fixed["condition_aptitude"] + fixed["weight"]
    result = {"baseline46": base, "condition_weight60": conditional,
              "condition_weight_gate_style73": conditional + fixed["gate"] + fixed["style_pace"],
              "pedigree53": base + fixed["pedigree"], "full80": list(features.FEATURE_COLUMNS)}
    if tuple(result) != GROUP_NAMES or [len(value) for value in result.values()] != [46, 60, 73, 53, 80]:
        raise ValueError("repaired-context-fixed-five-groups-required")
    return result


def validate_sources(sources):
    """Require the exact loaded fit dependencies plus optional repo-file pins."""
    fit, _ = modules()
    expected = fit.source_hashes()
    if not isinstance(sources, dict) or any(sources.get(name) != value for name, value in expected.items()):
        raise ValueError("repaired-context-fit-source-drift")
    for name, value in sources.items():
        if name in expected:
            continue
        path = Path(name)
        if not path.is_absolute():
            path = ROOT / path
        path = path.resolve(strict=True)
        if not path.is_relative_to(ROOT) or path.is_symlink() or sha(path) != value:
            raise ValueError("repaired-context-research-source-drift")
    if sources.get("scripts/repaired_context_evaluation.py") != sha(__file__):
        raise ValueError("repaired-context-evaluation-pin-required")


def partition_positions(target, year, period_end="20260930"):
    import numpy as np
    if type(year) is not int or year not in range(2016, 2027) or period_end != "20260930":
        raise ValueError("repaired-context-fixed-annual-period-required")
    return {"TRAIN": np.flatnonzero(target.race_date.between("20130101", f"{year-2}1231").to_numpy()),
            "CAL": np.flatnonzero(target.race_date.between(f"{year-1}0101", f"{year-1}1231").to_numpy()),
            "TEST": np.flatnonzero(target.race_date.between(f"{year}0101", min(f"{year}1231", period_end)).to_numpy())}


def worker(payload):
    for name in ("OMP_NUM_THREADS", "MKL_NUM_THREADS", "OPENBLAS_NUM_THREADS", "NUMEXPR_NUM_THREADS"):
        os.environ[name] = "2"
    os.environ["LOG_LEVEL"] = "CRITICAL"
    fit, _ = modules()
    import joblib
    import numpy as np
    import pandas as pd
    from threadpoolctl import threadpool_limits
    directory = Path(payload["output"]).resolve(strict=True)
    protocol_path = directory / "protocol.json"
    if sha(protocol_path) != payload["protocol_sha"]:
        raise ValueError("repaired-context-protocol-drift")
    protocol = json.loads(protocol_path.read_text(encoding="utf-8"))
    runner = Path(protocol["runner_path"])
    runner = runner if runner.is_absolute() else ROOT / runner
    if (not runner.resolve(strict=True).is_relative_to(ROOT)
            or protocol["runner_sha256"] != payload["runner_sha"] or sha(runner) != payload["runner_sha"]):
        raise ValueError("repaired-context-runner-drift")
    validate_sources(payload["sources"])
    if protocol.get("source_hashes") != payload["sources"] or protocol.get("groups") != groups():
        raise ValueError("repaired-context-protocol-source-or-group-drift")
    target_path, feature_path = Path(payload["target"]), Path(payload["features"])
    if sha(target_path) != payload["target_sha"] or sha(feature_path) != payload["features_sha"]:
        raise ValueError("repaired-context-matrix-drift")
    name, year = payload["group"], payload["year"]
    if name not in GROUP_NAMES:
        raise ValueError("repaired-context-unknown-group")
    if any((directory / f"{year}-{name}-{suffix}").exists() for suffix in
           ("predictions.parquet", "research.joblib", "result.json")):
        raise ValueError("repaired-context-existing-worker-artifacts-not-overwritten")
    target, raw = fit.prepare(pd.read_parquet(target_path), pd.read_parquet(feature_path))
    positions = partition_positions(target, year, payload["period_end"])
    with threadpool_limits(limits=2):
        bundle, result, output = fit.fit_on_partitions(target, raw, positions,
            columns=groups()[name], n_threads=2, preset_parallel=False)
    required = ["race_id", "race_date", "horse_number", "horse_id", "finish", "venue", "surface", "field_condition", "num_horses"]
    predicted = output["target"][required].copy()
    # An odds value cannot influence training: it is attached only after fit.
    predicted["odds"] = pd.to_numeric(output["target"].get("odds", pd.Series(np.nan, index=predicted.index)), errors="coerce")
    predicted["score"] = output["scores"]
    predicted["physical_speed"] = output["physical_speed"]
    truth = fit.legacy.raw_speed_mps(output["target"])
    predicted["squared_error"] = (output["physical_speed"] - truth.to_numpy()) ** 2
    predicted["probability"] = output["probabilities"] if output["probabilities"] is not None else np.nan
    destination = directory / f"{year}-{name}-predictions.parquet"
    if destination.exists():
        raise ValueError("repaired-context-existing-worker-result-not-overwritten")
    predicted.to_parquet(destination, index=False)
    artifact = directory / f"{year}-{name}-research.joblib"
    if artifact.exists():
        raise ValueError("repaired-context-existing-research-bundle-not-overwritten")
    bundle["research_run"] = {"schema": SCHEMA, "protocol_sha256": payload["protocol_sha"],
                              "test_year": year, "group": name, "activation": False, "publication": False}
    joblib.dump(bundle, artifact)
    validate_sources(payload["sources"])
    if (sha(protocol_path) != payload["protocol_sha"] or sha(runner) != payload["runner_sha"]
            or sha(target_path) != payload["target_sha"] or sha(feature_path) != payload["features_sha"]):
        raise ValueError("repaired-context-input-changed-during-fit")
    result.update({"test_year": year, "group": name, "prediction_file": destination.name,
        "prediction_sha256": sha(destination), "research_bundle": artifact.name,
        "research_bundle_sha256": sha(artifact), "protocol_sha256": payload["protocol_sha"],
        "runner_sha256": payload["runner_sha"], "research_source_hashes": payload["sources"],
        "odds_diagnostic_only": True, "market_features": False, "historical_label": "known retrospective data",
        "source_population": "all eligible JRA flat; strict prior source dates", "model_activated": False})
    save(directory / f"{year}-{name}-result.json", result)
    return {"test_year": year, "group": name, "elapsed_seconds": result["elapsed_seconds"]}


def wilson_interval(wins, count):
    if type(count) is not int or type(wins) is not int or count < 0 or not 0 <= wins <= count:
        raise ValueError("repaired-context-calibration-count-invalid")
    if count == 0:
        return [None, None]
    z = 1.959963984540054
    proportion = wins / count
    denominator = 1 + z * z / count
    center = (proportion + z * z / (2 * count)) / denominator
    radius = z * math.sqrt(proportion * (1 - proportion) / count + z * z / (4 * count * count)) / denominator
    return [max(0., center - radius), min(1., center + radius)]


def calibration_table(frame, *, selected=False, odds_bands=False):
    """Observed alignment only; no fitting, selection optimization or ROI."""
    import numpy as np
    import pandas as pd
    data = frame.copy()
    if selected:
        data = data.sort_values(["race_id", "probability", "horse_number"], ascending=[True, False, True], kind="stable").drop_duplicates("race_id")
    if odds_bands:
        odds = pd.to_numeric(data.odds, errors="coerce")
        labels = pd.Series("missing_or_invalid", index=data.index, dtype="string")
        for lower, upper, label in ODDS_BANDS:
            labels.loc[np.isfinite(odds) & odds.ge(lower) & odds.lt(upper)] = label
        bins = [(label, labels.eq(label)) for *_bounds, label in ODDS_BANDS]
        bins.append(("missing_or_invalid", labels.eq("missing_or_invalid")))
    else:
        bins = [(f"{number/10:.1f}-{'< ' if number < 9 else '<= '}{(number+1)/10:.1f}",
                 data.probability.ge(number/10) & (data.probability.lt((number+1)/10) if number < 9 else data.probability.le(1.)))
                for number in range(10)]
    result = []
    for label, mask in bins:
        section = data.loc[mask]
        count, wins = len(section), int(section.finish.eq(1).sum())
        predicted = float(section.probability.mean()) if count else None
        observed = wins / count if count else None
        result.append({"band": label, "count": count, "wins": wins, "mean_predicted": predicted,
                       "observed_win_rate": observed, "observed_win_rate_ci95_wilson": wilson_interval(wins, count),
                       "prediction_minus_observed": predicted - observed if count else None})
    return result


def race_metrics(frame):
    import numpy as np
    import pandas as pd
    required = {"race_id", "race_date", "horse_number", "finish", "venue", "surface", "field_condition", "score", "squared_error", "probability", "num_horses", "odds"}
    if not required <= set(frame) or frame.empty or frame.duplicated(["race_id", "horse_number"]).any():
        raise ValueError("repaired-context-complete-predictions-required")
    data = frame.copy().reset_index(drop=True)
    grouped = data.groupby("race_id", sort=True)
    sizes = grouped.race_id.transform("size")
    numbers = pd.to_numeric(data.horse_number, errors="coerce")
    finish = pd.to_numeric(data.finish, errors="coerce")
    if (not np.isfinite(data[["score", "squared_error"]].to_numpy(float)).all()
            or data.squared_error.lt(0).any() or not sizes.between(2, 18).all()
            or not data.num_horses.eq(sizes).all()
            or not np.isfinite(numbers).all() or not numbers.between(1, 18).all() or not numbers.eq(np.floor(numbers)).all()
            or not np.isfinite(finish).all() or not finish.between(1, sizes).all() or not finish.eq(np.floor(finish)).all()
            or not data.finish.eq(1).groupby(data.race_id).any().all()
            or grouped[["race_date", "venue", "surface", "field_condition"]].nunique(dropna=False).ne(1).any().any()):
        raise ValueError("repaired-context-race-integrity-invalid")
    rank_a = grouped.score.rank(method="average")
    rank_b = (-data.finish).groupby(data.race_id).rank(method="average")
    rank_a -= rank_a.groupby(data.race_id).transform("mean")
    rank_b -= rank_b.groupby(data.race_id).transform("mean")
    cross = (rank_a * rank_b).groupby(data.race_id).sum()
    denominator = np.sqrt((rank_a ** 2).groupby(data.race_id).sum() * (rank_b ** 2).groupby(data.race_id).sum())
    races = grouped.agg(race_date=("race_date", "first"), venue=("venue", "first"), surface=("surface", "first"),
                       field_condition=("field_condition", "first"), squared_error=("squared_error", "sum"), runner_count=("horse_number", "size"))
    races["rank_correlation"] = (cross / denominator.where(denominator.gt(0))).fillna(0.)
    picks = data.sort_values(["race_id", "score", "horse_number"], ascending=[True, False, True], kind="stable").drop_duplicates("race_id").set_index("race_id")
    races["top1"] = picks.finish.eq(1)
    winners = data.finish.eq(1).groupby(data.race_id).sum()
    probability_counts = grouped.probability.count()
    sizes = grouped.size()
    if ((probability_counts != 0) & (probability_counts != sizes)).any():
        raise ValueError("repaired-context-partial-race-probabilities-invalid")
    finite_rows = data.probability.notna()
    p = data.loc[finite_rows, "probability"].to_numpy(float)
    if not np.isfinite(p).all() or ((p < 0.) | (p > 1.)).any():
        raise ValueError("repaired-context-invalid-probability")
    available = probability_counts.eq(sizes)
    sums = grouped.probability.sum(min_count=1)
    if not np.allclose(sums.loc[available].to_numpy(float), 1., rtol=0., atol=1e-10):
        raise ValueError("repaired-context-race-probability-normalization-invalid")
    eligible = available & winners.eq(1)
    probabilistic = data.loc[data.race_id.isin(eligible.index[eligible])].copy()
    races["probability_eligible"] = eligible
    races["nll"] = math.nan
    races["brier"] = math.nan
    if not probabilistic.empty:
        single = probabilistic.loc[probabilistic.finish.eq(1)].set_index("race_id")
        races.loc[single.index, "nll"] = -np.log(np.maximum(single.probability.to_numpy(float), 1e-15))
        errors = (probabilistic.probability - probabilistic.finish.eq(1).astype(float)) ** 2
        races.loc[single.index, "brier"] = errors.groupby(probabilistic.race_id).sum().reindex(single.index).to_numpy(float)
    return races.reset_index(), probabilistic


def accuracy(races):
    import numpy as np
    if races.empty:
        return {"race_count": 0, "runner_count": 0, "rank_correlation": None, "rmse_speed_mps": None, "top1": None}
    runners = int(races.runner_count.sum())
    return {"race_count": len(races), "runner_count": runners,
            "rank_correlation": float(races.rank_correlation.mean()),
            "rmse_speed_mps": float(np.sqrt(races.squared_error.sum() / runners)), "top1": float(races.top1.mean())}


def probability_summary(races, probabilistic):
    selected = calibration_table(probabilistic, selected=True)
    all_runner = calibration_table(probabilistic, selected=False)
    count = len(probabilistic.drop_duplicates("race_id"))
    ece = sum(row["count"] * abs(row["prediction_minus_observed"]) for row in selected if row["count"]) / count if count else None
    all_runner_ece = (sum(row["count"] * abs(row["prediction_minus_observed"]) for row in all_runner if row["count"])
                      / len(probabilistic) if len(probabilistic) else None)
    return {"status": "available" if count else "unavailable", "race_count": count, "runner_count": len(probabilistic),
            "total_races": len(races), "unavailable_or_dead_heat_races": len(races) - count,
            "nll": float(races.nll.dropna().mean()) if count else None,
            "brier": float(races.brier.dropna().mean()) if count else None, "selection_ece": ece,
            "all_runner_ece": all_runner_ece, "all_runner_probability_calibration": all_runner,
            "top1_probability_calibration": selected,
            "all_runner_odds_calibration": calibration_table(probabilistic, odds_bands=True),
            "top1_odds_calibration": calibration_table(probabilistic, selected=True, odds_bands=True),
            "odds_are_saved_hindsight_strata_not_prestart_execution_quotes": True,
            "calibration_retrained_in_aggregation": False, "profitability_verified": False}


def paired_comparison(current, reference, *, repetitions=1000, seed=20261017):
    import numpy as np
    import pandas as pd
    if type(repetitions) is not int or repetitions < 2 or type(seed) is not int or seed < 0:
        raise ValueError("repaired-context-bootstrap-arguments-invalid")
    left, right = current.set_index("race_id").sort_index(), reference.set_index("race_id").sort_index()
    if (not left.index.equals(right.index) or not left.runner_count.equals(right.runner_count)
            or not left.race_date.equals(right.race_date)):
        raise ValueError("repaired-context-identical-whole-races-required")
    delta = pd.DataFrame({"date": right.race_date, "rho": left.rank_correlation - right.rank_correlation,
        "left_se": left.squared_error, "right_se": right.squared_error, "runners": right.runner_count})
    days = delta.groupby("date", sort=True).agg(rho=("rho", "sum"), races=("rho", "size"),
        left_se=("left_se", "sum"), right_se=("right_se", "sum"), runners=("runners", "sum"))
    array = days.to_numpy(float)
    random = np.random.default_rng(seed)
    # Cluster resampling preserves same-day dependence. No thresholds are tuned.
    sums = array[random.integers(0, len(array), size=(repetitions, len(array)))].sum(axis=1)
    rho = sums[:, 0] / sums[:, 1]
    rmse = np.sqrt(sums[:, 2] / sums[:, 4]) - np.sqrt(sums[:, 3] / sums[:, 4])
    current_accuracy, reference_accuracy = accuracy(current), accuracy(reference)
    valid = left.probability_eligible & right.probability_eligible
    probability_delta = {"common_probability_races": int(valid.sum()), "nll_delta": None, "brier_delta": None,
                         "nll_delta_ci95": [None, None], "brier_delta_ci95": [None, None]}
    if valid.any():
        values = pd.DataFrame({"date": right.loc[valid, "race_date"],
            "nll": left.loc[valid, "nll"] - right.loc[valid, "nll"],
            "brier": left.loc[valid, "brier"] - right.loc[valid, "brier"]})
        probability_days = values.groupby("date", sort=True).agg(nll=("nll", "sum"), brier=("brier", "sum"), races=("nll", "size")).to_numpy(float)
        sampled = probability_days[random.integers(0, len(probability_days), size=(repetitions, len(probability_days)))].sum(axis=1)
        probability_delta.update(nll_delta=float(values.nll.mean()), brier_delta=float(values.brier.mean()),
            nll_delta_ci95=np.quantile(sampled[:, 0] / sampled[:, 2], [.025, .975]).tolist(),
            brier_delta_ci95=np.quantile(sampled[:, 1] / sampled[:, 2], [.025, .975]).tolist())
    years = current.race_date.astype(str).str[:4]
    reference_years = reference.race_date.astype(str).str[:4]
    year_rank = current.groupby(years).rank_correlation.mean()
    baseline_rank = reference.groupby(reference_years).rank_correlation.mean()
    return {"race_count": len(current), "date_count": len(days),
            "rank_delta": current_accuracy["rank_correlation"] - reference_accuracy["rank_correlation"],
            "rank_delta_ci95": np.quantile(rho, [.025, .975]).tolist(),
            "rmse_delta_mps": current_accuracy["rmse_speed_mps"] - reference_accuracy["rmse_speed_mps"],
            "rmse_relative_change": current_accuracy["rmse_speed_mps"] / reference_accuracy["rmse_speed_mps"] - 1 if reference_accuracy["rmse_speed_mps"] else None,
            "rmse_delta_ci95": np.quantile(rmse, [.025, .975]).tolist(),
            "years_with_better_rank": int((year_rank > baseline_rank).sum()),
            "top1_delta": current_accuracy["top1"] - reference_accuracy["top1"],
            "probability": probability_delta, "bootstrap_unit": "whole source date", "bootstrap_repetitions": repetitions,
            "test_used_to_select_or_refit": False, "production_activation": False}


def aggregate(directory, protocol):
    import pandas as pd
    directory = Path(directory)
    if protocol != json.loads((directory / "protocol.json").read_text(encoding="utf-8")):
        raise ValueError("repaired-context-aggregate-protocol-drift")
    validate_sources(protocol["source_hashes"])
    expected_groups = groups()
    if protocol.get("groups") != expected_groups or protocol.get("test_years") != list(range(2016, 2027)):
        raise ValueError("repaired-context-fixed-55-evaluations-required")
    protocol_sha = sha(directory / "protocol.json")
    summaries, races_by_group, identities = {}, {}, None
    for name in GROUP_NAMES:
        frames, years = [], []
        for year in protocol["test_years"]:
            path = directory / f"{year}-{name}-result.json"
            item = json.loads(path.read_text(encoding="utf-8"))
            prediction_path = directory / item["prediction_file"]
            artifact_path = directory / item["research_bundle"]
            if (item.get("test_year") != year or item.get("group") != name
                    or item.get("protocol_sha256") != protocol_sha
                    or item.get("research_source_hashes") != protocol["source_hashes"]
                    or item.get("runner_sha256") != protocol["runner_sha256"]
                    or prediction_path.parent != directory or prediction_path.name != f"{year}-{name}-predictions.parquet"
                    or artifact_path.parent != directory or artifact_path.name != f"{year}-{name}-research.joblib"
                    or sha(prediction_path) != item.get("prediction_sha256")
                    or sha(artifact_path) != item.get("research_bundle_sha256")):
                raise ValueError("repaired-context-saved-result-lineage-invalid")
            frame = pd.read_parquet(prediction_path)
            if not frame.race_date.astype(str).str[:4].eq(str(year)).all():
                raise ValueError("repaired-context-fold-test-year-conflict")
            frames.append(frame)
            years.append({"year": year, "accuracy": item["evaluation"]["primary"],
                          "probability": item["probability_metrics"], "probability_validation": item["probability_validation"],
                          "selected_preset": item["selected_preset"], "seconds": item["elapsed_seconds"]})
        frame = pd.concat(frames, ignore_index=True)
        current_ids = frame[["race_id", "race_date", "horse_number", "finish", "num_horses"]].sort_values(["race_id", "horse_number"]).reset_index(drop=True)
        if identities is None:
            identities = current_ids
        elif not current_ids.equals(identities):
            raise ValueError("repaired-context-identical-runner-outcomes-required")
        races, probabilistic = race_metrics(frame)
        races_by_group[name] = races
        summaries[name] = {"accuracy": accuracy(races), "probability": probability_summary(races, probabilistic), "years": years,
            "segments": [{"dimension": column, "label": str(label), **accuracy(part)}
                         for column in ("venue", "surface", "field_condition") for label, part in races.groupby(column, sort=True, dropna=False)]}
    repetitions, seed = protocol.get("bootstrap_repetitions", 1000), protocol.get("bootstrap_seed", 20261017)
    comparisons = {name: paired_comparison(races_by_group[name], races_by_group["baseline46"], repetitions=repetitions, seed=seed)
                   for name in GROUP_NAMES if name != "baseline46"}
    pedigree_increment = paired_comparison(races_by_group["full80"], races_by_group["condition_weight_gate_style73"], repetitions=repetitions, seed=seed)
    validate_sources(protocol["source_hashes"])
    return {"schema": SCHEMA, "summaries": summaries, "vs_baseline46": comparisons,
            "pedigree_increment_full80_vs_context73": pedigree_increment,
            "pedigree53_vs_baseline46": comparisons["pedigree53"],
            "historical_label": "retrospective known data, not unknown future holdout", "target": "speed_deviation",
            "rmse_unit": "physical speed m/s", "probability_calibration": "CAL-only scalar race softmax temperature",
            "active_winner_nll_model_is_not_the_speed_baseline": True, "roi_evaluated": False,
            "model_activated": False, "profitability_verified": False, "test_driven_optimization": False,
            "protocol_sha256": protocol_sha, "source_hashes": protocol["source_hashes"]}
