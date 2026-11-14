"""Reproduce the fixed opponent-pair experiment from hash-bound local inputs."""
from __future__ import annotations

import argparse
import hashlib
import importlib.metadata
import json
from pathlib import Path
import platform
import sys

import numpy as np
import pandas as pd
from threadpoolctl import threadpool_limits

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "python-api"))

from betting import research_opponent_pairs as core

SOURCES = {
    "horses": ("20261102-market-axis-full-test-v1", "cohort_predictions.parquet",
               "34c31e1e9c9bc443b770290e9f9d7b8b9cc2ecaa124d5174b45863ad30bcb447"),
    "wide_trio": ("20261102-market-axis-full-test-v1", "verified_payouts.json",
                  "d86e4c61485b103fd1865753f9aa1870506dc74dc5692f3dca95e9d594923790"),
    "trifecta": ("20261102-concentrated-million-v1", "verified_trifecta_payouts.json",
                 "b7a32c22485c333fbe7b52bd122943511fb318406d0175511aef132cfabbd4fc"),
    "priority": ("20261102-market-axis-priority-test-v1", "race_reference_decisions.parquet",
                 "dfb98d6216a4fe3aa2ea327c933d5a9fcec10c57fbeb4321012d7c7a1eba9a26"),
    "forecasts": ("20261102-adaptive-upset-v1", "forecast_ledger.parquet",
                  "ef54c738d630cbbb8552fede257839860d16dda5bb83aa5606d31125469e6f1e"),
    "gate": ("20261102-adaptive-simple-v1", "policy_decisions.parquet",
             "50d3b849a7abf0d877ce193040f124e4a0abf30864511cfc82a01d728d533add"),
    "old_books": ("20261102-adaptive-simple-v1", "selected_ticket_books.parquet",
                  "28c2fee46f0dcd320e00ad1ad21ce516344fb976e44a185fea5ab43dd3ebdfb8"),
}
BASELINE = "hole__r2_3__w2_t1_f2"


def sha(path: Path) -> str:
    with path.open("rb") as handle:
        return hashlib.file_digest(handle, "sha256").hexdigest()


def write_json(path: Path, value) -> None:
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False) + "\n", encoding="utf-8")


def _sources(root: Path) -> dict[str, Path]:
    result = {}
    for key, (folder, filename, expected) in SOURCES.items():
        path = root / folder / filename
        if sha(path) != expected:
            raise ValueError(f"source_hash_mismatch:{key}")
        result[key] = path
    return result


def _gate(inputs: dict) -> pd.DataFrame:
    decisions = pd.read_parquet(inputs["gate"])
    gate = decisions.loc[decisions.method.eq("ai_selected__axis_guard")].copy()
    priority = pd.read_parquet(inputs["priority"])
    priority = priority.loc[
        priority.scenario.eq("stored_win_market_hindsight") & priority.reference_priority
    ]
    expected_ids = set(priority.loc[priority.race_date.ge("20250101"), "race_id"])
    if len(priority) != 630 or len(gate) != 552 or set(gate.race_id) != expected_ids:
        raise ValueError("fixed_priority_cohort_mismatch")
    if gate.race_id.duplicated().any() or not (gate.fit_through < gate.race_date).all():
        raise ValueError("duplicate_race_or_noncausal_forecast")
    forecast = pd.read_parquet(inputs["forecasts"])
    forecast = forecast.loc[forecast.family.eq("ai_selected")].set_index("race_id")
    for row in gate.itertuples():
        other = forecast.loc[row.race_id]
        if (row.p_axis != other.p_axis or row.fit_through != other.fit_through
                or row.race_date != other.race_date or bool(row.cost_yen > 0) != bool(row.p_axis >= .65)):
            raise ValueError("fixed_gate_forecast_mismatch")
    if int(gate.cost_yen.gt(0).sum()) != 355:
        raise ValueError("fixed_gate_count_mismatch")
    return gate.sort_values(["race_date", "race_id"]).reset_index(drop=True)


def _signature(legs: dict | None) -> tuple:
    if not legs:
        return ()
    stake = core.stake_per_point(legs)
    return tuple((kind, tuple(ticket), stake) for kind in core.KINDS
                 for ticket in sorted(legs.get(kind, [])))


def _detailed(rows: pd.DataFrame, definitions: dict, method: str) -> dict:
    part = rows.loc[rows.method.eq(method)]
    return {
        "method": method, "definition": definitions[method],
        "all": core.summarize(part, bootstrap_samples=1000),
        "by_year": {year: core.summarize(part.loc[part.race_date.str.startswith(year)],
                                       bootstrap_samples=1000) for year in ("2025", "2026")},
    }


def evaluate(study_root: Path, output: Path) -> dict:
    if output.exists():
        raise FileExistsError("existing_output_will_not_be_overwritten")
    inputs = _sources(study_root)
    gate = _gate(inputs)
    definitions = core.registry()
    if len(definitions) != 1060:
        raise ValueError("frozen_method_grid_mismatch")
    horses = pd.read_parquet(inputs["horses"])
    horses = horses.loc[horses.race_id.isin(gate.race_id)]
    fields = {rid: part for rid, part in horses.groupby("race_id", sort=False)}
    wide_trio = json.loads(inputs["wide_trio"].read_text(encoding="utf-8"))
    trifecta = json.loads(inputs["trifecta"].read_text(encoding="utf-8"))
    amounts = np.zeros((len(definitions), len(gate), 4), dtype=np.int64)
    indices = np.zeros((len(definitions), len(gate)), dtype=np.int32)
    missing = np.zeros_like(indices, dtype=bool)
    books, race_orders = [], []
    for race_index, row in enumerate(gate.itertuples()):
        group = fields[row.race_id]
        if not group.num_horses.eq(len(group)).all() or not group.race_date.eq(row.race_date).all():
            raise ValueError("incomplete_or_misdated_field")
        field = group[["horse_number", "probability", "odds"]].to_dict("records")
        axis, ai, hole = core.opponent_orders(field)
        race_orders.append({
            "race_id": row.race_id, "race_date": row.race_date, "axis_number": axis,
            "ai_opponents": ai, "single_win_score_opponents": hole,
            "p_axis": row.p_axis, "fit_through": row.fit_through, "gate_passed": bool(row.cost_yen > 0),
        })
        dividends = {**wide_trio[row.race_id], "三連単": trifecta[row.race_id]}
        if any(kind not in dividends or not dividends[kind] for kind in core.KINDS):
            raise ValueError("all_official_markets_required_before_loss_settlement")
        local = {}
        for method_index, definition in enumerate(definitions.values()):
            legs = None
            if row.cost_yen > 0:
                legs = core.tickets_from_orders(axis, {"ai": ai, "hole": hole}, definition)
                missing[method_index, race_index] = legs is None
            signature = _signature(legs)
            if signature not in local:
                settled = core.settle(legs, dividends)
                index = len(books)
                books.append({
                    "book_id": index, "race_id": row.race_id, "race_date": row.race_date, **settled,
                })
                local[signature] = index
            index = local[signature]
            indices[method_index, race_index] = index
            amounts[method_index, race_index] = [
                books[index][key] for key in ("cost_yen", "return_yen", "ticket_count", "hit_ticket_count")
            ]
    methods = list(definitions)
    rows = pd.DataFrame({
        "method": np.repeat(methods, len(gate)),
        "race_id": np.tile(gate.race_id.to_numpy(), len(methods)),
        "race_date": np.tile(gate.race_date.to_numpy(), len(methods)),
        "book_id": indices.ravel(), "missing_used_rank_skip": missing.ravel(),
        **{key: amounts[:, :, i].ravel()
           for i, key in enumerate(("cost_yen", "return_yen", "ticket_count", "hit_ticket_count"))},
    })
    signatures = {method: hashlib.sha256(indices[i].astype("<i4").tobytes()).hexdigest()
                  for i, method in enumerate(methods)}
    equivalents = {}
    for method, signature in signatures.items():
        equivalents.setdefault(signature, []).append(method)
    canonicals = {signature: min(ids, key=lambda x: (definitions[x]["nominal_points"], x))
                  for signature, ids in equivalents.items()}
    aggregates = []
    for method, part in rows.groupby("method", sort=False):
        bought = part.loc[part.cost_yen.gt(0)]
        aggregate = {
            "method": method, "ranking": definitions[method]["ranking"],
            "nominal_points": definitions[method]["nominal_points"],
            "book_signature": signatures[method], "canonical_method": canonicals[signatures[method]],
            "bet_dates": bought.race_date.nunique(), "missing_used_rank_skips": int(part.missing_used_rank_skip.sum()),
            **core.summarize(part),
        }
        for year in ("2025", "2026"):
            annual = core.summarize(part.loc[part.race_date.str.startswith(year)])
            for key in ("bet_races", "hit_races", "cost_yen", "return_yen", "net_yen", "gross_return_rate_percent"):
                aggregate[f"{year}_{key}"] = annual[key]
        aggregates.append(aggregate)
    summary = pd.DataFrame(aggregates)
    unique = summary.loc[summary.method.eq(summary.canonical_method)].sort_values(
        ["gross_return_rate_percent", "nominal_points", "method"], ascending=[False, True, True],
    )
    supported = unique.loc[unique.hit_races.ge(20) & unique.bet_races.ge(150) & unique.bet_dates.ge(20)]
    old = rows.loc[rows.method.eq(BASELINE)].set_index("race_id")
    if (int(old.cost_yen.sum()), int(old.return_yen.sum())) != (426000, 679710):
        raise ValueError("historical_baseline_money_mismatch")
    golden = pd.read_parquet(inputs["old_books"])
    golden = golden.loc[golden.method.eq("ai_selected__axis_guard")]
    if len(golden) != 552 or set(golden.race_id) != set(gate.race_id):
        raise ValueError("historical_baseline_book_cohort_mismatch")
    book_keys = ("bet_types", "combinations", "stakes_yen", "official_payouts_per100_yen")
    for expected in golden.itertuples():
        actual = books[int(old.loc[expected.race_id, "book_id"])]
        expected_tickets = sorted(zip(*(getattr(expected, key) for key in book_keys)))
        actual_tickets = sorted(zip(*(actual[key] for key in book_keys)))
        if expected_tickets != actual_tickets:
            raise ValueError(f"historical_baseline_book_mismatch:{expected.race_id}")
    training = rows.loc[rows.race_date.lt("20260101")]
    selectors = {
        "mean_roi_2025": core.choose_past_recipe(training, "20260101"),
        "lower10_roi_2025": core.choose_past_recipe(training, "20260101", conservative=True),
    }
    fixed_controls = [
        BASELINE, "hole__r1_2__w1_t1_f2", "hole__r1_2__w3_t1_f2",
        "ai__r1_2__w1_t1_f2", "ai__r1_2__w3_t1_f2",
        "hole__r1_2__w3_t0_f0", "ai__r1_2__w3_t0_f0",
    ]
    detailed_ids = set(unique.head(10).method) | set(supported.head(10).method) | set(fixed_controls)
    detailed_ids.update(item["method"] for item in selectors.values() if item["method"] is not None)
    details = {method: _detailed(rows, definitions, method) for method in sorted(detailed_ids)}
    selection_reports = {}
    for name, result in selectors.items():
        method = result["method"]
        if method is None:
            future = gate.loc[gate.race_date.ge("20260101"), ["race_id", "race_date"]].copy()
            for key in ("cost_yen", "return_yen", "ticket_count", "hit_ticket_count"):
                future[key] = 0
        else:
            future = rows.loc[rows.method.eq(method) & rows.race_date.ge("20260101")]
        selection_reports[name] = {
            "selected_method": method, "selection_score_percent": result["score_percent"],
            "support_thresholds": {"bet_races": 150, "bet_dates": 20, "hit_races": 10},
            "selection_2025": details[method]["by_year"]["2025"] if method is not None else None,
            "fixed_2026": core.summarize(future, bootstrap_samples=1000),
        }
    protocol = {
        "schema": core.SCHEMA, "reference_only": True, "purchase_eligible": False,
        "base_horse_model_sha256": "2f193b42e0405f0bb5fe2c0450939373c8ed0531dbf06772745efe93bcf61d99",
        "sources": {key: {"study": folder, "file": filename, "sha256": digest}
                    for key, (folder, filename, digest) in SOURCES.items()},
        "implementation_sha256": sha(Path(core.__file__)), "runner_sha256": sha(Path(__file__)),
        "cohort": "Original priority630, excluding2024 warmup78;552 remain; existing355 gate passes fixed.",
        "pool": "Up to5 AI-ranked horses excluding the favourite; refill to AI6 when possible.",
        "rankings": ["AI probability", "AI probability times single-win odds; NOT joint ticket EV"],
        "tie_breaker": "horse_number ascending", "axis_probability_gate": .65,
        "templates": "Every rank pair; wide0/first/second/both; trio0/1; every subset of3 axis positions.",
        "point_range": [1, 6], "method_count": 1060, "max_race_yen": 1500,
        "stake_rule": "100*floor(1500/(100*points)); no compounding; missing USED rank skips whole recipe.",
        "initial_budget_yen": core.INITIAL_BUDGET, "budget_exhaustion": "No borrowing; all total stakes must fit1m.",
        "selection": "2025 only; mean ROI or date bootstrap10th percentile; score<=100 means skip.",
        "selection_bootstrap_samples": 500, "descriptive_bootstrap_samples": 1000, "seed": core.SEED,
        "pooled_supported_min_hit_races": 20, "pooled_supported_min_bet_races": 150,
        "pooled_supported_min_bet_dates": 20,
        "deduplication": "Full-cohort staked-book equivalence is descriptive only; no future data in2025 selection.",
        "classification_refit": False, "scraping": False, "live_policy_changed": False,
        "limitations": [
            "Stored win odds, not assured pre-start odds; baseline horse probabilities not EV-approved.",
            "Same previously inspected TEST data;2025-to2026 is pseudo-out-of-time, not a fresh holdout.",
            "1060 configurations; best pooled recipe is hindsight and intervals are not multiple-test corrected.",
            "Unit stakes differ from the earlier all300yen quick comparison except4and5point recipes.",
            "No ticket-specific pre-start odds or joint-probability EV optimization.",
        ],
        "environment": {"python": platform.python_version(),
                        **{name: importlib.metadata.version(name)
                           for name in ("numpy", "pandas", "pyarrow", "threadpoolctl")}},
    }
    report = {
        "schema": core.SCHEMA, "reference_only": True, "purchase_eligible": False,
        "cohort_races": len(gate), "fixed_gate_passes": int(gate.cost_yen.gt(0).sum()),
        "raw_settings": len(methods), "distinct_full_cohort_staked_books": len(unique),
        "hindsight_top": list(unique.head(10).method),
        "supported_hindsight_top": list(supported.head(10).method),
        "fixed_controls": fixed_controls, "frozen_selections": selection_reports, "details": details,
        "baseline_money_and_all552_books_match": True,
    }
    output.mkdir(parents=True, exist_ok=False)
    write_json(output / "protocol.json", protocol)
    write_json(output / "method_definitions.json", definitions)
    pd.DataFrame(race_orders).to_parquet(output / "race_orders.parquet", index=False)
    pd.DataFrame(books).to_parquet(output / "ticket_books.parquet", index=False)
    rows.to_parquet(output / "race_results.parquet", index=False)
    summary.to_csv(output / "summary.csv", index=False, encoding="utf-8-sig")
    write_json(output / "report.json", report)
    for name, result in selectors.items():
        pd.DataFrame(result["candidates"]).to_csv(output / f"selection_{name}.csv", index=False, encoding="utf-8-sig")
    write_json(output / "artifact-manifest.json", {
        "schema": core.SCHEMA, "files": {p.name: sha(p) for p in sorted(output.iterdir()) if p.is_file()},
    })
    return report


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--study-root", type=Path, default=REPO / "reports" / "generated" / "race-selection")
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    with threadpool_limits(limits=1):
        report = evaluate(args.study_root, args.output)
    print(json.dumps({key: report[key] for key in
                      ("cohort_races", "fixed_gate_passes", "raw_settings", "distinct_full_cohort_staked_books",
                       "hindsight_top", "supported_hindsight_top", "frozen_selections")},
                     ensure_ascii=False, indent=2, allow_nan=False))


if __name__ == "__main__":
    main()
