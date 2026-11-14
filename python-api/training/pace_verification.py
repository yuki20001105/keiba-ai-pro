"""Independent passage, model-score, ticket-money and timing verification."""
from __future__ import annotations

from pathlib import Path

import joblib
import numpy as np
import pandas as pd

from betting import adaptive_strategy as axis
from training import db_context_features as context
from training import db_shared_grouped as grouped
from training import pace_features as features
from training.pace_comparison import _task, read, sha, write


def rebuild_styles(target, history):
    columns = list(features.STYLE_COLUMNS)
    output = {name: np.full(len(target), np.nan, dtype=np.float32) for name in columns}
    horses = set(target.horse_id.dropna().astype(str))
    past = history.loc[history.horse_id.isin(horses)].sort_values(
        ["race_date", "race_id", "horse_number"], kind="stable")
    lookup = {}
    for horse, part in past.groupby("horse_id", sort=False):
        lookup[str(horse)] = (
            part.race_date.to_numpy(dtype=str),
            list(zip(part.num_horses, part[context._CORNER_COLUMN])),
        )
    for index, row in enumerate(target.itertuples(index=False)):
        dates, records = lookup.get(str(row.horse_id), (np.array([], dtype=str), []))
        end = int(np.searchsorted(dates, str(row.race_date), side="left"))
        valid = []
        for count, corners in records[max(0, end - 5):end]:
            if corners:
                valid.append(((corners[0] - 1) / (count - 1),
                              (corners[0] - corners[-1]) / (count - 1)))
        output["ctx_style_count5"][index] = len(valid)
        if valid:
            early, gain = np.asarray(valid, dtype=float).T
            output["ctx_early_position_mean5"][index] = early.mean()
            output["ctx_front_rate5"][index] = np.mean(early <= 1 / 3)
            output["ctx_late_gain_mean5"][index] = gain.mean()
            if len(valid) >= 2:
                output["ctx_early_position_std5"][index] = early.std(ddof=1)
    front = pd.Series(output["ctx_front_rate5"])
    race = target.race_id.reset_index(drop=True)
    known = front.groupby(race).transform("count")
    total = front.groupby(race).transform("sum", min_count=1)
    others = (total - front.fillna(0)) / (known - front.notna().astype(int)).replace(0, np.nan)
    output["ctx_field_front_pressure"] = (total / known.replace(0, np.nan)).to_numpy(np.float32)
    output["ctx_other_front_pressure"] = others.to_numpy(np.float32)
    output["ctx_field_style_coverage"] = (
        known / race.groupby(race).transform("size")).to_numpy(np.float32)
    output["ctx_style_pressure_interaction"] = (front * others).to_numpy(np.float32)
    return pd.DataFrame(output, index=target.index)[columns]


def _rebuild_proxies(target, style):
    result = pd.DataFrame(np.nan, index=target.index, columns=features.PROXY_COLUMNS)
    for _, positions in target.groupby("race_id", sort=False).indices.items():
        frame = style.iloc[positions]
        early, front = frame.ctx_early_position_mean5.to_numpy(), frame.ctx_front_rate5.to_numpy()
        gain = frame.ctx_late_gain_mean5.to_numpy()
        valid = frame.ctx_style_count5.ge(3).to_numpy() & np.isfinite(early) & np.isfinite(front)
        count, size = int(valid.sum()), len(positions)
        leaders = valid & (front >= .6)
        for local, position in enumerate(positions):
            row = target.index[position]
            result.loc[row, "pace_pressure_coverage"] = count / size
            if count == size:
                result.loc[row, "pace_lone_front"] = float(leaders[local] and leaders.sum() == 1)
            if not valid[local] or count < 2:
                continue
            equal = int(np.sum(early[valid] == early[local]))
            lower = int(np.sum(early[valid] < early[local]))
            rival_count = int(leaders.sum()) - int(leaders[local])
            others = valid.copy()
            others[local] = False
            result.loc[row, "pace_expected_position_rank"] = (lower + (equal - 1) / 2) / (count - 1)
            result.loc[row, "pace_front_rival_count"] = rival_count
            result.loc[row, "pace_front_dominance"] = front[local] - float(front[others].astype(float).mean())
            result.loc[row, "pace_closing_pressure_interaction"] = gain[local] * rival_count / (size - 1)
    return result.astype(np.float32)


def book_money(field, dividends, selected):
    if not selected:
        return {"cost_yen": 0, "return_yen": 0, "hit_ticket_count": 0, "combinations": [],
                "bet_types": [], "stakes_yen": [], "official_payouts_per100_yen": []}
    market = field.sort_values(["odds", "horse_number"])
    favorite = int(market.iloc[0].horse_number)
    pool = field.loc[field.horse_number.ne(favorite)].sort_values(
        ["probability", "horse_number"], ascending=[False, True]).head(5)
    hole = pool.assign(value=pool.probability * pool.odds).sort_values(
        ["value", "horse_number"], ascending=[False, True])
    second, third = map(int, hole.horse_number.iloc[1:3])
    key = lambda numbers: "-".join(map(str, numbers))
    tickets = [
        ("ワイド", key(sorted((favorite, third)))),
        ("三連単", key((second, favorite, third))),
        ("三連単", key((third, favorite, second))),
    ]
    if any(not dividends.get(kind) for kind, _ in tickets):
        raise ValueError("independent-official-markets-required")
    payouts = [int(dividends[kind].get(number, 0)) for kind, number in tickets]
    return {"cost_yen": 1500, "return_yen": sum(payouts) * 5,
            "hit_ticket_count": sum(value > 0 for value in payouts),
            "combinations": [number for _, number in tickets],
            "bet_types": [kind for kind, _ in tickets], "stakes_yen": [500] * 3,
            "official_payouts_per100_yen": payouts}


def verify(root):
    from training import db_shared_fit as legacy
    from training.pace_comparison import PROTOCOL, check_approval, predict_axis

    task = _task(root)
    check_approval(task, read(root / "approval.json"), sha(root / "task-ledger.json"))
    manifest = read(root / "artifact-manifest.json")
    for name, digest in manifest["files"].items():
        if sha(root / name) != digest:
            raise ValueError(f"pace-saved-artifact-changed:{name}")
    report = read(root / "report.json")
    if report["protocol"] != PROTOCOL:
        raise ValueError("pace-verification-protocol-mismatch")
    target = pd.read_parquet(task["inputs"]["target"]["path"])
    saved_features = pd.read_parquet(task["inputs"]["features.parquet"]["path"])
    original = pd.read_parquet(task["inputs"]["original_features"]["path"])
    pd.testing.assert_frame_equal(saved_features[list(grouped.FEATURE_COLUMNS)], original, check_flags=False)
    print("Independently rebuilding every runner's strictly-prior five-passage style", flush=True)
    _, history, _ = context.load_db_frames(task["inputs"]["snapshot"]["path"], "2013-01",
        pd.to_datetime(target.race_date.max(), format="%Y%m%d").strftime("%Y-%m-%d"))
    rebuilt = rebuild_styles(target, history)
    pd.testing.assert_frame_equal(saved_features[list(features.STYLE_COLUMNS)], rebuilt,
                                  check_exact=True, check_flags=False)
    proxy = _rebuild_proxies(target, rebuilt)
    np.testing.assert_allclose(saved_features[list(features.PROXY_COLUMNS)],
                               proxy, rtol=0, atol=2e-7, equal_nan=True)
    splits = legacy.date_partitions(target)
    fields, axis_forecasts, checked_scores, checked_axis = {}, {}, 0, 0
    baseline = joblib.load(task["inputs"]["baseline"]["path"])
    raw = pd.read_parquet(task["inputs"]["baseline_predictions"]["path"])
    score, probabilities = grouped.predict_shared_scores(
        baseline, saved_features.iloc[splits["TEST"]][list(grouped.FEATURE_COLUMNS)], raw.race_id)
    np.testing.assert_allclose(raw.score, score, rtol=0, atol=0)
    np.testing.assert_allclose(raw.probability, probabilities, rtol=0, atol=0)
    fields["baseline"] = raw
    for name in features.GROUPS:
        model_path = root / name / "model.joblib"
        bundle = joblib.load(model_path)
        if bundle["feature_columns"] != list(features.GROUPS[name]):
            raise ValueError("pace-saved-model-feature-contract-mismatch")
        if (bundle["probability_validation"]["eligible_for_ev"] is not False
                or bundle["training_date_to"] >= task["partitions"]["CAL"]["date_from"]):
            raise ValueError("pace-purchase-or-temporal-boundary-invalid")
        expected_baseline = grouped.grouping.fit_group_baseline(target.iloc[splits["TRAIN"]], grouped.ARM)
        if grouped._baseline_hash(bundle["baseline"]) != grouped._baseline_hash(expected_baseline):
            raise ValueError("pace-target-normalization-changed")
        prediction = pd.read_parquet(root / name / "predictions.parquet")
        pd.testing.assert_frame_equal(prediction[list(target.columns)], target.iloc[splits["TEST"]].reset_index(drop=True))
        encoded = bundle["encoder"].transform(
            saved_features.iloc[splits["TEST"]][list(features.GROUPS[name])])
        score = np.asarray(bundle["model"].predict(encoded, num_threads=2))
        probability = bundle["calibrator"].predict_proba(score, prediction.race_id)
        np.testing.assert_allclose(prediction.score, score, rtol=0, atol=0)
        np.testing.assert_allclose(prediction.probability, probability, rtol=0, atol=0)
        np.testing.assert_allclose(prediction.groupby("race_id").probability.sum(), 1, atol=1e-10)
        checked_scores += len(score)
        fields[name] = prediction
        data = pd.read_parquet(root / name / "axis_features.parquet")
        forecast = pd.read_parquet(root / name / "axis_forecasts.parquet")
        axis_forecasts[name] = forecast.set_index("race_id")
        if not forecast.fit_through.lt(forecast.race_date).all():
            raise ValueError("pace-axis-same-day-or-future-fit")
        for path in sorted((root / name / "axis_models").glob("*.json")):
            bundle = read(path)
            selected = forecast.loc[forecast.quarter.eq(path.stem)]
            cutoff = pd.Period(path.stem, freq="Q").start_time.strftime("%Y%m%d")
            past = data.loc[data.race_date.lt(cutoff)]
            expected_hash = legacy._frame_digest(past[[
                "race_id", "race_date", *axis.NUMERIC, *axis.CATEGORICAL, "axis"]])
            if bundle["training_sha256"] != expected_hash or bundle["training_races"] != len(past):
                raise ValueError("pace-axis-training-frame-changed")
            rows = data.set_index("race_id").loc[selected.race_id].reset_index()
            values = predict_axis(bundle, rows, sha(model_path))
            np.testing.assert_allclose(selected.p_axis, values, rtol=0, atol=1e-12)
            checked_axis += len(values)
    print("Recalculating A/H2/H3 tickets, official money, and daily cash paths", flush=True)
    wide, trifecta = read(task["inputs"]["wide"]["path"]), read(task["inputs"]["trifecta"]["path"])
    ledger = pd.read_parquet(root / "decision_ledger.parquet")
    lookups = {name: dict(frame.groupby("race_id")) for name, frame in fields.items()}
    previous = pd.read_parquet(task["inputs"]["previous_decisions"]["path"])
    previous = previous.loc[previous.method.eq("candidate2")].set_index("race_id")
    original_features = pd.read_parquet(task["inputs"]["previous_features"]["path"])
    cohort = set(original_features.loc[original_features.numeric_priority, "race_id"])
    if ledger.duplicated(["model", "scope", "race_id"]).any():
        raise ValueError("pace-ledger-duplicate-race")
    for _, part in ledger.groupby(["model", "scope"]):
        if set(part.race_id) != cohort:
            raise ValueError("pace-roi-cohort-changed")
    checked_tickets = 0
    for row in ledger.itertuples(index=False):
        old_gate = bool(previous.loc[row.race_id, "cost_yen"] > 0)
        if row.model == "baseline" or row.scope == "same_old_gate":
            enabled = old_gate
        else:
            forecast = axis_forecasts[row.model]
            enabled = (row.race_id in forecast.index
                and axis.numeric_priority(lookups[row.model][row.race_id])
                and forecast.loc[row.race_id, "p_axis"] >= .65)
        if row.selected != enabled:
            raise ValueError("pace-independent-axis-and-selection-mismatch")
        dividend = {**wide[row.race_id], "三連単": trifecta[row.race_id]}
        expected = book_money(lookups[row.model][row.race_id], dividend, row.selected)
        for key in ("cost_yen", "return_yen", "hit_ticket_count"):
            if getattr(row, key) != expected[key]:
                raise ValueError(f"pace-independent-ticket-money-mismatch:{key}")
        keys = ("bet_types", "combinations", "stakes_yen", "official_payouts_per100_yen")
        if sorted(zip(*(getattr(row, key) for key in keys))) != sorted(
                zip(*(expected[key] for key in keys))):
            raise ValueError("pace-independent-ticket-book-mismatch")
        checked_tickets += len(expected["combinations"])
    for (name, scope), rows in ledger.groupby(["model", "scope"]):
        for period in ("2025_2026", "2025", "2026"):
            selected = rows.loc[rows.race_date.ge("20250101")] if period == "2025_2026" else rows.loc[
                rows.race_date.str.startswith(period)]
            summary = report["fixed_two"][f"{name}__{scope}"][period]
            cost, paid = int(selected.cost_yen.sum()), int(selected.return_yen.sum())
            daily = selected.groupby("race_date")[["cost_yen", "return_yen"]].sum()
            equity = PROTOCOL["bankroll_yen"] + (daily.return_yen - daily.cost_yen).cumsum()
            peak = np.maximum.accumulate(np.r_[PROTOCOL["bankroll_yen"], equity.to_numpy()])
            drawdown = int(np.max(peak - np.r_[PROTOCOL["bankroll_yen"], equity.to_numpy()]))
            if (summary["cost_yen"] != cost or summary["return_yen"] != paid
                    or summary["net_yen"] != paid - cost
                    or summary["daily_max_drawdown_yen"] != drawdown):
                raise ValueError("pace-independent-summary-mismatch")
            if cost and abs(summary["roi_percent"] - 100 * paid / cost) > 1e-9:
                raise ValueError("pace-independent-roi-mismatch")
            if selected.cost_yen.cumsum().max() > PROTOCOL["bankroll_yen"]:
                raise ValueError("pace-budget-reserve-exceeded")
    _task(root)
    result = {"verified": True, "rebuilt_style_runners": len(rebuilt),
        "verified_candidate_runner_scores": checked_scores, "verified_axis_forecasts": checked_axis,
        "verified_tickets": checked_tickets, "artifact_manifest_sha256": sha(root / "artifact-manifest.json"),
        "production_eligible": False, "live_effectiveness_confirmed": False}
    write(root / "verification.json", result)
    return result
