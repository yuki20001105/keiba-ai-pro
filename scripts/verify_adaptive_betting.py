"""Independent arithmetic, timing, event-label and official-payout audit."""
from __future__ import annotations

import argparse
import hashlib
from itertools import permutations
import json
from pathlib import Path

import numpy as np
import pandas as pd
from threadpoolctl import threadpool_limits


def read(path):
    return json.loads(path.read_text(encoding="utf-8"))


def sha(path):
    with path.open("rb") as handle:
        return hashlib.file_digest(handle, "sha256").hexdigest()


def verify(study_root, output):
    manifest_path = output / "artifact-manifest.json"
    manifest, protocol, report = read(manifest_path), read(output / "protocol.json"), read(output / "report.json")
    for name, digest in manifest["files"].items():
        assert sha(output / name) == digest
    repo = Path(__file__).resolve().parents[1]
    assert sha(repo / "python-api" / "betting" / "adaptive_strategy.py") == protocol["feature_source_sha256"]
    assert sha(repo / "scripts" / "reproduce_adaptive_betting.py") == protocol["runner_sha256"]
    assert sha(repo / "python-api" / "betting" / "research_opponent_pairs.py") == protocol["ticket_source_sha256"]
    sources = {}
    for name, entry in protocol["sources"].items():
        path = study_root / entry["study"] / entry["file"]
        assert sha(path) == entry["sha256"]
        sources[name] = path
    raw = pd.read_parquet(sources["horses"])
    wide_trio, trifecta = read(sources["wide_trio"]), read(sources["trifecta"])
    data = pd.read_parquet(output / "features_and_targets.parquet").set_index("race_id")
    forecast = pd.read_parquet(output / "forecasts.parquet").set_index("race_id")
    ledger = pd.read_parquet(output / "decision_ledger.parquet")
    history = pd.read_parquet(output / "policy_calibration_2025.parquet")
    assert len(data) == 6458 and len(forecast) == 5473 and len(ledger) == 3150 and len(history) == 205
    assert history.race_date.max() < "20260101"
    assert not ledger.duplicated(["race_id", "method"]).any()
    assert (forecast.fit_through < forecast.race_date).all()
    for path in [output / "latest_model.json", *sorted((output / "models").glob("*.json")),
                 output / "policy_mean.json", output / "policy_lower10.json"]:
        value = read(path)
        body = {key: item for key, item in value.items() if key != "checksum"}
        digest = hashlib.sha256(json.dumps(body, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()).hexdigest()
        assert value["checksum"] == digest
        assert value["base_model_sha256"] == protocol["base_model_sha256"]
        assert value["feature_source_sha256"] == protocol["feature_source_sha256"]
    orders = {}
    for rid, part in raw.groupby("race_id", sort=True):
        source = part.set_index("horse_number")
        market = part.sort_values(["odds", "horse_number"]).horse_number.astype(int).tolist()
        axis = market[0]
        ai = part.sort_values(["probability", "horse_number"], ascending=[False, True])
        pool = ai.loc[ai.horse_number.ne(axis)].head(5)
        hole = pool.assign(value=pool.probability * pool.odds).sort_values(
            ["value", "horse_number"], ascending=[False, True],
        ).horse_number.astype(int).tolist()
        b = pool.horse_number.astype(int).tolist()
        orders[rid] = (axis, b, hole)
        row = data.loc[rid]
        assert part.num_horses.eq(len(part)).all()
        for prefix, number in (("b1", b[0]), ("b4", b[3]), ("h2", hole[1]), ("h3", hole[2])):
            expected = {
                "probability": source.loc[number, "probability"], "odds": source.loc[number, "odds"],
                "market_share": (1 / source.loc[number, "odds"]) / (1 / part.odds).sum(),
                "market_rank_fraction": (market.index(number) + 1) / len(market),
            }
            for name, value in expected.items():
                assert abs(row[f"{prefix}_{name}"] - value) < 1e-12
        trios = {tuple(map(int, key.split("-"))) for key in wide_trio[rid]["三連複"]}
        total = min(sum(market.index(n) + 1 for n in group) for group in trios)
        assert row.rank_sum == total and row.upset == (0 if total <= 10 else 1 if total <= 17 else 2)
        ordered = {ticket for group in trios for ticket in permutations(group)
                   if [source.loc[n, "finish"] for n in ticket] == sorted(source.loc[n, "finish"] for n in ticket)}
        if rid in trifecta:
            assert ordered == {tuple(map(int, key.split("-"))) for key in trifecta[rid]}
        assert row.axis == int(any(axis in group for group in trios))
        assert row.axis_win == int(source.loc[axis, "finish"] == 1)
        assert row.f1 == int((axis, b[0], b[3]) in ordered or (axis, b[3], b[0]) in ordered)
        assert row.f2 == int((hole[1], axis, hole[2]) in ordered or (hole[2], axis, hole[1]) in ordered)
        assert row.wide == int("-".join(map(str, sorted((axis, hole[2])))) in wide_trio[rid]["ワイド"])
        assert row.trio == int(tuple(sorted((axis, hole[1], hole[2]))) in trios)
    for row in history.itertuples():
        axis, b, h = orders[row.race_id]
        pay = trifecta[row.race_id]
        key = lambda t: "-".join(map(str, t))
        assert row.payout_f1 == pay.get(key((axis, b[0], b[3])), 0) + pay.get(key((axis, b[3], b[0])), 0)
        assert row.payout_f2 == pay.get(key((h[1], axis, h[2])), 0) + pay.get(key((h[2], axis, h[1])), 0)
        assert row.payout_wide == wide_trio[row.race_id]["ワイド"].get(key(sorted((axis, h[2]))), 0)
        assert row.payout_trio == wide_trio[row.race_id]["三連複"].get(key(sorted((axis, h[1], h[2]))), 0)
        assert row.forecast_class == int(np.argmax(forecast.loc[row.race_id, ["p0", "p1", "p2"]].to_numpy()))
    blocked = {"finish", "upset", "axis", "axis_win", "f1", "wide", "trio", "f2", "rank_sum", "return_yen"}
    model_predictions = 0
    for entry in read(output / "folds.json"):
        quarter = entry["quarter"]
        bundle = read(output / "models" / f"{quarter}.json")
        assert bundle["fitted_through"] < entry["cutoff"]
        training = data.loc[data.race_date.lt(entry["cutoff"])]
        assert len(training) == bundle["training_races"]
        assert training.race_date.max() == bundle["fitted_through"]
        target = forecast.loc[forecast.quarter.eq(quarter)]
        frame = data.loc[target.index]
        for name, model in bundle["models"].items():
            assert not blocked.intersection(model["numeric"])
            if "constant_probability" in model:
                predicted = np.full((len(frame), 1), model["constant_probability"])
            else:
                matrix = (frame[model["numeric"]].to_numpy() - model["mean"]) / model["scale"]
                for category_name, labels in zip(("surface", "venue", "field_condition", "race_class"),
                                                 model["categories"]):
                    matrix = np.column_stack([matrix, *[
                        frame[category_name].eq(label).to_numpy() for label in labels]])
                logits = matrix.dot(np.asarray(model["coefficient"]).T) + model["intercept"]
                if name == "upset":
                    exp = np.exp(logits - logits.max(axis=1)[:, None])
                    predicted = exp / exp.sum(axis=1)[:, None]
                else:
                    predicted = 1 / (1 + np.exp(-np.clip(logits, -700, 700)))
            columns = ["p0", "p1", "p2"] if name == "upset" else [f"p_{name}"]
            assert np.max(np.abs(predicted - target[columns].to_numpy())) < 1e-12
            model_predictions += len(frame)
    policy_checks = 0
    for mode in ("mean", "lower10"):
        policy = read(output / f"policy_{mode}.json")
        assert policy["fitted_through"] == history.race_date.max()
        for state in range(3):
            context = history.loc[history.forecast_class.eq(state)]
            enough = len(context) >= 40 and context.race_date.nunique() >= 12
            for component in ("f1", "wide", "trio", "f2"):
                name = f"payout_{component}"
                sample = context if enough and context[name].gt(0).sum() >= 3 else history
                hits = sample[name].gt(0).sum()
                if hits < 3:
                    expected = 0
                elif mode == "mean":
                    expected = sample.loc[sample[name].gt(0), name].mean()
                else:
                    daily = sample.assign(hit=sample[name].gt(0).astype(int)).groupby("race_date")[[name, "hit"]].sum()
                    weights = np.random.default_rng(20261103).multinomial(
                        len(daily), np.full(len(daily), 1 / len(daily)), size=500)
                    denominator = weights @ daily.hit.to_numpy()
                    means = np.divide(weights @ daily[name].to_numpy(), denominator,
                                      out=np.zeros(len(weights), dtype=float), where=denominator > 0)
                    expected = np.percentile(means, 10)
                assert abs(policy["states"][str(state)][component]["value"] - expected) < 1e-10
                policy_checks += 1
    old_gate = pd.read_parquet(sources["gate"])
    old_gate = old_gate.loc[old_gate.method.eq("ai_selected__axis_guard")].set_index("race_id")
    verified_tickets = 0
    for row in ledger.itertuples():
        enabled = row.race_id in old_gate.index and old_gate.loc[row.race_id, "cost_yen"] > 0
        if row.method.startswith("ai_"):
            if row.race_date < "20260101" or not enabled:
                action = "skip"
            else:
                f = forecast.loc[row.race_id]
                state = str(int(np.argmax([f.p0, f.p1, f.p2])))
                policy = read(output / f"policy_{row.method[3:]}.json")
                values = {key: f[f"p_{key}"] * item["value"] for key, item in policy["states"][state].items()}
                scores = {"candidate1": values["f1"] / 200,
                          "candidate2": (values["wide"] + values["f2"]) / 300,
                          "candidate3": (values["wide"] + values["f2"] + values["trio"]) / 400}
                best = min(scores, key=lambda x: (-scores[x], {"candidate1": 2, "candidate2": 3, "candidate3": 4}[x], x))
                action = best if scores[best] > 1.10 else "skip"
            assert row.selected_action == action
        else:
            action = row.method if enabled else "skip"
            assert row.selected_action == action
        axis, b, h = orders[row.race_id]
        tickets = []
        if action == "candidate1":
            tickets = [("三連単", (axis, b[0], b[3]), 700), ("三連単", (axis, b[3], b[0]), 700)]
        elif action in ("candidate2", "candidate3"):
            stake = 500 if action == "candidate2" else 300
            tickets = [("ワイド", tuple(sorted((axis, h[2]))), stake),
                       ("三連単", (h[1], axis, h[2]), stake), ("三連単", (h[2], axis, h[1]), stake)]
            if action == "candidate3":
                tickets.append(("三連複", tuple(sorted((axis, h[1], h[2]))), stake))
        expected = {(kind, "-".join(map(str, key)), stake) for kind, key, stake in tickets}
        actual = set(zip(row.bet_types, row.combinations, map(int, row.stakes_yen)))
        assert expected == actual and len(expected) == row.ticket_count
        paymaps = {**wide_trio[row.race_id], "三連単": trifecta[row.race_id]}
        dividends = [paymaps[kind].get(key, 0) for kind, key in zip(row.bet_types, row.combinations)]
        assert dividends == list(row.official_payouts_per100_yen)
        assert sum(row.stakes_yen) == row.cost_yen <= 1500
        assert sum(int(s) // 100 * d for s, d in zip(row.stakes_yen, dividends)) == row.return_yen
        assert sum(d > 0 for d in dividends) == row.hit_ticket_count
        verified_tickets += row.ticket_count
    for method, part in ledger.loc[ledger.race_date.ge("20260101")].groupby("method"):
        stats = report["comparisons_2026"][method]
        assert len(part) == 236 and stats["cost_yen"] == part.cost_yen.sum()
        assert stats["return_yen"] == part.return_yen.sum()
        assert stats["profitable_races"] == part.return_yen.gt(part.cost_yen).sum()
        assert stats["net_yen"] == part.return_yen.sum() - part.cost_yen.sum()
    assert report["adoption_verdict"] == "not_ready_for_live"
    assert all(value["live_effectiveness_confirmed"] is False for value in report["effectiveness"].values())
    return {
        "verified": True, "race_feature_and_event_labels": len(data),
        "verified_model_row_predictions": model_predictions, "verified_policy_components": policy_checks,
        "verified_decisions": len(ledger), "verified_tickets": int(verified_tickets),
        "no_production_activation": True, "manifest_sha256": sha(manifest_path),
        "verifier_sha256": sha(Path(__file__)),
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--study-root", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if not __debug__:
        raise RuntimeError("verification_requires_assertions")
    with threadpool_limits(limits=1):
        result = verify(args.study_root, args.output)
    serialized = json.dumps(result, indent=2, allow_nan=False) + "\n"
    target = args.output / "verification.json"
    if target.exists() and target.read_text(encoding="utf-8") != serialized:
        raise FileExistsError("different_verification_already_exists")
    target.write_text(serialized, encoding="utf-8")
    print(serialized)


if __name__ == "__main__":
    main()
