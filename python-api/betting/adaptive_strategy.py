"""Causal, reference-only upset forecasts and bounded three-recipe previews."""
from __future__ import annotations

from datetime import datetime
import hashlib
from itertools import combinations, permutations
import json
import math
from pathlib import Path

import numpy as np
import pandas as pd
from sklearn.compose import ColumnTransformer
from sklearn.linear_model import LogisticRegression
from sklearn.preprocessing import OneHotEncoder, StandardScaler
from threadpoolctl import threadpool_limits

from betting.research_opponent_pairs import opponent_orders, registry, select_tickets

SCHEMA = "adaptive-three-v1"
BASE_MODEL_SHA = "2f193b42e0405f0bb5fe2c0450939373c8ed0531dbf06772745efe93bcf61d99"
CATEGORICAL = ["surface", "venue", "field_condition", "race_class"]
NUMERIC = [
    "runners", "q1", "q2", "q_top3", "q_top5", "market_gap", "market_entropy",
    "market_effective_runners", "favourite_odds", "odds_second_to_first",
    "ai_p1", "ai_p2", "ai_top3", "ai_gap", "ai_entropy", "axis_ai_probability",
    "axis_ai_rank_fraction", "ai_top1_market_rank_fraction", "ai_market_top3_overlap",
    "ai_top5_market_mass", "market_top3_ai_mass", "market_ai_js_divergence", "distance",
]
PARTNERS = [f"{prefix}_{name}" for prefix in ("b1", "b4", "h2", "h3")
            for name in ("probability", "odds", "market_share", "market_rank_fraction")]
TARGETS = ("upset", "axis", "axis_win", "f1", "wide", "trio", "f2")
COMPONENTS = ("f1", "wide", "trio", "f2")
RECIPES = {
    "candidate1": "ai__r1_4__w0_t0_f1",
    "candidate2": "hole__r2_3__w2_t0_f2",
    "candidate3": "hole__r2_3__w2_t1_f2",
}
RECIPE_DEFINITIONS = {name: registry()[key] for name, key in RECIPES.items()}


def source_sha() -> str:
    return hashlib.sha256(Path(__file__).read_bytes()).hexdigest()


def seal(value: dict) -> dict:
    body = {key: item for key, item in value.items() if key != "checksum"}
    return {**body, "checksum": hashlib.sha256(json.dumps(
        body, sort_keys=True, separators=(",", ":"), allow_nan=False,
    ).encode()).hexdigest()}


def check_artifact(value: dict) -> None:
    if (value.get("checksum") != seal(value)["checksum"] or value.get("schema") != SCHEMA
            or value.get("base_model_sha256") != BASE_MODEL_SHA
            or value.get("feature_source_sha256") != source_sha()):
        raise ValueError("incompatible_or_changed_adaptive_artifact")


def guard_date(value: str, fitted_through: str) -> None:
    for day in (value, fitted_through):
        if not isinstance(day, str) or len(day) != 8:
            raise ValueError("YYYYMMDD_dates_required")
        datetime.strptime(day, "%Y%m%d")
    if value <= fitted_through:
        raise ValueError("decision_must_be_strictly_after_training")


def race_features(field: pd.DataFrame, *, partners: bool = False) -> dict:
    work = field.sort_values("horse_number")
    if "num_horses" not in work or not work.num_horses.eq(len(work)).all():
        raise ValueError("complete_declared_runner_field_required")
    for name in ("race_id", "race_date"):
        if name in work and (work[name].isna().any() or work[name].nunique() != 1):
            raise ValueError("single_race_identity_required")
    axis_number, ai, hole = opponent_orders(work.to_dict("records"))
    odds, p = work.odds.to_numpy(float), work.probability.to_numpy(float)
    numbers, n = work.horse_number.to_numpy(), len(work)
    q = 1 / odds
    q /= q.sum()
    morder, aorder = np.lexsort((numbers, odds)), np.lexsort((numbers, -p))
    qm, pa = q[morder], p[aorder]
    axis = morder[0]
    middle = (q + p) / 2

    def entropy(values):
        return -float(np.sum(values * np.log(np.maximum(values, 1e-12)))) / np.log(n)

    result = {
        "runners": n, "q1": float(qm[0]), "q2": float(qm[1]),
        "q_top3": float(qm[:3].sum()), "q_top5": float(qm[:5].sum()),
        "market_gap": float(qm[0] - qm[1]), "market_entropy": entropy(q),
        "market_effective_runners": float(1 / np.sum(q * q)),
        "favourite_odds": float(odds[axis]),
        "odds_second_to_first": float(odds[morder[1]] / odds[axis]),
        "ai_p1": float(pa[0]), "ai_p2": float(pa[1]), "ai_top3": float(pa[:3].sum()),
        "ai_gap": float(pa[0] - pa[1]), "ai_entropy": entropy(p),
        "axis_ai_probability": float(p[axis]),
        "axis_ai_rank_fraction": float(np.flatnonzero(aorder == axis)[0] + 1) / n,
        "ai_top1_market_rank_fraction": float(np.flatnonzero(morder == aorder[0])[0] + 1) / n,
        "ai_market_top3_overlap": len(set(aorder[:3]) & set(morder[:3])) / 3,
        "ai_top5_market_mass": float(q[aorder[:5]].sum()),
        "market_top3_ai_mass": float(p[morder[:3]].sum()),
        "market_ai_js_divergence": float(.5 * np.sum(
            q * np.log(q / middle) + p * np.log(np.maximum(p, 1e-12) / middle))),
    }
    for name in ["distance", *CATEGORICAL]:
        values = work[name].dropna().unique()
        if len(values) > 1 or (name == "distance" and len(values) != 1):
            raise ValueError("consistent_prerace_metadata_required")
        result[name] = float(values[0]) if name == "distance" else str(values[0]) if len(values) else "unknown"
    if not math.isfinite(result["distance"]) or result["distance"] <= 0:
        raise ValueError("positive_distance_required")
    if partners:
        for prefix, number in (("b1", ai[0]), ("b4", ai[3]), ("h2", hole[1]), ("h3", hole[2])):
            index = int(np.flatnonzero(numbers == number)[0])
            result.update({
                f"{prefix}_probability": float(p[index]), f"{prefix}_odds": float(odds[index]),
                f"{prefix}_market_share": float(q[index]),
                f"{prefix}_market_rank_fraction": float(np.flatnonzero(morder == index)[0] + 1) / n,
            })
    return result


def race_outcomes(field: pd.DataFrame, trio: dict, wide: dict) -> dict:
    records = field.to_dict("records")
    axis, _, _ = opponent_orders(records)
    finish = {int(row.horse_number): int(row.finish) for row in field.itertuples()}
    if any(not np.isfinite(row.finish) or row.finish != int(row.finish) or row.finish < 1
           for row in field.itertuples()):
        raise ValueError("complete_official_finishes_required")
    wanted = sorted(finish.values())[:3]
    legal = {tuple(sorted(t)) for t in combinations(finish, 3) if sorted(finish[n] for n in t) == wanted}
    actual = {tuple(map(int, key.split("-"))) for key in trio}
    if not actual or actual != legal:
        raise ValueError("official_trio_and_finishes_disagree")
    ordered = {t for group in actual for t in permutations(group)
               if [finish[n] for n in t] == sorted(finish[n] for n in t)}
    market = field.sort_values(["odds", "horse_number"]).horse_number.tolist()
    ranks = {number: i + 1 for i, number in enumerate(market)}
    total = min(sum(ranks[n] for n in t) for t in actual)
    books = {name: select_tickets(records, definition) for name, definition in RECIPE_DEFINITIONS.items()}
    hit = lambda tickets, winning: int(any(t in winning for t in tickets))
    return {
        "rank_sum": total, "upset": 0 if total <= 10 else 1 if total <= 17 else 2,
        "axis": int(any(axis in t for t in actual)), "axis_win": int(finish[axis] == 1),
        "f1": hit(books["candidate1"]["三連単"], ordered),
        "wide": hit(books["candidate2"]["ワイド"], {tuple(map(int, key.split("-"))) for key in wide}),
        "trio": hit(books["candidate3"]["三連複"], actual),
        "f2": hit(books["candidate2"]["三連単"], ordered),
    }


def numeric_priority(field: pd.DataFrame) -> bool:
    candidates = []
    for row in field.itertuples():
        cp, co = row.probability * .9, row.odds * .95
        ev = cp * co
        if ev < 1.2 or co <= 1:
            continue
        amount = 100 * math.floor((min(300, 2500 * max(0, (ev - 1) / (co - 1))) + 1e-8) / 100)
        if amount >= 100:
            candidates.append((-ev, int(row.horse_number), row.probability))
    if not candidates:
        return False
    best = min(candidates)
    return bool(best[2] >= .15 and -best[0] >= 1.5)


def _fit_model(frame: pd.DataFrame, target: str) -> dict:
    numeric = NUMERIC if target in ("upset", "axis") else NUMERIC + PARTNERS
    transform = ColumnTransformer([
        ("numeric", StandardScaler(), numeric),
        ("categorical", OneHotEncoder(handle_unknown="ignore", sparse_output=False), CATEGORICAL),
    ])
    x = transform.fit_transform(frame)
    y = frame[target].to_numpy(int)
    scaler, encoder = transform.named_transformers_["numeric"], transform.named_transformers_["categorical"]
    result = {
        "numeric": numeric, "mean": scaler.mean_.tolist(), "scale": scaler.scale_.tolist(),
        "categories": [list(values) for values in encoder.categories_],
    }
    if target != "upset" and len(set(y)) == 1:
        return {**result, "constant_probability": float((y.sum() + 1) / (len(y) + 2))}
    classifier = LogisticRegression(C=1, max_iter=1500, random_state=42).fit(x, y)
    expected = [0, 1, 2] if target == "upset" else [0, 1]
    if classifier.classes_.tolist() != expected:
        raise ValueError("required_target_classes_missing")
    return {**result, "coefficient": classifier.coef_.tolist(), "intercept": classifier.intercept_.tolist()}


def fit_bundle(frame: pd.DataFrame, cutoff: str) -> dict:
    if frame.empty:
        raise ValueError("training_rows_required")
    pd.to_datetime(frame.race_date, format="%Y%m%d", errors="raise")
    guard_date(cutoff, frame.race_date.max())
    with threadpool_limits(limits=1):
        models = {target: _fit_model(frame, target) for target in TARGETS}
    return seal({
        "schema": SCHEMA, "feature_source_sha256": source_sha(), "base_model_sha256": BASE_MODEL_SHA,
        "fitted_through": frame.race_date.max(), "training_races": len(frame),
        "training_input_sha256": hashlib.sha256(frame[
            ["race_date", *NUMERIC, *PARTNERS, *CATEGORICAL, *TARGETS]
        ].to_csv(index=False, float_format="%.17g", lineterminator="\n").encode()).hexdigest(),
        "models": models, "reference_only": True, "purchase_eligible": False,
    })


def predict_frame(bundle: dict, frame: pd.DataFrame, base_model_sha: str) -> pd.DataFrame:
    check_artifact(bundle)
    if base_model_sha != BASE_MODEL_SHA or frame.empty:
        raise ValueError("compatible_base_model_and_rows_required")
    pd.to_datetime(frame.race_date, format="%Y%m%d", errors="raise")
    guard_date(frame.race_date.min(), bundle["fitted_through"])
    result = pd.DataFrame(index=frame.index)
    for target, model in bundle["models"].items():
        if "constant_probability" in model:
            result[target] = model["constant_probability"]
            continue
        x = (frame[model["numeric"]].to_numpy(float) - np.asarray(model["mean"])) / np.asarray(model["scale"])
        categorical = [np.column_stack([frame[name].to_numpy() == category for category in categories])
                       for name, categories in zip(CATEGORICAL, model["categories"])]
        x = np.column_stack([x, *categorical])
        if not np.isfinite(x).all():
            raise ValueError("finite_inference_features_required")
        z = x @ np.asarray(model["coefficient"]).T + np.asarray(model["intercept"])
        if target == "upset":
            raw = np.exp(z - z.max(axis=1, keepdims=True))
            for i, column in enumerate(("p0", "p1", "p2")):
                result[column] = (raw / raw.sum(axis=1, keepdims=True))[:, i]
        else:
            result[target] = 1 / (1 + np.exp(-np.clip(z[:, 0], -700, 700)))
    return result


def predict_bundle(bundle: dict, features: dict, decision_date: str, base_model_sha: str) -> dict:
    row = predict_frame(bundle, pd.DataFrame([{**features, "race_date": decision_date}]), base_model_sha).iloc[0]
    return {"upset": [float(row[name]) for name in ("p0", "p1", "p2")],
            **{name: float(row[name]) for name in TARGETS if name != "upset"}}


def fit_severity(history: pd.DataFrame, *, conservative: bool = False) -> dict:
    if history.empty or history.duplicated("race_id").any():
        raise ValueError("distinct_calibration_races_required")
    pd.to_datetime(history.race_date, format="%Y%m%d", errors="raise")
    for component in COMPONENTS:
        values = history[f"payout_{component}"].to_numpy(float)
        if not np.isfinite(values).all() or (values < 0).any():
            raise ValueError("nonnegative_verified_component_payoffs_required")

    def estimate(sample):
        days = sorted(sample.race_date.unique())
        weights = np.random.default_rng(20261103).multinomial(
            len(days), np.full(len(days), 1 / len(days)), size=500,
        )
        answer = {}
        for component in COMPONENTS:
            name = f"payout_{component}"
            positives = sample[name].gt(0)
            daily = sample.assign(_hit=positives.astype(int)).groupby("race_date")[[name, "_hit"]].sum().reindex(days)
            denominator = weights @ daily._hit.to_numpy()
            ratios = np.divide(weights @ daily[name].to_numpy(), denominator,
                               out=np.zeros(len(weights), dtype=float), where=denominator > 0)
            hits = int(positives.sum())
            answer[component] = {
                "hits": hits,
                "value": float(np.percentile(ratios, 10) if conservative else sample.loc[positives, name].mean())
                if hits >= 3 else 0.0,
            }
        return answer

    global_values = estimate(history)
    states = {}
    for state in range(3):
        sample = history.loc[history.forecast_class.eq(state)]
        supported = len(sample) >= 40 and sample.race_date.nunique() >= 12
        values = estimate(sample) if supported else global_values
        states[str(state)] = {
            key: {**(values[key] if values[key]["hits"] >= 3 else global_values[key]),
                  "global_fallback": not supported or values[key]["hits"] < 3}
            for key in COMPONENTS
        }
    return seal({
        "schema": SCHEMA, "feature_source_sha256": source_sha(), "base_model_sha256": BASE_MODEL_SHA,
        "fitted_through": history.race_date.max(), "calibration_races": len(history),
        "calibration_input_sha256": hashlib.sha256(history[
            ["race_id", "race_date", "forecast_class", *[f"payout_{name}" for name in COMPONENTS]]
        ].to_csv(index=False, float_format="%.17g", lineterminator="\n").encode()).hexdigest(),
        "mode": "lower10" if conservative else "mean", "states": states,
        "minimum_score": 1.10, "reference_only": True, "purchase_eligible": False,
    })


def choose_reference(probabilities: dict, policy: dict, forecast_class: int, decision_date: str) -> dict:
    check_artifact(policy)
    guard_date(decision_date, policy["fitted_through"])
    if forecast_class not in (0, 1, 2):
        raise ValueError("known_forecast_class_required")
    for name in COMPONENTS:
        if not np.isfinite(probabilities[name]) or not 0 <= probabilities[name] <= 1:
            raise ValueError("valid_event_probabilities_required")
    expected = {key: probabilities[key] * item["value"]
                for key, item in policy["states"][str(forecast_class)].items()}
    scores = {
        "candidate1": expected["f1"] / 200,
        "candidate2": (expected["wide"] + expected["f2"]) / 300,
        "candidate3": (expected["wide"] + expected["f2"] + expected["trio"]) / 400,
    }
    points = {"candidate1": 2, "candidate2": 3, "candidate3": 4}
    best = min(scores, key=lambda key: (-scores[key], points[key], key))
    return {"action": best if scores[best] > policy["minimum_score"] else "skip",
            "historical_payoff_scores": scores, "score_is_live_ticket_ev": False}


def preview(field: pd.DataFrame, bundle: dict, decision_date: str, base_model_sha: str,
            policy: dict | None = None) -> dict:
    if "race_date" in field and not field.race_date.eq(decision_date).all():
        raise ValueError("field_date_must_match_decision_date")
    predictions = predict_bundle(bundle, race_features(field, partners=True), decision_date, base_model_sha)
    probabilities = predictions["upset"]
    label = int(np.argmax(probabilities))
    reasons = []
    if not numeric_priority(field):
        reasons.append("original_numeric_priority_failed")
    if predictions["axis"] < .65:
        reasons.append("axis_top3_below_65_percent")
    if policy is None:
        reasons.append("policy_missing")
    choice = (choose_reference(predictions, policy, label, decision_date)
              if policy is not None else {"action": "skip"})
    action = choice["action"] if not reasons else "skip"
    if choice["action"] == "skip" and policy is not None:
        reasons.append("insufficient_historical_payoff_score")
    book = select_tickets(field.to_dict("records"), RECIPE_DEFINITIONS[action]) if action != "skip" else {}
    return {
        "schema": SCHEMA, "reference_only": True, "purchase_eligible": False,
        "upset_label": ["順当", "中荒れ", "大荒れ"][label],
        "upset_probabilities": dict(zip(("calm", "medium", "large"), probabilities)),
        "event_probabilities": {key: value for key, value in predictions.items() if key != "upset"},
        "action": action, "book": book, "skip_reasons": reasons,
        "model_fitted_through": bundle["fitted_through"],
        "limitations": ["stored_odds_not_proven_prestart", "previously_seen_test",
                        "base_probabilities_not_purchase_validated"],
    }
