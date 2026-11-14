"""Independently verify pair books, official settlements and past-only selection."""
from __future__ import annotations

import argparse
import hashlib
from itertools import permutations
import json
from pathlib import Path

import numpy as np
import pandas as pd
from threadpoolctl import threadpool_limits


def sha(path):
    with path.open("rb") as handle:
        return hashlib.file_digest(handle, "sha256").hexdigest()


def expected_book(field, definition, enabled):
    if not enabled:
        return frozenset(), False
    axis, orders = field
    order = orders[definition["ranking"]]
    first, second = definition["pair"]
    wide, trio, positions = definition["wide_mask"], definition["trio"], definition["axis_positions"]
    needed = set()
    if wide & 1:
        needed.add(first)
    if wide & 2:
        needed.add(second)
    if trio or positions:
        needed.update((first, second))
    if any(rank > len(order) for rank in needed):
        return frozenset(), True
    tickets = []
    for bit, rank in ((1, first), (2, second)):
        if wide & bit:
            tickets.append(("ワイド", "-".join(map(str, sorted([axis, order[rank - 1]])))))
    if trio:
        tickets.append(("三連複", "-".join(map(str, sorted([axis, order[first - 1], order[second - 1]])))))
    if positions:
        for ticket in permutations([axis, order[first - 1], order[second - 1]]):
            if ticket.index(axis) + 1 in positions:
                tickets.append(("三連単", "-".join(map(str, ticket))))
    assert 1 <= len(tickets) <= 6
    stake = 100 * (1500 // (100 * len(tickets)))
    return frozenset((kind, key, stake) for kind, key in tickets), False


def verify(study_root: Path, output: Path) -> dict:
    protocol = json.loads((output / "protocol.json").read_text(encoding="utf-8"))
    report = json.loads((output / "report.json").read_text(encoding="utf-8"))
    manifest_path = output / "artifact-manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    for name, digest in manifest["files"].items():
        assert sha(output / name) == digest, name
    repo = Path(__file__).resolve().parents[1]
    assert sha(repo / "python-api" / "betting" / "research_opponent_pairs.py") == protocol["implementation_sha256"]
    assert sha(repo / "scripts" / "evaluate_market_axis_pairs.py") == protocol["runner_sha256"]
    source = {}
    for name, entry in protocol["sources"].items():
        path = study_root / entry["study"] / entry["file"]
        assert sha(path) == entry["sha256"], name
        source[name] = path
    ledger = pd.read_parquet(output / "race_results.parquet")
    books = pd.read_parquet(output / "ticket_books.parquet")
    definitions = json.loads((output / "method_definitions.json").read_text(encoding="utf-8"))
    assert len(definitions) == 1060 and len(ledger) == 552 * 1060
    assert not ledger.duplicated(["race_id", "method"]).any()
    assert set(ledger.method) == set(definitions)
    gate = pd.read_parquet(source["gate"])
    gate = gate.loc[gate.method.eq("ai_selected__axis_guard")].set_index("race_id")
    assert len(gate) == 552 and gate.cost_yen.gt(0).sum() == 355
    assert (gate.fit_through < gate.race_date).all()
    horses = pd.read_parquet(source["horses"])
    horses = horses.loc[horses.race_id.isin(gate.index)]
    fields = {}
    for rid, part in horses.groupby("race_id"):
        assert part.horse_number.nunique() == len(part) and part.num_horses.eq(len(part)).all()
        assert abs(part.probability.sum() - 1) < 1e-8 and part.odds.ge(1).all()
        axis = int(part.sort_values(["odds", "horse_number"]).iloc[0].horse_number)
        ai = part.sort_values(["probability", "horse_number"], ascending=[False, True])
        ai = ai.loc[ai.horse_number.ne(axis)].head(5)
        hole = ai.assign(single_win_score=ai.probability * ai.odds).sort_values(
            ["single_win_score", "horse_number"], ascending=[False, True],
        )
        fields[rid] = (axis, {"ai": ai.horse_number.astype(int).tolist(),
                             "hole": hole.horse_number.astype(int).tolist()})
    wide_trio = json.loads(source["wide_trio"].read_text(encoding="utf-8"))
    trifecta = json.loads(source["trifecta"].read_text(encoding="utf-8"))
    actual_books, ticket_count = {}, 0
    assert books.book_id.is_unique
    for book in books.itertuples():
        size = len(book.combinations)
        assert all(len(getattr(book, key)) == size for key in
                   ("bet_types", "stakes_yen", "official_payouts_per100_yen"))
        assert size == book.ticket_count and 0 <= size <= 6
        expected_stake = 100 * (1500 // (100 * size)) if size else 0
        values = {**wide_trio[book.race_id], "三連単": trifecta[book.race_id]}
        cost, paid, hits = 0, 0, 0
        signature = set()
        for kind, key, stake, original_payout in zip(
                book.bet_types, book.combinations, book.stakes_yen, book.official_payouts_per100_yen):
            numbers = list(map(int, key.split("-")))
            assert kind in ("ワイド", "三連複", "三連単")
            assert len(numbers) == (2 if kind == "ワイド" else 3) and len(set(numbers)) == len(numbers)
            assert fields[book.race_id][0] in numbers
            if kind != "三連単":
                assert numbers == sorted(numbers)
            assert stake == expected_stake and stake % 100 == 0
            dividend = values[kind].get(key, 0)
            assert dividend == original_payout
            cost += int(stake)
            paid += int(stake) // 100 * int(dividend)
            hits += int(dividend > 0)
            signature.add((kind, key, int(stake)))
        assert len(signature) == size and cost <= 1500
        assert (cost, paid, hits) == (book.cost_yen, book.return_yen, book.hit_ticket_count)
        actual_books[int(book.book_id)] = frozenset(signature)
        ticket_count += size
    indexed = books.set_index("book_id")
    selected = indexed.loc[ledger.book_id]
    for key in ("race_id", "race_date", "ticket_count", "cost_yen", "return_yen", "hit_ticket_count"):
        assert (selected[key].to_numpy() == ledger[key].to_numpy()).all(), key
    money = pd.read_csv(output / "summary.csv").set_index("method")
    cohort_signatures, candidates = set(), []
    for method, part in ledger.groupby("method", sort=True):
        assert len(part) == 552 and set(part.race_id) == set(gate.index)
        definition = definitions[method]
        for result in part.itertuples():
            assert result.race_date == gate.loc[result.race_id, "race_date"]
            expected, missing = expected_book(fields[result.race_id], definition,
                                               gate.loc[result.race_id, "cost_yen"] > 0)
            assert expected == actual_books[int(result.book_id)], (method, result.race_id)
            assert bool(result.missing_used_rank_skip) == missing
        ordered = part.sort_values(["race_date", "race_id"])
        cohort_signatures.add(tuple(ordered.book_id))
        total_cost, total_paid = int(part.cost_yen.sum()), int(part.return_yen.sum())
        assert total_cost <= 1000000
        assert money.loc[method, "cost_yen"] == total_cost
        assert money.loc[method, "return_yen"] == total_paid
        assert money.loc[method, "net_yen"] == total_paid - total_cost
        assert abs(money.loc[method, "gross_return_rate_percent"] - 100 * total_paid / total_cost) < 1e-9
        for year in ("2025", "2026"):
            year_rows = part.loc[part.race_date.str.startswith(year)]
            assert money.loc[method, f"{year}_cost_yen"] == year_rows.cost_yen.sum()
            assert money.loc[method, f"{year}_return_yen"] == year_rows.return_yen.sum()
        train = part.loc[part.race_date.lt("20260101")]
        bought = train.loc[train.cost_yen.gt(0)]
        if len(bought) >= 150 and bought.race_date.nunique() >= 20 and bought.hit_ticket_count.gt(0).sum() >= 10:
            candidates.append({"method": method, "points": definition["nominal_points"],
                               "roi": float(100 * train.return_yen.sum() / train.cost_yen.sum())})
    assert len(cohort_signatures) == report["distinct_full_cohort_staked_books"]
    candidate_ids = [row["method"] for row in candidates]
    historical = ledger.loc[ledger.race_date.lt("20260101") & ledger.method.isin(candidate_ids)]
    daily = historical.groupby(["race_date", "method"])[["cost_yen", "return_yen"]].sum()
    cost = daily.cost_yen.unstack().reindex(columns=candidate_ids).sort_index()
    paid = daily.return_yen.unstack().reindex(index=cost.index, columns=candidate_ids)
    weights = np.random.default_rng(20261103).multinomial(
        len(cost), np.full(len(cost), 1 / len(cost)), size=500,
    )
    sampled_cost, sampled_paid = weights @ cost.to_numpy(), weights @ paid.to_numpy()
    ratios = np.divide(100 * sampled_paid, sampled_cost, out=np.full_like(sampled_paid, np.nan, dtype=float),
                       where=sampled_cost > 0)
    for entry, lower in zip(candidates, np.nanpercentile(ratios, 10, axis=0)):
        entry["lower"] = float(lower)
    for label, score_key in (("mean_roi_2025", "roi"), ("lower10_roi_2025", "lower")):
        winner = min(candidates, key=lambda x: (-x[score_key], x["points"], x["method"]))
        expected_method = winner["method"] if winner[score_key] > 100 else None
        reported = report["frozen_selections"][label]
        assert reported["selected_method"] == expected_method
        assert abs(reported["selection_score_percent"] - winner[score_key]) < 1e-9
        future = ledger.loc[ledger.method.eq(expected_method) & ledger.race_date.ge("20260101")]
        expected_cost = int(future.cost_yen.sum()) if expected_method is not None else 0
        expected_paid = int(future.return_yen.sum()) if expected_method is not None else 0
        assert reported["fixed_2026"]["cost_yen"] == expected_cost
        assert reported["fixed_2026"]["return_yen"] == expected_paid
        assert reported["fixed_2026"]["ending_cash_yen"] == 1000000 - expected_cost + expected_paid
    for method, detail in report["details"].items():
        part = ledger.loc[ledger.method.eq(method)]
        for label, subset in [("all", part)] + [
                (year, part.loc[part.race_date.str.startswith(year)]) for year in ("2025", "2026")]:
            expected = detail["all"] if label == "all" else detail["by_year"][label]
            assert expected["cohort_races"] == len(subset)
            assert expected["bet_races"] == subset.cost_yen.gt(0).sum()
            assert expected["hit_races"] == subset.hit_ticket_count.gt(0).sum()
            denominator = int(subset.cost_yen.sum())
            total = int(subset.return_yen.sum())
            assert expected["cost_yen"] == denominator and expected["return_yen"] == total
            assert abs(expected["highest_payout_zero_roi_percent"]
                       - 100 * (total - int(subset.return_yen.max())) / denominator) < 1e-9
    return {
        "schema": protocol["schema"], "verified": True, "cohort_races": 552, "fixed_gate_passes": 355,
        "verified_method_race_links": len(ledger), "verified_unique_race_books": len(books),
        "verified_tickets_in_unique_books": ticket_count,
        "distinct_full_cohort_staked_books": len(cohort_signatures),
        "checks": ["artifact_and_source_hashes", "independent_AI_and_single_win_score_rankings",
                   "all_recipe_books_and_used_rank_skips", "ordered_official_dividends_and_stakes",
                   "all_cohort_rows_and_money", "independent_2025_only_mean_and_lower10_selection",
                   "fixed_2026_cash", "detailed_annual_hit_and_big_payout_sensitivity"],
        "verified_manifest_sha256": sha(manifest_path), "verifier_sha256": sha(Path(__file__)),
    }


def main():
    repo = Path(__file__).resolve().parents[1]
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--study-root", type=Path, default=repo / "reports" / "generated" / "race-selection")
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if not __debug__:
        raise RuntimeError("verification_requires_python_assertions_enabled")
    with threadpool_limits(limits=1):
        result = verify(args.study_root, args.output)
    target = args.output / "verification.json"
    serialized = json.dumps(result, indent=2, allow_nan=False) + "\n"
    if target.exists() and target.read_text(encoding="utf-8") != serialized:
        raise FileExistsError("different_verification_evidence_already_exists")
    target.write_text(serialized, encoding="utf-8")
    print(serialized)


if __name__ == "__main__":
    main()
