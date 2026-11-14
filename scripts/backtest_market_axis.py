"""Compare frozen market-axis wide/trio policies with actual official dividends."""
from __future__ import annotations

import argparse
from collections import Counter, defaultdict
import json
from pathlib import Path
import re
import sqlite3
import sys
from urllib.parse import parse_qs, urlsplit

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "python-api"))

from betting.research_market_axis import (
    METHODS, capture_odds, select_tickets, settle_tickets, verify_dividends,
)
from experiments.common import active_pointer_hash, file_sha256
from forward_runtime.provider import parse_odds
from forward_runtime.schedule import aware, canonical, sha256


def summarize(rows: pd.DataFrame) -> dict:
    cost, returned = int(rows.cost_yen.sum()), int(rows.return_yen.sum())
    bets = rows.loc[rows.cost_yen.gt(0)]
    hits = int(bets.hit_ticket_count.gt(0).sum())
    daily = rows.groupby("race_date", sort=True)[["cost_yen", "return_yen"]].sum()
    balance = (daily.return_yen - daily.cost_yen).cumsum().to_numpy()
    peaks = np.maximum.accumulate(np.r_[0, balance])[1:]
    drawdown = int((peaks - balance).max()) if len(balance) else 0
    streak = longest = 0
    for losing in (daily.loc[daily.cost_yen.gt(0), "return_yen"] - daily.loc[daily.cost_yen.gt(0), "cost_yen"]).lt(0):
        streak = streak + 1 if losing else 0
        longest = max(longest, streak)
    # All arms with the same date cohort receive the same resampled date weights.
    weights = np.random.default_rng(20261102).multinomial(
        len(daily), np.full(len(daily), 1 / len(daily)), size=1000,
    )
    costs = weights @ daily.cost_yen.to_numpy()
    returns = weights @ daily.return_yen.to_numpy()
    valid = costs > 0
    ci = np.percentile(100 * returns[valid] / costs[valid], [2.5, 97.5]).tolist() if valid.any() else [None, None]
    return {
        "cohort_races": len(rows), "bet_races": len(bets), "hit_races": hits,
        "tickets": int(rows.ticket_count.sum()), "hit_tickets": int(rows.hit_ticket_count.sum()),
        "min_points": int(bets.ticket_count.min()) if len(bets) else 0,
        "max_points": int(bets.ticket_count.max()) if len(bets) else 0,
        "average_points_per_bet_race": float(bets.ticket_count.mean()) if len(bets) else None,
        "cost_yen": cost, "return_yen": returned, "net_yen": returned - cost,
        "gross_return_rate_percent": 100 * returned / cost if cost else None,
        "hit_race_rate_percent": 100 * hits / len(bets) if len(bets) else None,
        "profitable_race_rate_percent": 100 * bets.return_yen.gt(bets.cost_yen).mean() if len(bets) else None,
        "daily_max_drawdown_yen": drawdown, "max_consecutive_losing_bet_days": longest,
        "top1_payout_race_share_percent": 100 * rows.return_yen.max() / returned if returned else None,
        "top5_payout_race_share_percent": 100 * rows.return_yen.nlargest(5).sum() / returned if returned else None,
        "date_bootstrap_gross_roi_95_ci_percent": ci,
        "bootstrap_zero_bet_resamples": int((~valid).sum()),
    }


def load_prestart_markets(root: Path) -> tuple[dict, dict]:
    accepted, rejected = {}, Counter()
    with sqlite3.connect((root / "ledger.sqlite3").as_uri() + "?mode=ro", uri=True) as db:
        db.execute("BEGIN")
        records = db.execute("SELECT race_id,post_time,snapshot_json FROM races WHERE snapshot_json IS NOT NULL").fetchall()
    for race_id, post, raw in records:
        try:
            envelope = json.loads(raw)
            odds = capture_odds(envelope, race_id)
            snap = envelope["snapshot"]
            if aware(snap["post_time"]) != aware(post):
                raise ValueError("capture_schedule_mismatch")
            receipt_id = snap["odds_receipt_sha256"]
            if not re.fullmatch(r"[0-9a-f]{64}", receipt_id):
                raise ValueError("invalid_receipt_digest")
            receipt = json.loads((root / "receipts" / (receipt_id + ".json")).read_text(encoding="utf-8"))
            content = {k: v for k, v in receipt.items() if k != "receipt_sha256"}
            if sha256(content) != receipt_id or receipt.get("receipt_sha256") != receipt_id:
                raise ValueError("receipt_content_hash_mismatch")
            if (receipt.get("source") != "network" or receipt.get("status") != 200
                    or receipt.get("protocol_sha256") != snap["protocol_sha256"]
                    or aware(receipt["received_at"]) != aware(snap["received_at"])):
                raise ValueError("receipt_source_or_clock_mismatch")
            for key in ("request_url", "url"):
                url = urlsplit(receipt[key])
                query = parse_qs(url.query)
                if (url.scheme != "https" or url.hostname != "race.netkeiba.com"
                        or url.path != "/api/api_get_jra_odds.html"
                        or query.get("race_id") != [race_id] or query.get("type") != ["1"]):
                    raise ValueError("receipt_market_or_race_mismatch")
            body_id = receipt["body_sha256"]
            if not re.fullmatch(r"[0-9a-f]{64}", body_id):
                raise ValueError("invalid_body_digest")
            body_path = root / "objects" / body_id
            if file_sha256(body_path) != body_id:
                raise ValueError("odds_response_body_hash_mismatch")
            parsed = parse_odds(body_path.read_bytes(), race_id, received_at=receipt["received_at"])
            if parsed["odds"] != odds or aware(parsed["provider_time"]) != aware(snap["source_at"]):
                raise ValueError("capture_prices_or_tick_not_bound_to_body")
            accepted[race_id] = {
                "odds": odds, "horse_ids": {r["horse_number"]: r["horse_id"] for r in snap["runners"]},
                "source_at": snap["source_at"], "stored_at": snap["stored_at"],
                "post_time": snap["post_time"], "snapshot_sha256": envelope["snapshot_sha256"],
                "receipt_sha256": receipt_id, "body_sha256": body_id,
            }
        except (ValueError, KeyError, TypeError, OSError) as exc:
            rejected[str(exc)] += 1
    return accepted, {"saved_captures": len(records), "verified_captures": len(accepted), "rejections": dict(rejected)}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    output = args.output.resolve()
    if output.exists() or not output.is_relative_to(ROOT / "reports" / "generated" / "race-selection"):
        parser.error("Use a new output directory under reports\\generated\\race-selection")
    experiment = ROOT / "reports" / "generated" / "model-experiments" / "target-baseline-groups-20261101-v1"
    parent = ROOT / "reports" / "generated" / "model-experiments" / "target-optimization-20261101-v1"
    forward = ROOT / "reports" / "generated" / "forward-runtime" / "h04-live-20260920-v2"
    ledger = json.loads((experiment / "task-ledger.json").read_text(encoding="utf-8"))
    manifest = json.loads((experiment / "artifact-manifest.json").read_text(encoding="utf-8"))["files"]
    source_hashes, predictions = {}, []
    pointer = active_pointer_hash()
    for year in (2023, 2024, 2025, 2026):
        folder = experiment / "cases" / f"course_going_weather-standard-{year}"
        for name in ("predictions.parquet", "model.research.joblib", "result.json"):
            path = folder / name
            digest = file_sha256(path)
            if digest != manifest[str(path.relative_to(experiment))]:
                raise ValueError("frozen_model_artifact_hash_mismatch")
            source_hashes[str(path)] = digest
        frame = pd.read_parquet(folder / "predictions.parquet")
        result = json.loads((folder / "result.json").read_text(encoding="utf-8"))
        if result["calibration"]["date_to"] >= str(frame.race_date.min()):
            raise ValueError("calibration_not_before_prediction_period")
        predictions.append(frame)
    target_path = parent / "target.parquet"
    snapshot = next(Path(p) for p in ledger["inputs"] if p.endswith(".db"))
    for path in (target_path, snapshot):
        digest = file_sha256(path)
        if digest != ledger["inputs"][str(path)]:
            raise ValueError("frozen_market_input_hash_mismatch")
        source_hashes[str(path)] = digest
    frame = pd.concat(predictions, ignore_index=True)
    target = pd.read_parquet(target_path, columns=[
        "race_id", "horse_number", "race_date", "horse_id", "finish", "num_horses", "odds",
    ])
    frame = frame.merge(
        target, on=["race_id", "horse_number", "race_date"], how="left",
        validate="one_to_one", suffixes=("", "_source"),
    )
    if frame.horse_id.isna().any() or not frame.finish.eq(frame.finish_source).all():
        raise ValueError("prediction_and_frozen_market_identity_mismatch")
    prestart, capture_audit = load_prestart_markets(forward)
    output.mkdir(parents=True)
    protocol = {
        "schema": "market-axis-return-comparison-v1", "model": "grouped-IV-standard-46-OOT",
        "model_predictions": "existing frozen course_going_weather cases; not H04 predictions",
        "probability_basis": "saved CAL-only race-normalized win probability",
        "policy": METHODS, "stake_per_ticket_yen": 100, "ev_filter_used": False,
        "selection_basis": "single-win favorite plus AI top five, excluding the axis only",
        "market_ties": "lowest single-win odds, then horse number",
        "ai_shortlist": "top five normalized win probabilities; no subset renormalization",
        "maximum_total_horses": 6, "policy_tuning_on_this_run": False,
        "period_previously_observed": True, "prospective_frozen_bet_replay": False,
        "prestart_replay": "verified historical single-win prices, retrospectively reconstructed IV predictions and new policy",
        "automatic_purchase": False, "purchase_eligible": False, "promotion_eligible": False,
        "sources": source_hashes,
        "implementation_sources": {
            str(path): file_sha256(path) for path in (
                Path(__file__), ROOT / "python-api" / "betting" / "research_market_axis.py",
            )
        },
        "bootstrap": "1000 paired calendar-date resamples; seed20261102; no multiple-selection adjustment",
        "active_pointer_sha256": pointer,
    }
    (output / "protocol.json").write_text(canonical(protocol), encoding="utf-8")
    (output / "prestart_markets.json").write_text(canonical(prestart), encoding="utf-8")
    raw_payouts = defaultdict(list)
    with sqlite3.connect(snapshot.as_uri() + "?mode=ro&immutable=1", uri=True) as db:
        ids = frame.race_id.unique().tolist()
        for start in range(0, len(ids), 500):
            batch = ids[start:start + 500]
            sql = "SELECT race_id,bet_type,combinations,payout FROM return_tables_ultimate WHERE race_id IN (" + ",".join("?" for _ in batch) + ") AND bet_type IN ('ワイド','三連複','3連複')"
            for race_id, kind, combination, payout in db.execute(sql, batch):
                raw_payouts[race_id].append({"bet_type": kind, "combinations": combination, "payout": payout})
    rows, verified, retained, exclusions, pre_exclusions = [], {}, [], Counter(), Counter()
    for race_id, group in frame.groupby("race_id", sort=True):
        field = group.to_dict("records")
        try:
            if len(group) < 5 or not group.num_horses.eq(len(group)).all():
                raise ValueError("incomplete_or_small_prediction_field")
            select_tickets(field, "market_wide_ai5")
            payouts = verify_dividends(field, raw_payouts[race_id])
        except (ValueError, TypeError) as exc:
            exclusions[str(exc)] += 1
            continue
        verified[race_id] = {
            kind: {"-".join(map(str, key)): value for key, value in mapping.items()}
            for kind, mapping in payouts.items()
        }
        retained.append(group)
        scenarios = {"final_market_hindsight": field}
        if race_id in prestart:
            capture = prestart[race_id]
            identities = {r["horse_number"]: r["horse_id"] for r in field}
            if identities != capture["horse_ids"]:
                pre_exclusions["capture_prediction_runner_identity_mismatch"] += 1
            else:
                scenarios["prestart_win_market_replay"] = [
                    {**r, "odds": capture["odds"][r["horse_number"]]} for r in field
                ]
        for scenario, market_field in scenarios.items():
            for method, definition in METHODS.items():
                tickets = select_tickets(market_field, method)
                result = settle_tickets(tickets, payouts[definition["bet_type"]])
                if result["ticket_count"] > definition["max_points"]:
                    raise ValueError("strategy_ticket_cap_exceeded")
                rows.append({
                    "race_id": race_id, "race_date": str(group.race_date.iloc[0]),
                    "scenario": scenario, "method": method,
                    "selections": canonical(tickets), **result,
                })
    results = pd.DataFrame(rows)
    if results.empty:
        raise ValueError("no_common_verified_payout_cohort")
    report = {
        "protocol_sha256": sha256(protocol), "automatic_purchase": False,
        "purchase_eligible": False,
        "input_races": int(frame.race_id.nunique()), "common_races": len(retained),
        "payout_or_field_exclusions": dict(exclusions), "capture_audit": capture_audit,
        "prestart_join_exclusions": dict(pre_exclusions), "scenarios": {},
    }
    for scenario, part in results.groupby("scenario", sort=True):
        report["scenarios"][scenario] = {
            "date_from": part.race_date.min(), "date_to": part.race_date.max(),
            "races": int(part.race_id.nunique()),
            "methods": {
                method: {
                    "all": summarize(subset),
                    "by_year": {str(year): summarize(annual) for year, annual in subset.groupby(subset.race_date.str[:4])},
                }
                for method, subset in part.groupby("method", sort=True)
            },
        }
    (output / "verified_payouts.json").write_text(canonical(verified), encoding="utf-8")
    pd.concat(retained, ignore_index=True).to_parquet(output / "cohort_predictions.parquet", index=False)
    results.to_parquet(output / "race_method_results.parquet", index=False)
    report["active_pointer_unchanged"] = pointer == active_pointer_hash()
    if not report["active_pointer_unchanged"]:
        raise ValueError("active_model_changed_during_research")
    (output / "report.json").write_text(canonical(report), encoding="utf-8")
    generated = {p.name: file_sha256(p) for p in output.iterdir() if p.is_file()}
    (output / "artifact-manifest.json").write_text(canonical({"files": generated}), encoding="utf-8")
    print(json.dumps({
        "report": str(output / "report.json"), "common_races": report["common_races"],
        "capture_audit": report["capture_audit"],
        "scenario_races": {name: part["races"] for name, part in report["scenarios"].items()},
        "active_pointer_unchanged": report["active_pointer_unchanged"],
    }, ensure_ascii=True, indent=2), flush=True)


if __name__ == "__main__":
    main()
