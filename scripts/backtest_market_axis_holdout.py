"""Evaluate every TEST race of the saved conditioned-baseline trial model."""
from __future__ import annotations

import argparse
from collections import Counter, defaultdict
from datetime import datetime, timedelta, timezone
import json
from pathlib import Path
import shutil
import sqlite3
import sys
from urllib.parse import parse_qs, urlsplit

import joblib
import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / "python-api"), str(ROOT / "keiba"), str(ROOT / "scripts")]

from backtest_market_axis import load_prestart_markets, summarize
from betting.research_holdout import settle_holdout_tickets, verify_holdout_dividends
from betting.research_market_axis import METHODS, select_tickets
from experiments.common import active_pointer_hash, file_sha256
from forward_runtime.provider import parse_result, soup_for
from forward_runtime.schedule import canonical, sha256
from scraping.race import _parse_return_tables
from training import db_shared_grouped as grouped


def full_summary(rows: pd.DataFrame) -> dict:
    known = rows.loc[rows.return_yen.notna() & rows.hit_ticket_count.notna()]
    counts = {
        "cohort_races": len(rows), "settled_races": len(known),
        "unsettled_races": len(rows) - len(known),
    }
    if len(known) == len(rows):
        return {**summarize(rows), **counts, "settled_subset": None}
    return {
        **counts, "bet_races": int(rows.cost_yen.gt(0).sum()),
        "tickets": int(rows.ticket_count.sum()), "cost_yen": int(rows.cost_yen.sum()),
        "return_yen": None, "net_yen": None, "gross_return_rate_percent": None,
        "hit_race_rate_percent": None,
        "settled_subset": summarize(known) if len(known) else None,
    }


def result_sources(forward: Path, supplemental: Path) -> dict:
    sources = defaultdict(list)
    for path in sorted((forward / "receipts").glob("*.json")):
        receipt = json.loads(path.read_text(encoding="utf-8"))
        url = urlsplit(receipt.get("url", ""))
        if url.path != "/race/result.html" or receipt.get("status") != 200:
            continue
        ids = parse_qs(url.query).get("race_id", [])
        if len(ids) != 1:
            continue
        content = {k: v for k, v in receipt.items() if k != "receipt_sha256"}
        if (sha256(content) != path.stem or receipt.get("receipt_sha256") != path.stem
                or receipt.get("source") != "network"):
            raise ValueError("archived_result_receipt_hash_mismatch")
        sources[ids[0]].append((receipt, forward / "objects" / receipt["body_sha256"], "saved_result_html"))
    for path in sorted(supplemental.glob("full-holdout-result-*.json")):
        receipt = json.loads(path.read_text(encoding="utf-8"))
        ids = parse_qs(urlsplit(receipt["url"]).query).get("race_id", [])
        if len(ids) == 1 and receipt.get("status") == 200 and receipt.get("source") == "network":
            sources[ids[0]].append((receipt, path.with_suffix(".html"), "supplemental_result_html"))
    return sources


def recover_dividends(race_id: str, field: list[dict], metadata: dict, sources: list, output: Path):
    post = datetime.strptime(
        str(metadata["date"]) + metadata["post_time"].replace(":", ""), "%Y%m%d%H%M",
    ).replace(tzinfo=timezone(timedelta(hours=9))).isoformat()
    rejected = []
    for receipt, path, origin in sources:
        try:
            if file_sha256(path) != receipt["body_sha256"]:
                raise ValueError("result_response_body_hash_mismatch")
            body = path.read_bytes()
            result = parse_result(body, race_id, post, receipt=receipt)
            if set(result["runner_numbers"]) != {r["horse_number"] for r in field}:
                raise ValueError("result_prediction_runner_identity_mismatch")
            top = {r["horse_number"]: int(r["finish"]) for r in field if r["finish"] <= 3}
            if {n: rank for n, rank in result["ranks"].items() if rank <= 3} != top:
                raise ValueError("result_frozen_top_three_mismatch")
            raw = _parse_return_tables(soup_for(body), race_id)
            payouts = verify_holdout_dividends(field, raw)
            (output / "objects").mkdir(exist_ok=True)
            (output / "receipts").mkdir(exist_ok=True)
            (output / "objects" / receipt["body_sha256"]).write_bytes(body)
            evidence = {"receipt": receipt, "receipt_sha256": sha256(receipt), "origin": origin}
            (output / "receipts" / f"{race_id}.json").write_text(canonical(evidence), encoding="utf-8")
            return payouts, {
                "origin": origin, "body_sha256": receipt["body_sha256"],
                "receipt_sha256": sha256(receipt), "rejected_candidates": rejected,
            }
        except (ValueError, KeyError, TypeError, OSError) as exc:
            rejected.append(str(exc))
    return None, {"origin": "unresolved", "rejected_candidates": rejected}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--model-evidence", type=Path, required=True)
    parser.add_argument("--supplemental-results", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    output = args.output.resolve()
    if output.exists() or not output.is_relative_to(ROOT / "reports" / "generated" / "race-selection"):
        parser.error("Use a new output directory under reports\\generated\\race-selection")
    pointer = active_pointer_hash()
    evidence = json.loads(args.model_evidence.read_text(encoding="utf-8"))
    model_path = Path(evidence["saved_artifact"])
    if file_sha256(model_path) != evidence["artifact_sha256"]:
        raise ValueError("saved_trial_model_hash_mismatch")
    parent = Path(evidence["source_cohort"])
    snapshot = parent / "snapshots" / "05695c917a0d3fe183b3a6b28c145adcd8d9b735979258cd65e227e6a15c18aa.db"
    paths = {
        parent / "target.parquet": "96fe66264b82dedce223b3d6d55a1a2da3426585106df049faca93cab391469b",
        parent / "features.parquet": "9a1b02e487dba22d2ae236d349585b65e3c1411eb725cbca359d9d300d1d7315",
        snapshot: snapshot.stem,
    }
    for path, digest in paths.items():
        if file_sha256(path) != digest:
            raise ValueError("frozen_trial_input_hash_mismatch")
    bundle = joblib.load(model_path)
    grouped._validate_prediction_bundle(bundle)
    target, features = pd.read_parquet(parent / "target.parquet"), pd.read_parquet(parent / "features.parquet")
    if len(target) != len(features) or list(features.columns) != list(bundle["feature_columns"]):
        raise ValueError("frozen_trial_feature_alignment_mismatch")
    partitions, masks = {}, {}
    for name, definition in evidence["partitions"].items():
        mask = target.race_date.between(definition["date_from"], definition["date_to"])
        part = target.loc[mask]
        observed = {
            "date_from": str(part.race_date.min()), "date_to": str(part.race_date.max()),
            "date_count": int(part.race_date.nunique()), "race_count": int(part.race_id.nunique()),
            "runner_count": len(part),
        }
        if observed != definition:
            raise ValueError("trial_partition_receipt_mismatch")
        partitions[name], masks[name] = observed, mask
    if (not partitions["TRAIN"]["date_to"] < partitions["CAL"]["date_from"]
            or not partitions["CAL"]["date_to"] < partitions["TEST"]["date_from"]
            or sum(part["runner_count"] for part in partitions.values()) != len(target)):
        raise ValueError("trial_partitions_overlap_or_omit_source_rows")
    test = target.loc[masks["TEST"]].reset_index(drop=True)
    inputs = features.loc[masks["TEST"]].reset_index(drop=True)
    scores, probabilities = grouped.predict_shared_scores(bundle, inputs, test.race_id, n_threads=2)
    if (probabilities is None or not np.isfinite(scores).all() or not np.isfinite(probabilities).all()
            or np.any(probabilities < 0) or np.any(probabilities > 1)):
        raise ValueError("trial_prediction_probability_invalid")
    test["score"], test["probability"] = scores, probabilities
    if not np.allclose(test.groupby("race_id").probability.sum(), 1, atol=1e-8, rtol=0):
        raise ValueError("trial_race_probability_sum_invalid")
    correlations = [
        group.score.corr(-group.finish, method="spearman")
        for _, group in test.groupby("race_id", sort=True)
    ]
    best = test.sort_values(["race_id", "probability", "horse_number"], ascending=[True, False, True]).drop_duplicates("race_id")
    metrics = {
        "rank_correlation": float(np.nan_to_num(correlations).mean()),
        "top_pick_win_rate": float(best.finish.eq(1).mean()),
    }
    if any(not np.isclose(value, evidence["evaluation"]["primary"][key], atol=1e-10, rtol=0) for key, value in metrics.items()):
        raise ValueError("saved_trial_test_evaluation_not_reproduced")
    output.mkdir(parents=True)
    shutil.copy2(model_path, output / "model.research.joblib")
    shutil.copy2(args.model_evidence, output / "model-evidence.json")
    test.to_parquet(output / "cohort_predictions.parquet", index=False)
    forward = ROOT / "reports" / "generated" / "forward-runtime" / "h04-live-20260920-v2"
    prestart, capture_audit = load_prestart_markets(forward)
    sources = result_sources(forward, args.supplemental_results)
    raw_payouts, metadata = defaultdict(list), {}
    ids = test.race_id.unique().tolist()
    with sqlite3.connect(snapshot.as_uri() + "?mode=ro&immutable=1", uri=True) as db:
        for start in range(0, len(ids), 500):
            batch = ids[start:start + 500]
            placeholders = ",".join("?" for _ in batch)
            for rid, kind, key, value in db.execute(
                f"SELECT race_id,bet_type,combinations,payout FROM return_tables_ultimate WHERE race_id IN ({placeholders}) AND bet_type IN ('ワイド','三連複','3連複')", batch,
            ):
                raw_payouts[rid].append({"bet_type": kind, "combinations": key, "payout": value})
            for rid, data in db.execute(f"SELECT race_id,data FROM races_ultimate WHERE race_id IN ({placeholders})", batch):
                metadata[rid] = json.loads(data)
    results, audit, verified, pre_exclusions = [], [], {}, Counter()
    for race_id, group in test.groupby("race_id", sort=True):
        field = group.to_dict("records")
        if not group.num_horses.eq(len(group)).all() or group.horse_number.duplicated().any():
            raise ValueError("incomplete_test_prediction_field")
        if str(metadata[race_id]["date"]) != str(group.race_date.iloc[0]):
            raise ValueError("frozen_race_metadata_date_mismatch")
        ordinary = sorted(group.finish.tolist()) == list(range(1, len(group) + 1))
        top = group.loc[group.finish.le(3)]
        payout_audit = {"origin": "frozen_return_tables"}
        try:
            payouts = verify_holdout_dividends(field, raw_payouts[race_id])
        except (ValueError, TypeError) as exc:
            payouts, payout_audit = recover_dividends(race_id, field, metadata[race_id], sources[race_id], output)
            payout_audit["frozen_payout_issue"] = str(exc)
        if payouts is not None:
            verified[race_id] = {
                kind: {"-".join(map(str, key)): value for key, value in mapping.items()}
                for kind, mapping in payouts.items()
            }
        audit.append({
            "race_id": race_id, "race_date": str(group.race_date.iloc[0]),
            "runner_count": len(group), "ordinary_complete_finish": ordinary,
            "top_three_dead_heat": top.finish.duplicated().any().item(),
            "settled": payouts is not None, **payout_audit,
        })
        selection = [{key: r[key] for key in ("horse_number", "probability", "odds")} for r in field]
        scenarios = {"stored_win_market_hindsight": selection}
        if race_id in prestart:
            capture = prestart[race_id]
            if {r["horse_number"]: r["horse_id"] for r in field} != capture["horse_ids"]:
                pre_exclusions["capture_prediction_runner_identity_mismatch"] += 1
            else:
                scenarios["prestart_win_market_replay"] = [
                    {**r, "odds": capture["odds"][r["horse_number"]]} for r in selection
                ]
        for scenario, market in scenarios.items():
            for method, definition in METHODS.items():
                tickets = select_tickets(market, method)
                settlement = settle_holdout_tickets(tickets, None if payouts is None else payouts[definition["bet_type"]])
                if settlement["ticket_count"] > definition["max_points"]:
                    raise ValueError("research_method_ticket_cap_exceeded")
                results.append({
                    "race_id": race_id, "race_date": str(group.race_date.iloc[0]),
                    "scenario": scenario, "method": method, "selections": canonical(tickets),
                    "settled": payouts is not None, **settlement,
                })
    rows = pd.DataFrame(results)
    all_test = rows.loc[rows.scenario.eq("stored_win_market_hindsight")]
    if (len(all_test) != len(ids) * len(METHODS)
            or all_test.duplicated(["race_id", "method"]).any()
            or any(set(part.race_id) != set(ids) for _, part in all_test.groupby("method"))):
        raise ValueError("full_test_race_method_coverage_mismatch")
    implementation = [
        Path(__file__), ROOT / "scripts" / "backtest_market_axis.py",
        ROOT / "python-api" / "betting" / "research_holdout.py",
        ROOT / "python-api" / "betting" / "research_market_axis.py",
        ROOT / "python-api" / "forward_runtime" / "provider.py",
        ROOT / "python-api" / "scraping" / "race.py",
    ]
    protocol = {
        "schema": "full-test-market-axis-v1", "partitions": partitions,
        "evaluated_partition": "TEST", "model_retrained": False, "calibrator_refitted": False,
        "trial_model_sha256": evidence["artifact_sha256"], "feature_count": len(inputs.columns),
        "normalization": bundle["target_normalization"], "policy": METHODS,
        "single_win_odds_only_for_axis": True, "opponents": "AI top five, excluding axis, then first N",
        "stake_per_ticket_yen": 100, "ev_filter_used": False, "maximum_total_horses": 6,
        "policy_tuning_on_this_run": False, "test_previously_observed": True,
        "prestart_replay": "saved verified prestart odds; retrospectively reconstructed trial model predictions",
        "full_test_market": "frozen stored single-win odds; not verified prestart throughout the TEST period",
        "automatic_purchase": False, "purchase_eligible": False, "promotion_eligible": False,
        "sources": {**{str(p): h for p, h in paths.items()}, str(model_path): file_sha256(model_path),
                    str(args.model_evidence): file_sha256(args.model_evidence)},
        "implementation_sources": {str(p): file_sha256(p) for p in implementation},
        "active_pointer_sha256": pointer,
    }
    report = {
        "protocol_sha256": sha256(protocol), "partitions": partitions, "input_test_races": len(ids),
        "input_test_runners": len(test), "reproduced_model_metrics": metrics,
        "settled_test_races": sum(r["settled"] for r in audit),
        "unsettled_test_races": sum(not r["settled"] for r in audit),
        "nonordinary_complete_finish_races": sum(not r["ordinary_complete_finish"] for r in audit),
        "top_three_dead_heat_races": sum(r["top_three_dead_heat"] for r in audit),
        "payout_origins": dict(Counter(r["origin"] for r in audit)),
        "capture_audit": capture_audit, "capture_without_test_prediction": len(set(prestart) - set(ids)),
        "prestart_join_exclusions": dict(pre_exclusions), "scenarios": {},
        "automatic_purchase": False, "purchase_eligible": False,
        "active_pointer_unchanged": pointer == active_pointer_hash(),
    }
    if not report["active_pointer_unchanged"] or any(file_sha256(path) != digest for path, digest in paths.items()):
        raise ValueError("protected_model_pointer_or_frozen_inputs_changed")
    for scenario, part in rows.groupby("scenario", sort=True):
        report["scenarios"][scenario] = {
            "date_from": str(part.race_date.min()), "date_to": str(part.race_date.max()),
            "races": int(part.race_id.nunique()),
            "methods": {
                method: {"all": full_summary(subset),
                         "by_year": {year: full_summary(annual) for year, annual in subset.groupby(subset.race_date.str[:4])}}
                for method, subset in part.groupby("method", sort=True)
            },
        }
    for name, content in (
        ("protocol.json", protocol), ("report.json", report), ("race_audit.json", audit),
        ("verified_payouts.json", verified), ("prestart_markets.json", prestart),
    ):
        (output / name).write_text(canonical(content), encoding="utf-8")
    rows.to_parquet(output / "race_method_results.parquet", index=False)
    manifest = {str(path.relative_to(output)): file_sha256(path) for path in output.rglob("*") if path.is_file()}
    (output / "artifact-manifest.json").write_text(canonical({"files": manifest}), encoding="utf-8")
    print(json.dumps({
        "report": str(output / "report.json"), "test_races": len(ids), "test_runners": len(test),
        "settled": report["settled_test_races"], "unsettled": report["unsettled_test_races"],
        "payout_origins": report["payout_origins"], "model_metrics": metrics,
        "scenario_races": {key: value["races"] for key, value in report["scenarios"].items()},
        "active_pointer_unchanged": report["active_pointer_unchanged"],
    }, indent=2), flush=True)


if __name__ == "__main__":
    main()
