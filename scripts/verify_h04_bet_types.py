"""Independent no-fit verification of the fixed rank-first bet-type study."""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import sqlite3
import sys

for name in ("OMP_NUM_THREADS", "OPENBLAS_NUM_THREADS", "MKL_NUM_THREADS", "NUMEXPR_NUM_THREADS"):
    os.environ[name] = "2"
ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / "python-api"), str(ROOT / "scripts"), str(ROOT / "keiba")]
import numpy as np
import pandas as pd
import pyarrow.parquet as pq
from verify_market_segments_outputs import sha256, read, require, verify_files, bound, _protected_state, _quality_check

# Deliberately independent of bet_types.baseline.RULES and its calculation.
RULES = {"win_top1": ("win", [[0]]), "place_top1": ("place", [[0]]),
         "wide_12": ("wide", [[0, 1]]), "wide_13": ("wide", [[0, 2]]),
         "quinella_12": ("quinella", [[0, 1]]), "quinella_top3_box": ("quinella", [[0, 1], [0, 2], [1, 2]])}
PRIMARY = ["top2_recall_at2", "top3_recall_at3", *RULES]
TOP_METRICS = ["winner_in_topn", "top2_recall", "top3_recall", "exacta_pair_in_topn", "quinella_pair_in_topn",
               "trifecta_horses_in_topn", "ordered_leading_pair_hit"]
BET_TYPES = {"win", "place", "wide", "quinella", "exacta", "trio", "trifecta"}
MONEY = ["stake_yen", "return_yen", "profit_yen", "roi_percent", "average_hit_payout_yen", "median_hit_payout_yen",
         "max_payout_yen", "top1_payout_concentration", "top3_payout_concentration", "max_payout_removed_roi", "top3_payout_removed_roi"]


def _csv(path):
    return pd.read_csv(path, dtype={"race_id": str, "race_date": str, "horses": str})


def _parquet(path):
    # Filter in the reader; never decode a later year's outcome then drop it.
    frame = pd.read_parquet(path, filters=[("race_date", ">=", "20210101"), ("race_date", "<=", "20221231")], use_threads=False)
    require(len(frame) == pq.read_metadata(path).num_rows, "out-of-scope parquet rows")
    return frame


def compare_frames(actual, expected, keys, *, exact=False):
    require(not actual.duplicated(keys).any() and not expected.duplicated(keys).any(), "duplicate output key")
    require(set(actual.columns) == set(expected.columns), "output schema mismatch")
    left = actual.sort_values(keys).reset_index(drop=True)[expected.columns]
    right = expected.sort_values(keys).reset_index(drop=True)
    require(len(left) == len(right), "output row count mismatch")
    for column in right:
        if exact:
            require(left[column].equals(right[column]), "source/cohort value mismatch: " + column)
        elif pd.api.types.is_numeric_dtype(right[column]) and not pd.api.types.is_bool_dtype(right[column]):
            require(np.allclose(pd.to_numeric(left[column], errors="raise"), right[column], rtol=0, atol=1e-12, equal_nan=True),
                    "numeric output mismatch: " + column)
        else:
            require(left[column].fillna("__missing__").eq(right[column].fillna("__missing__")).all(), "output mismatch: " + column)


def independent_rows(frame):
    """NumPy sorting and actual horse-ID sets; never call production rank code."""
    needed = {"race_id", "race_date", "horse_number", "finish", "h04_score", "odds", "expected_runner_count", "tansho_payout"}
    require(needed <= set(frame) and not frame.empty and not frame.duplicated(["race_id", "horse_number"]).any(), "invalid common cohort")
    race_rows, top_rows, tickets = [], [], []
    for race_id, part in frame.groupby("race_id", sort=True):
        n = len(part)
        dates = part.race_date.astype(str)
        require(dates.nunique() == 1 and dates.str.fullmatch(r"202[12]\d{4}").all(), "invalid cohort date")
        day = dates.iloc[0]
        pd.to_datetime(day, format="%Y%m%d", errors="raise")
        numbers, finish = part.horse_number.to_numpy(float), part.finish.to_numpy(float)
        score, odds = part.h04_score.to_numpy(float), part.odds.to_numpy(float)
        require(8 <= n <= 18 and part.expected_runner_count.eq(n).all(), "not a complete >=8-runner race")
        require(np.isfinite(np.r_[numbers, finish, score, odds]).all() and np.all(odds >= 1)
                and np.all(numbers == np.floor(numbers)) and np.all((numbers >= 1) & (numbers <= 18))
                and len(set(numbers)) == n and np.array_equal(np.sort(finish), np.arange(1, n + 1)), "invalid runner/rank/value")
        numbers = numbers.astype(int)
        actual = numbers[np.argsort(finish)]
        first, first2, first3 = int(actual[0]), set(actual[:2]), set(actual[:3])
        winner_paid = float(part.loc[part.horse_number.eq(first), "tansho_payout"].iloc[0])
        require(np.isfinite(winner_paid) and winner_paid >= 100 and winner_paid % 10 == 0, "unverified winner payout")
        period = "calibration" if day < "20210701" else "development" if day < "20220101" else "validation"
        for model, value in (("H04", -score), ("Market", odds)):
            predicted = numbers[np.lexsort((numbers, value))]
            base = {"race_id": str(race_id), "race_date": day, "period": period, "model": model, "runners": n}
            race = dict(base)
            for top_n in range(1, 6):
                chosen = set(predicted[:top_n])
                row = {**base, "top_n": top_n, "winner_in_topn": int(first in chosen),
                    "top2_recall": len(chosen & first2) / 2, "top3_recall": len(chosen & first3) / 3,
                    "exacta_pair_in_topn": int(first2 <= chosen), "quinella_pair_in_topn": int(first2 <= chosen),
                    "trifecta_horses_in_topn": int(first3 <= chosen),
                    "ordered_leading_pair_hit": int(top_n >= 2 and np.array_equal(predicted[:2], actual[:2]))}
                top_rows.append(row)
                if top_n in (2, 3): race[f"top{top_n}_recall_at{top_n}"] = row[f"top{top_n}_recall"]
            for rule, (kind, choices) in RULES.items():
                hits = []
                for choice in choices:
                    horses = tuple(sorted(int(v) for v in predicted[choice]))
                    hit = (horses == (first,)) if kind == "win" else (set(horses) <= first3) if kind in {"place", "wide"} else set(horses) == first2
                    hits.append(hit)
                    tickets.append({**base, "rule": rule, "bet_type": kind, "horses": "-".join(map(str, horses)),
                        "hit": int(hit), "payout_yen": winner_paid if kind == "win" and hit else 0. if kind == "win" else None,
                        "roi_evaluable": kind == "win", "fixed_unit_yen": 100})
                race[rule] = int(any(hits))
            race_rows.append(race)
    return pd.DataFrame(race_rows), pd.DataFrame(top_rows), pd.DataFrame(tickets)


def _boot(values, dates, samples, seed):
    values, dates = np.asarray(values, dtype=float), np.asarray(dates)
    days, day_index = np.unique(dates, return_inverse=True)
    totals, counts = np.bincount(day_index, weights=values), np.bincount(day_index)
    draws = np.random.default_rng(seed).integers(0, len(days), size=(samples, len(days)))
    boot = totals[draws].sum(axis=1) / counts[draws].sum(axis=1)
    return {"point": float(values.mean()), "ci95_low": float(np.quantile(boot, .025)),
            "ci95_high": float(np.quantile(boot, .975)), "bootstrap_samples": samples, "usable_samples": samples, "days": len(days)}, boot


def independent_gate(races, plan):
    settings = plan["bootstrap"]
    development, validation, samples_by_metric = [], [], []
    for period in ("development", "validation"):
        part = races.loc[races.period.eq(period)]
        h = part.loc[part.model.eq("H04")].set_index("race_id").sort_index()
        m = part.loc[part.model.eq("Market")].set_index("race_id").sort_index()
        require(not h.empty and h.index.equals(m.index), "paired evaluation cohort missing")
        for metric in PRIMARY:
            delta = h[metric].to_numpy(float) - m[metric].to_numpy(float)
            stats, boot = _boot(delta, h.race_date.to_numpy(), settings["samples"], settings["seed"])
            row = {"period": period, "metric": metric, "races": len(h), "runners": int(h.runners.sum()),
                "sample_pass": bool(len(h) >= plan["minimum_races"] and h.runners.sum() >= plan["minimum_runners"]),
                "H04": float(h[metric].mean()), "Market": float(m[metric].mean()), **stats}
            if period == "development": development.append(row)
            else:
                half = h.race_date.le("20220630").to_numpy()
                row.update(H1_delta=float(delta[half].mean()) if half.any() else None,
                           H2_delta=float(delta[~half].mean()) if (~half).any() else None,
                           H1_races=int(half.sum()), H2_races=int((~half).sum()))
                validation.append(row); samples_by_metric.append(boot)
    matrix = np.column_stack(samples_by_metric)
    points = np.array([row["point"] for row in validation])
    deviation = matrix.std(axis=0, ddof=1)
    standardized = np.divide(abs(matrix - points), deviation, out=np.zeros_like(matrix), where=deviation > 0)
    critical = float(np.quantile(standardized.max(axis=1), .95))
    for i, row in enumerate(validation):
        dev = development[i]
        row.update(family_ci95_low=float(points[i] - critical * deviation[i]), family_ci95_high=float(points[i] + critical * deviation[i]),
                   family_size=8, development_delta=dev["point"], development_sample_pass=dev["sample_pass"])
        row["lead"] = bool(dev["sample_pass"] and row["sample_pass"] and dev["point"] > 0 and row["family_ci95_low"] > 0
                           and row["H1_delta"] is not None and row["H2_delta"] is not None and row["H1_delta"] > 0 and row["H2_delta"] > 0)
    leads = [row["metric"] for row in validation if row["lead"]]
    return {"development_comparisons": development, "primary_comparisons": validation,
            "leads": leads, "nonwin_leads": [name for name in leads if name != "win_top1"], "critical": critical}


def verify_gate(saved, expected):
    compare_frames(pd.DataFrame(saved["development_comparisons"]), pd.DataFrame(expected["development_comparisons"]), ["metric"])
    compare_frames(pd.DataFrame(saved["primary_comparisons"]), pd.DataFrame(expected["primary_comparisons"]), ["metric"])
    require(saved["leads"] == expected["leads"] and saved["nonwin_leads"] == expected["nonwin_leads"], "rank lead set mismatch")
    allowed = bool(expected["nonwin_leads"])
    require(saved["calibrator_fitting_allowed"] is allowed and saved["gate"] == ("CONTINUE_CALIBRATION" if allowed else "STOP_NO_STABLE_RANK_INCREMENT"), "rank stop gate mismatch")
    require(saved["independent_validation"] is False and saved["new_model_training"] is False
            and saved["phase_B_allowed"] is False and saved["phase_C_allowed"] is False, "unapproved model/phase promotion")
    require(saved["multiplicity"]["comparisons"] == 8 and np.isclose(saved["multiplicity"]["critical"], expected["critical"], rtol=0, atol=1e-12), "simultaneous family changed")


def _ledger(directory, expected):
    with sqlite3.connect((directory / "job_ledger.sqlite").as_uri() + "?mode=ro", uri=True) as db:
        db.row_factory = sqlite3.Row
        db.execute("PRAGMA query_only=ON"); db.execute("BEGIN")
        try:
            meta = db.execute("SELECT value FROM meta WHERE key='plan_sha256'").fetchone()
            rows = [dict(row) for row in db.execute("SELECT * FROM jobs")]
        finally: db.rollback()
    require(meta and meta[0] == expected and len(rows) == 1, "exactly one plan-bound job required")
    job = rows[0]
    require(job["job_id"] == "rank-baseline" and job["status"] == "completed" and job["exit_code"] == 0, "baseline job not completed")
    result_path = directory / "baseline-result.json"
    require(Path(job["result_path"]).resolve() == result_path and sha256(result_path) == job["result_sha256"], "completed result hash mismatch")
    require(job["started_at"] is not None and job["finished_at"] >= job["started_at"], "invalid job timestamps")
    command = json.loads(job["command_json"])
    require(command.count("baseline") == 1 and command.count("--expected-plan-sha256") == 1
            and command[command.index("--expected-plan-sha256") + 1] == expected, "worker command contract differs")
    return job


def _protect(repo):
    protected = _protected_state(repo)
    old = repo / "reports/generated/model-experiments/market-segments-v1"
    manifest = read(old / "artifact_manifest.json")
    protected["old_segments_files"] = verify_files(old, manifest["files"])
    protected["old_segments_manifest_sha256"] = sha256(old / "artifact_manifest.json")
    verify_files(repo, read(old / "segment_plan.json")["code_sha256"])
    return protected


def _summaries(topn, tickets):
    top = topn.groupby(["period", "model", "top_n"], sort=True).agg(races=("race_id", "size"), runners=("runners", "sum"),
        **{key: (key, "mean") for key in TOP_METRICS}).reset_index()
    rules = []
    for (period, model, rule), part in tickets.groupby(["period", "model", "rule"], sort=True):
        n, kind = len(part), part.bet_type.iloc[0]
        money = dict.fromkeys(MONEY)
        if kind == "win":
            paid = np.sort(part.payout_yen.to_numpy(float))[::-1]
            total, positive = float(paid.sum()), paid[paid > 0]
            money = dict(zip(MONEY, [n * 100, total, total - n * 100, total / n,
                float(positive.mean()) if len(positive) else None, float(np.median(positive)) if len(positive) else None,
                float(paid[0]), float(paid[0] / total) if total else None, float(paid[:3].sum() / total) if total else None,
                float(paid[1:].sum() / (n - 1)) if n > 1 else None, float(paid[3:].sum() / (n - 3)) if n > 3 else None]))
        races = part.race_id.nunique()
        rules.append({"period": period, "model": model, "rule": rule, "bet_type": kind, "races": races,
            "bet_races": races, "bet_rate": 1., "bets": n, "ticket_count": n, "hit_count": int(part.hit.sum()),
            "hit_rate": float(part.hit.mean()), "race_hit_rate": float(part.groupby("race_id").hit.max().mean()),
            "roi_evaluable": kind == "win", "status": "historical_win_reference" if kind == "win" else "prediction_only_no_all_combination_odds", **money})
    return top, pd.DataFrame(rules)


def verify_outputs(experiment_root, expected_plan_sha256, *, repo_root=ROOT):
    directory, repo = Path(experiment_root).resolve(), Path(repo_root).resolve()
    receipt = {"schema": "h04-bet-types-independent-verification-v1", "verified": False, "issues": [],
        "created_at": datetime.now(timezone.utc).isoformat(), "plan_sha256": expected_plan_sha256,
        "source_script_sha256": sha256(Path(__file__)), "new_model_or_temperature_fits": 0,
        "post_2022_outcomes_decoded": False, "independent_rank_method": "numpy.lexsort + actual horse-number sets; no baseline rank function reused"}
    try:
        require(sha256(directory / "experiment_plan.json") == expected_plan_sha256, "plan changed")
        plan = read(directory / "experiment_plan.json")
        require(plan["schema"] == "h04-fixed-bet-types-preregistered-v1" and plan["audits_completed_before_model_computation"] is True, "premetric frozen plan required")
        require(plan["fixed_rules"] == {name: {"bet_type": kind, "zero_based_rank_positions": positions} for name, (kind, positions) in RULES.items()}
                and plan["topn"] == [1, 2, 3, 4, 5] and plan["primary_rank_contrasts"] == PRIMARY and plan["stake_per_ticket_yen"] == 100, "fixed rank rule contract changed")
        verify_files(repo, plan["source_sha256"])
        _quality_check(plan)
        receipt["protected_before"] = _protect(repo)
        job = _ledger(directory, expected_plan_sha256)
        require(Path(json.loads(job["command_json"])[1]).resolve() == repo / "scripts/run_market_edge_bet_types.py", "unexpected worker source")
        require(datetime.fromisoformat(plan["frozen_at"]).timestamp() <= job["started_at"], "worker preceded frozen plan")
        result = read(directory / "baseline-result.json")
        require(result["status"] == "completed" and result["plan_sha256"] == expected_plan_sha256
                and result["new_H04_fits"] == result["new_calibrator_fits"] == result["actual_purchases"] == 0
                and result["post_2022_outcomes"] is False, "forbidden fitting/purchases/scope")
        verify_files(directory, result["artifact_sha256"], required=("common_cohort.parquet", "rank_race_metrics.parquet", "fixed_tickets.parquet", "topn_race_metrics.parquet", "rank_gate.json", "bootstrap_results.json"))
        for key, audit in plan["audits"].items():
            folder = directory / audit["directory"]
            require(folder.resolve().is_relative_to(directory) and sha256(folder / "artifact_manifest.json") == audit["manifest_sha256"], "premetric audit manifest changed")
            manifest = read(folder / "artifact_manifest.json")
            require(manifest["files"] == audit["files"], "audit file declaration mismatch")
            verify_files(folder, audit["files"])
            verify_files(repo, manifest.get("code", manifest.get("source", {})))
        require(read(directory / "ranking_distribution_spec.json") == plan["distribution_spec"], "distribution definition changed")
        inventory = _csv(directory / plan["audits"]["inventory"]["directory"] / "bet_type_data_inventory.csv")
        require(len(inventory) == 7 and inventory.bet_type.nunique() == 7
                and inventory.loc[inventory.bet_type.ne("tansho"), "all_combination_odds_races"].eq(0).all(), "unexpected nonwin odds permission")
        eligibility = _csv(directory / plan["audits"]["eligibility"]["directory"] / "race_eligibility.csv")
        require(not eligibility.race_id.duplicated().any() and eligibility.common_phase_a_eligible.isin([True, False]).all(), "invalid eligibility keys/booleans")
        ids = set(eligibility.loc[eligibility.common_phase_a_eligible, "race_id"])
        source = Path(plan["phase1_gate"]["quality_summary_path"]).parent / "clean_runners.parquet"
        require(sha256(source) == plan["source_cohort_sha256"], "original cohort changed")
        original = _parquet(source)
        expected = original.loc[original.race_id.isin(ids)].copy()
        common = _parquet(directory / "common_cohort.parquet")
        require(set(expected.race_id) == set(common.race_id) == ids, "extra/missing common race")
        compare_frames(common, expected, ["race_id", "horse_number"], exact=True)
        require(result["common_cohort_sha256"] == sha256(directory / "common_cohort.parquet")
                and result["cohort"] == {"races": len(ids), "runners": len(common)}, "common cohort receipt differs")
        races, topn, tickets = independent_rows(common)
        for name, expected_rows, keys in (("rank_race_metrics.parquet", races, ["race_id", "model"]),
                ("topn_race_metrics.parquet", topn, ["race_id", "model", "top_n"]),
                ("fixed_tickets.parquet", tickets, ["race_id", "model", "rule", "horses"])):
            compare_frames(_parquet(directory / name), expected_rows, keys)
        top_summary, rule_summary = _summaries(topn, tickets)
        compare_frames(_csv(directory / "topn_accuracy.csv"), top_summary, ["period", "model", "top_n"])
        compare_frames(_csv(directory / "fixed_rule_results.csv"), rule_summary, ["period", "model", "rule"])
        compare_frames(_csv(directory / "market_baseline.csv"), rule_summary.loc[rule_summary.model.eq("Market")], ["period", "model", "rule"])
        yearly = rule_summary.assign(year=rule_summary.period.map({"calibration": 2021, "development": 2021, "validation": 2022}))
        compare_frames(_csv(directory / "yearly_results.csv"), yearly, ["period", "model", "rule"])
        concentration_columns = ["period", "model", "rule", "bet_type", "roi_evaluable", "roi_percent", "top1_payout_concentration",
                                 "top3_payout_concentration", "max_payout_removed_roi", "top3_payout_removed_roi", "status"]
        compare_frames(_csv(directory / "payout_concentration.csv"), rule_summary[concentration_columns], ["period", "model", "rule"])
        expected_gate = independent_gate(races, plan)
        gate = read(directory / "rank_gate.json")
        verify_gate(gate, expected_gate)
        bootstrap = read(directory / "bootstrap_results.json")
        require(bootstrap["rank_gate"] == gate and result["rank_gate"] == gate["gate"] and result["leads"] == gate["leads"]
                and result["calibration_next_allowed"] == gate["calibrator_fitting_allowed"], "gate/receipt mismatch")
        reference = []
        for (period, model), part in tickets.loc[tickets.rule.eq("win_top1") & tickets.period.isin(["development", "validation"])].groupby(["period", "model"]):
            stats, _ = _boot(part.payout_yen.to_numpy(float), part.race_date.to_numpy(), plan["bootstrap"]["samples"], plan["bootstrap"]["seed"])
            reference.append({"period": period, "model": model, "bet_type": "win", "rule": "win_top1", **stats,
                              "metric": "ROI_percent", "status": "historical_only_not_executable_T5"})
        compare_frames(pd.DataFrame(bootstrap["ROI_reference"]), pd.DataFrame(reference), ["period", "model"])
        compare_frames(_csv(directory / "paired_rank_comparison.csv"), pd.DataFrame(gate["development_comparisons"] + gate["primary_comparisons"]), ["period", "metric"])
        require(tickets.loc[tickets.bet_type.ne("win"), "payout_yen"].isna().all()
                and rule_summary.loc[rule_summary.bet_type.ne("win"), MONEY].isna().all().all(), "nonwin monetary evaluation forbidden")
        if not gate["calibrator_fitting_allowed"]:
            ev, probability = _csv(directory / "ev_results.csv"), _csv(directory / "combination_probability_audit.csv")
            require(len(ev) == len(probability) == 7 and set(ev.bet_type) == set(probability.bet_type) == BET_TYPES
                    and ev.status.eq("NOT_EVALUATED").all() and ev[["bets", "ROI", "profit"]].isna().all().all()
                    and probability.status.eq("NOT_COMPUTED_REAL_DATA").all() and probability.temperature_fitted.eq(False).all(), "early stop not enforced")
            forbidden = [p.name for p in directory.iterdir() if p.is_file() and any(token in p.name.lower() for token in ("calibrator", "temperature", "phase_b", "phase_c"))]
            require(not forbidden, "unapproved post-gate artifacts")
        verify_files(repo, plan["source_sha256"])
        verify_files(directory, result["artifact_sha256"])
        require(_ledger(directory, expected_plan_sha256) == job and sha256(source) == plan["source_cohort_sha256"], "sealed inputs/results changed during verification")
        _quality_check(plan)
        receipt["protected_after"] = _protect(repo)
        receipt.update(verified=True, completed_jobs=1, races=len(ids), runners=len(common),
            independently_checked_race_model_rows=len(races), independently_checked_topn_rows=len(topn),
            independently_checked_tickets=len(tickets), rank_gate=gate["gate"], nonwin_leads=gate["nonwin_leads"],
            result_sha256=job["result_sha256"], common_cohort_sha256=result["common_cohort_sha256"],
            source_files_checked=len(plan["source_sha256"]), nonwin_ROI_EV_performed=False,
            premetric_audit_manifest_sha256={key: item["manifest_sha256"] for key, item in plan["audits"].items()})
    except (ValueError, KeyError, TypeError, IndexError, AttributeError, OSError, AssertionError, sqlite3.Error) as exc:
        receipt["issues"].append(type(exc).__name__ + ": " + str(exc))
    return receipt


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--experiment-root", type=Path, required=True)
    parser.add_argument("--expected-plan-sha256", required=True)
    args = parser.parse_args()
    if os.name == "nt":
        import psutil
        psutil.Process().nice(psutil.BELOW_NORMAL_PRIORITY_CLASS)
    result = verify_outputs(args.experiment_root, args.expected_plan_sha256)
    with (args.experiment_root / "independent-verification.json").open("x", encoding="utf-8") as stream:
        json.dump(result, stream, ensure_ascii=False, indent=2, allow_nan=False)
    print(json.dumps(result, ensure_ascii=False, indent=2))
    if not result["verified"]: raise SystemExit(1)
