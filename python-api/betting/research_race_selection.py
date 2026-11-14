"""Frozen, research-only race participation and win-ticket optimization."""
from __future__ import annotations

from dataclasses import dataclass
import hashlib
import itertools
import json
from pathlib import Path

import joblib
import numpy as np
import pandas as pd
from sklearn.compose import ColumnTransformer
from sklearn.ensemble import HistGradientBoostingClassifier
from sklearn.linear_model import LogisticRegression
from sklearn.pipeline import Pipeline
from sklearn.preprocessing import OneHotEncoder, StandardScaler
import sklearn
from threadpoolctl import threadpool_limits

from betting.value_policy import ODDS_HAIRCUT, PROBABILITY_HAIRCUT
from experiments.betting_search import (
    _bootstrap_plan, _confidence, _dates, _json, _normalize, _totals, _write_exclusive,
)
from experiments.common import REPO_ROOT, file_sha256

SCHEMA = "research-race-selection-v1"
NUMERIC_FEATURES = (
    "prediction", "standardized_score", "rank_fraction", "gap_to_top",
    "field_size", "score_std", "top_gap", "entropy", "distance",
)
CATEGORICAL_FEATURES = ("surface", "venue")
MODEL_NAMES = ("logistic", "hist_gradient_boosting")


def _features(frame: pd.DataFrame) -> pd.DataFrame:
    required = {"race_id", "horse_number", "prediction", "distance", "surface", "venue"}
    if not required.issubset(frame):
        raise ValueError("Race-relative scores and pre-race course metadata are required")
    work = frame.copy()
    score = pd.to_numeric(work["prediction"], errors="coerce")
    distance = pd.to_numeric(work["distance"], errors="coerce")
    if not np.isfinite(score).all() or not np.isfinite(distance).all() or distance.le(0).any():
        raise ValueError("Scores and distances must be finite")
    groups = work["race_id"]
    counts = score.groupby(groups).transform("size")
    declared = [name for name in ("expected_runner_count", "num_horses") if name in work]
    if not declared or counts.lt(2).any() or work.duplicated(["race_id", "horse_number"]).any():
        raise ValueError("Complete, distinct runner cohorts are required")
    if any(not pd.to_numeric(work[name], errors="coerce").eq(counts).all() for name in declared):
        raise ValueError("Do not normalize probabilities over partial runner cohorts")
    for name in ("distance", *CATEGORICAL_FEATURES):
        if work[name].isna().any() or work.groupby("race_id")[name].nunique().ne(1).any():
            raise ValueError("Course metadata must be complete and consistent within each race")
    mean = score.groupby(groups).transform("mean")
    std = score.groupby(groups).transform("std").fillna(0)
    top = score.groupby(groups).transform("max")
    weights = np.exp(np.clip(score - top, -50, 0))
    probability = weights / weights.groupby(groups).transform("sum")
    top_two = work.assign(_score=score).sort_values(
        ["race_id", "_score", "horse_number"], ascending=[True, False, True],
    ).groupby("race_id", sort=False).head(2).copy()
    top_two["_position"] = top_two.groupby("race_id", sort=False).cumcount()
    pair = top_two.pivot(index="race_id", columns="_position", values="_score")
    return pd.DataFrame({
        "prediction": score,
        "standardized_score": (score - mean) / std.clip(lower=1e-8),
        "rank_fraction": score.groupby(groups).rank(ascending=False, method="average") / counts,
        "gap_to_top": (score - top) / std.clip(lower=1e-8),
        "field_size": counts,
        "score_std": std,
        "top_gap": groups.map(pair[0] - pair[1]),
        "entropy": (-probability * np.log(probability)).groupby(groups).transform("sum") / np.log(counts),
        "distance": distance,
        "surface": work["surface"].astype(str),
        "venue": work["venue"].astype(str),
    }, index=frame.index)


@dataclass
class RaceProbabilityModel:
    algorithm: str

    def fit(self, frame: pd.DataFrame) -> "RaceProbabilityModel":
        if self.algorithm not in MODEL_NAMES:
            raise ValueError("Unsupported race probability model")
        dates = _dates(frame["race_date"])
        winner = frame["finish"].eq(1).astype(int)
        if not winner.groupby(frame["race_id"]).sum().eq(1).all():
            raise ValueError("A sole winner is required for probability model fitting")
        estimator = (
            LogisticRegression(C=1.0, max_iter=1000, random_state=42)
            if self.algorithm == "logistic" else
            HistGradientBoostingClassifier(
                max_iter=100, max_leaf_nodes=7, min_samples_leaf=100,
                learning_rate=0.05, l2_regularization=10, early_stopping=False, random_state=42,
            )
        )
        self.pipeline = Pipeline([
            ("features", ColumnTransformer([
                ("numeric", StandardScaler(), list(NUMERIC_FEATURES)),
                ("categorical", OneHotEncoder(handle_unknown="ignore", sparse_output=False), list(CATEGORICAL_FEATURES)),
            ])),
            ("classifier", estimator),
        ])
        with threadpool_limits(limits=1):
            self.pipeline.fit(_features(frame), winner)
        self.fitted_through = dates.max()
        self.feature_source_sha256 = file_sha256(Path(__file__))
        return self

    def predict_probabilities(self, frame: pd.DataFrame) -> np.ndarray:
        if self.feature_source_sha256 != file_sha256(Path(__file__)):
            raise ValueError("race-probability-feature-source-mismatch")
        if _dates(frame["race_date"]).min() <= self.fitted_through:
            raise ValueError("Probability inference must be strictly after the fit period")
        with threadpool_limits(limits=1):
            raw = np.clip(self.pipeline.predict_proba(_features(frame))[:, 1], 1e-12, 1)
        probabilities = pd.Series(raw, index=frame.index)
        return (probabilities / probabilities.groupby(frame["race_id"]).transform("sum")).to_numpy()


def _probability_metrics(frame: pd.DataFrame, probabilities: np.ndarray) -> dict:
    winner = frame["finish"].eq(1).to_numpy(dtype=float)
    races = frame["race_id"].nunique()
    return {
        "race_count": int(races),
        "nll": float(-(winner * np.log(np.clip(probabilities, 1e-12, 1))).sum() / races),
        "brier_per_race": float(((probabilities - winner) ** 2).sum() / races),
    }


def _input_hash(frame: pd.DataFrame) -> str:
    columns = [
        "race_date", "race_id", "horse_number", "prediction", "finish", "odds",
        "popularity", "tansho_payout", "fold_train_date_to", "distance", "surface", "venue",
    ]
    ordered = frame.sort_values(["race_date", "race_id", "horse_number"])
    digest = hashlib.sha256(_json(columns).encode())
    for start in range(0, len(ordered), 4096):
        digest.update(ordered.iloc[start:start + 4096][columns].to_csv(
            index=False, header=False, na_rep="<missing>", float_format="%.17g", lineterminator="\n",
        ).encode())
    return digest.hexdigest()


def _catalog(profile: str) -> list[dict]:
    values = (
        itertools.product((1.0,), (5.0, 10.0), (.15, .25, .35), (1.05, 1.2))
        if profile == "steady" else
        itertools.product((5.0, 10.0), (30.0, 50.0), (.02, .05), (1.2, 1.5))
    )
    return [
        {"profile": profile, "odds_min": lower, "odds_max": upper,
         "minimum_probability": p, "minimum_buffered_ev": ev}
        for lower, upper, p, ev in values
    ]


def _pick(frame: pd.DataFrame, rule: dict) -> pd.DataFrame:
    eligible = frame.loc[
        frame["odds"].between(rule["odds_min"], rule["odds_max"])
        & frame["_probability"].ge(rule["minimum_probability"])
        & frame["_buffered_ev"].ge(rule["minimum_buffered_ev"])
    ]
    order = ["race_id", "_probability", "_buffered_ev", "horse_number"]
    if rule["profile"] == "longshot":
        order[1:3] = ["_buffered_ev", "_probability"]
    return eligible.sort_values(order, ascending=[True, False, False, True]).groupby("race_id", sort=False).head(1)


def _metrics(frame: pd.DataFrame, bets: pd.DataFrame, bootstrap: tuple) -> dict:
    metrics = _totals(bets, int(frame["race_id"].nunique()))
    metrics["hit_rate"] = metrics["win_bet_count"] / len(bets) if len(bets) else None
    metrics["date_bootstrap_net_roi_95_ci"] = _confidence(
        bets, *bootstrap, complete=metrics["payout_complete_for_bet_races"],
    )["net_roi_percent"]
    metrics["by_year"] = {
        str(year): _totals(bets.loc[bets["_year"].eq(year)], int(subset["race_id"].nunique()))
        for year, subset in frame.groupby("_year", sort=True)
    }
    if metrics["payout_complete_for_bet_races"]:
        daily = bets.groupby("race_date")["_return_yen"].agg(["sum", "size"])
        streak = longest = 0
        for losing in (daily["sum"] - daily["size"] * 100).lt(0):
            streak = streak + 1 if losing else 0
            longest = max(longest, streak)
        metrics["max_consecutive_losing_bet_days"] = longest
    else:
        metrics["max_consecutive_losing_bet_days"] = None
    return metrics


def _rejections(metrics: dict, min_bets: int, min_dates: int, min_hits: int) -> list[str]:
    reasons = []
    for name, minimum in (("bet_count", min_bets), ("bet_date_count", min_dates), ("win_bet_count", min_hits)):
        if metrics[name] < minimum:
            reasons.append(f"insufficient_{name}")
    if not metrics["payout_complete_for_bet_races"]:
        reasons.append("unverified_winner_payout")
    lower = metrics["date_bootstrap_net_roi_95_ci"]["lower"]
    if lower is None or lower <= 0:
        reasons.append("positive_lower_roi_bound_not_established")
    return reasons


def _selection_key(candidate: dict, profile: str) -> tuple:
    metrics = candidate["metrics"]
    lower = metrics["date_bootstrap_net_roi_95_ci"]["lower"]
    lower = lower if lower is not None else -float("inf")
    drawdown = metrics["max_drawdown_yen"]
    drawdown = drawdown if drawdown is not None else float("inf")
    hit_rate = metrics["hit_rate"] or 0
    return (
        (-hit_rate, drawdown, -lower, _json(candidate["rule"])) if profile == "steady"
        else (-lower, drawdown, -hit_rate, _json(candidate["rule"]))
    )


def _decisions(frame: pd.DataFrame, selections: dict, phase: str) -> pd.DataFrame:
    rows = []
    cohorts = frame[["race_id", "race_date"]].drop_duplicates()
    for profile, selection in selections.items():
        tickets = _pick(frame, selection["rule"]) if selection["admissible"] else frame.iloc[:0]
        by_race = tickets.set_index("race_id")
        for race_id, date in cohorts.itertuples(index=False, name=None):
            ticket = by_race.loc[race_id] if race_id in by_race.index else None
            priority = False
            if ticket is not None and selection["priority_supported"]:
                score = ticket["_probability"] if profile == "steady" else ticket["_buffered_ev"]
                priority = score >= selection["priority_threshold"]
            rows.append({
                "phase": phase, "race_id": race_id, "race_date": date, "profile": profile,
                "simulated_action": "priority_candidate" if priority else "candidate" if ticket is not None else "skip",
                "reason": "historically_supported_subset" if priority else "policy_conditions_met" if ticket is not None
                    else "insufficient_policy_evidence" if not selection["admissible"] else "no_qualifying_ticket",
                "horse_number": int(ticket["horse_number"]) if ticket is not None else None,
                "estimated_probability": float(ticket["_probability"]) if ticket is not None else None,
                "retrospective_odds_proxy": float(ticket["odds"]) if ticket is not None else None,
                "buffered_ev_proxy": float(ticket["_buffered_ev"]) if ticket is not None else None,
                "simulated_stake_yen": 100 if ticket is not None else 0,
                "purchase_eligible": False,
            })
    return pd.DataFrame(rows)


def optimize_race_selection(
    predictions: pd.DataFrame, output_dir: str | Path, *, model_id: str,
    model_fit_end: str = "20211231", model_choice_end: str = "20220630",
    policy_fit_end: str = "20221231", min_bets: int = 100, min_dates: int = 20,
    min_hits: int = 10, bootstrap_samples: int = 500, prediction_provenance: dict | None = None,
) -> dict:
    output = Path(output_dir).resolve()
    for protected in (
        REPO_ROOT / "python-api" / "models", REPO_ROOT / "python-api" / "data",
        REPO_ROOT / "keiba" / "models", REPO_ROOT / "keiba" / "data",
    ):
        if output == protected or output.is_relative_to(protected):
            raise ValueError("Research outputs must be isolated from production models and data")
    if output.exists():
        raise FileExistsError("Frozen race-selection runs cannot be overwritten")
    if not isinstance(model_id, str) or not model_id.strip():
        raise ValueError("A fixed base model identifier is required")
    boundaries = _dates(pd.Series([model_fit_end, model_choice_end, policy_fit_end])).tolist()
    if not boundaries[0] < boundaries[1] < boundaries[2]:
        raise ValueError("Model fit, model choice and policy fit must be chronological and disjoint")
    for value in (min_bets, min_dates, min_hits):
        if type(value) is not int or value < 1:
            raise ValueError("Evidence minimums must be positive integers")
    if type(bootstrap_samples) is not int or not 100 <= bootstrap_samples <= 5000:
        raise ValueError("Use 100-5000 date-bootstrap samples")
    if "fold_train_date_to" not in predictions or not any(
        name in predictions for name in ("expected_runner_count", "num_horses")
    ):
        raise ValueError("Explicit OOT training boundaries and declared runner counts are required")
    frame, audit = _normalize(predictions)
    context = predictions[["race_id", "horse_number", "distance", "surface", "venue"]].copy()
    context["race_id"] = context["race_id"].astype(str).str.strip()
    context["horse_number"] = pd.to_numeric(context["horse_number"], errors="raise").astype(int)
    frame = frame.merge(context, on=["race_id", "horse_number"], how="left", validate="one_to_one")
    _features(frame)
    winners = frame["finish"].eq(1).groupby(frame["race_id"]).transform("sum")
    audit["dead_heat_races_excluded_from_probability_cohort"] = int(frame.loc[winners.ne(1), "race_id"].nunique())
    frame = frame.loc[winners.eq(1)].reset_index(drop=True)
    fit = frame.loc[frame["race_date"].le(boundaries[0])]
    choice = frame.loc[frame["race_date"].gt(boundaries[0]) & frame["race_date"].le(boundaries[1])]
    policy = frame.loc[frame["race_date"].gt(boundaries[1]) & frame["race_date"].le(boundaries[2])].copy()
    if any(part.empty for part in (fit, choice, policy)):
        raise ValueError("All three chronological learning/selection periods need complete races")
    model_comparison = {}
    for name in MODEL_NAMES:
        model = RaceProbabilityModel(name).fit(fit)
        model_comparison[name] = _probability_metrics(choice, model.predict_probabilities(choice))
    winner = min(MODEL_NAMES, key=lambda name: (model_comparison[name]["nll"], name))
    fitted = frame.loc[frame["race_date"].le(boundaries[1])]
    model = RaceProbabilityModel(winner).fit(fitted)
    policy["_probability"] = model.predict_probabilities(policy)
    policy["_buffered_ev"] = policy["_probability"] * policy["odds"] * (1 - PROBABILITY_HAIRCUT) * (1 - ODDS_HAIRCUT)
    bootstrap = _bootstrap_plan(policy, bootstrap_samples, 42)
    searches, selections = {}, {}
    for profile in ("steady", "longshot"):
        candidates = []
        for rule in _catalog(profile):
            metrics = _metrics(policy, _pick(policy, rule), bootstrap)
            reasons = _rejections(metrics, min_bets, min_dates, min_hits)
            candidates.append({"rule": rule, "metrics": metrics, "rejection_reasons": reasons})
        eligible = [item for item in candidates if not item["rejection_reasons"]]
        selected = min(eligible or candidates, key=lambda item: _selection_key(item, profile))
        bets = _pick(policy, selected["rule"])
        score = "_probability" if profile == "steady" else "_buffered_ev"
        threshold = float(bets[score].quantile(.75)) if len(bets) else None
        priority_metrics = _metrics(
            policy, bets.loc[bets[score].ge(threshold)] if threshold is not None else bets, bootstrap,
        )
        selections[profile] = {
            "rule": selected["rule"], "admissible": not selected["rejection_reasons"],
            "rejection_reasons": selected["rejection_reasons"],
            "development_metrics": selected["metrics"],
            "priority_threshold": threshold,
            "priority_supported": bool(eligible) and not _rejections(priority_metrics, min_bets, min_dates, min_hits),
            "priority_development_metrics": priority_metrics,
        }
        searches[profile] = candidates
    output.mkdir(parents=True, exist_ok=False)
    (output / "implementation.py").write_bytes(Path(__file__).read_bytes())
    joblib.dump(model, output / "probability_model.joblib")
    probability_model = {
        "algorithm": winner, "sha256": file_sha256(output / "probability_model.joblib"),
        "fitted_through": model.fitted_through, "feature_columns": [*NUMERIC_FEATURES, *CATEGORICAL_FEATURES],
        "market_features_used": False, "production_probability_validated": False,
    }
    frozen = {
        "schema": SCHEMA, "model_id": model_id, "probability_model": probability_model,
        "model_fit_data_sha256": _input_hash(fitted), "policy_fit_data_sha256": _input_hash(policy),
        "model_comparison": model_comparison, "selection": selections,
        "boundaries": boundaries, "implementation_sha256": file_sha256(Path(__file__)),
        "minimum_evidence": {"bets": min_bets, "dates": min_dates, "hits": min_hits},
        "bootstrap_samples": bootstrap_samples, "automatic_purchase": False,
        "safety_buffers": {"probability": PROBABILITY_HAIRCUT, "odds": ODDS_HAIRCUT},
        "value_policy_sha256": file_sha256(Path(__file__).with_name("value_policy.py")),
        "library_versions": {"numpy": np.__version__, "pandas": pd.__version__, "sklearn": sklearn.__version__},
    }
    frozen_text = _json(frozen)
    frozen_sha = hashlib.sha256(frozen_text.encode()).hexdigest()
    _write_exclusive(output / "frozen_policy.json", frozen_text)
    _write_exclusive(output / "frozen_policy.sha256", frozen_sha + "\n")
    evaluation, decision_frames = {}, [_decisions(policy, selections, "policy_development")]
    for year, subset in frame.loc[frame["race_date"].gt(boundaries[2])].groupby("_year", sort=True):
        subset = subset.copy()
        subset["_probability"] = model.predict_probabilities(subset)
        subset["_buffered_ev"] = subset["_probability"] * subset["odds"] * (1 - PROBABILITY_HAIRCUT) * (1 - ODDS_HAIRCUT)
        plan = _bootstrap_plan(subset, bootstrap_samples, 42 + int(year))
        top_meta = subset.sort_values(
            ["race_id", "_probability", "horse_number"], ascending=[True, False, True],
        ).groupby("race_id", sort=False).head(1)
        calibration = []
        for lower, upper in ((1, 3), (3, 5), (5, 10), (10, 20), (20, 50), (50, np.inf)):
            band = subset.loc[subset["odds"].ge(lower) & subset["odds"].lt(upper)]
            calibration.append({
                "odds_band": f"{lower}-{upper}" if np.isfinite(upper) else "50+",
                "runner_count": len(band), "win_count": int(band["finish"].eq(1).sum()),
                "mean_probability": float(band["_probability"].mean()) if len(band) else None,
                "observed_win_rate": float(band["finish"].eq(1).mean()) if len(band) else None,
            })
        evaluation[str(year)] = {
            "known_reused_period": True, "unseen": False,
            "probability_metrics": _probability_metrics(subset, subset["_probability"].to_numpy()),
            "baselines": {
                "base_top1": _metrics(subset, subset.loc[subset["_rank"].eq(1)], plan),
                "meta_top1": _metrics(subset, top_meta, plan),
            },
            "profiles": {
                profile: {
                    "selected_policy": _metrics(subset, _pick(subset, selected["rule"]) if selected["admissible"] else subset.iloc[:0], plan),
                    "reference_rule": _metrics(subset, _pick(subset, selected["rule"]), plan),
                } for profile, selected in selections.items()
            },
            "calibration_by_retrospective_odds_band": calibration,
        }
        decision_frames.append(_decisions(subset, selections, str(year)))
    pd.concat(decision_frames, ignore_index=True).to_parquet(output / "race_decisions.parquet", index=False)
    report = {
        **frozen, "frozen_policy_sha256": frozen_sha, "source_predictions_sha256": _input_hash(frame),
        "input_audit": audit, "prediction_provenance": prediction_provenance or {},
        "selection_period": {"date_from_exclusive": boundaries[1], "date_to": boundaries[2]},
        "development_search": searches, "evaluation": evaluation,
        "market_policy": "retrospective_market_proxy",
        "production_rule_activated": False, "live_trading_reproduction": False, "unseen_period_claimed": False,
        "profiles_are_alternatives": True, "stake_yen_per_selected_ticket": 100,
        "limitations": [
            "Historical odds/popularity lack verified pre-bet timestamps; EV is a retrospective proxy.",
            "H04 OOT source predictions are not interchangeable with another active model.",
            "Model choice, probability fit and policy selection precede evaluation; evaluated years are known-reused.",
            "Date-bootstrap intervals are descriptive and not adjusted for condition selection.",
            "Incomplete payout nulls full monetary metrics; no odds-to-payout substitution.",
            "Dead-heat races are excluded in full from this sole-winner probability cohort.",
            "Priority is a historical subset label, never a larger stake or an executable purchase.",
            "Profiles are alternative plans, not a combined budget; production and automatic purchase remain disabled.",
        ],
    }
    _write_exclusive(output / "race_selection_report.json", _json(report))
    return report


def load_frozen_oof(experiment_dir: str | Path) -> tuple[pd.DataFrame, dict]:
    root = Path(experiment_dir).resolve()
    frozen_path = root / "betting" / "frozen_rule.json"
    if file_sha256(frozen_path) != (root / "betting" / "frozen_rule.sha256").read_text().strip():
        raise ValueError("Frozen source rule sha256 mismatch")
    frozen = json.loads(frozen_path.read_text(encoding="utf-8"))
    provenance = frozen["prediction_provenance"]
    frames = []
    for item in provenance["out_of_time_folds"]:
        path = Path(item["artifact"]["path"]).resolve()
        if not path.is_relative_to(root) or file_sha256(path) != item["artifact"]["sha256"]:
            raise ValueError("OOF source prediction sha256 mismatch or path outside experiment")
        frame = pd.read_parquet(path)
        boundaries = {int(fold["year"]): fold["train_period"][1] for fold in item["folds"]}
        years = pd.to_numeric(frame["fold_year"], errors="raise").astype(int)
        if not years.eq(_dates(frame["race_date"]).str[:4].astype(int)).all():
            raise ValueError("OOF fold year and prediction date disagree")
        train_end = years.map(boundaries)
        if train_end.isna().any():
            raise ValueError("Every OOT prediction needs its recorded fold training boundary")
        if "fold_train_date_to" in frame and not _dates(frame["fold_train_date_to"]).eq(_dates(train_end)).all():
            raise ValueError("OOF artifact and recorded training boundary disagree")
        frame["fold_train_date_to"] = train_end
        frames.append(frame)
    predictions = pd.concat(frames, ignore_index=True)
    manifest = json.loads((root / "dataset" / "dataset_manifest.json").read_text(encoding="utf-8"))
    dataset = root / "dataset" / "dataset.parquet"
    if file_sha256(dataset) != manifest["dataset_sha256"]:
        raise ValueError("Frozen course metadata dataset sha256 mismatch")
    context = pd.read_parquet(dataset, columns=["race_id", "horse_number", "distance", "venue"])
    predictions = predictions.merge(context, on=["race_id", "horse_number"], how="left", validate="one_to_one")
    return predictions, {
        "model_id": frozen["model_id"], "experiment_dir": str(root),
        "frozen_rule_sha256": file_sha256(frozen_path),
        "dataset_sha256": manifest["dataset_sha256"], **provenance,
    }
