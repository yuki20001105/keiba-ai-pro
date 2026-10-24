"""External, research-only audit of a completed frozen betting experiment.

This audit never selects a new rule, fits a model, changes a study artifact or
publishes a prediction. Selected-ticket calibration is distinct from Top1 ECE.
Historical result odds are indicative inputs, not executable pre-race quotes.
"""
from __future__ import annotations

import argparse
import json
import math
from pathlib import Path

import numpy as np
import pandas as pd

import db_shared_blend46_betting as betting
import evaluate_db_shared_blend46_roi as driver


ROOT = Path(__file__).resolve().parents[1]
METHODS = ("global", "venue_only", "blend50", "logistic_blend", "lightgbm_blend")
ODDS_EDGES = (1., 3., 5., 10., 20., 30., math.inf)
DEFAULT_OUTPUT = ROOT / "reports/generated/db-shared-blend46-roi-audit-20261012.json"


def _calibration_record(rows):
    count = len(rows)
    predicted = float(rows.probability.mean()) if count else None
    actual = float(rows.hit.mean()) if count else None
    expected_gross = float((rows.stake * rows.probability * rows.odds).sum())
    gross = float(rows.payout.sum())
    return {"bets": count, "predicted_win_rate": predicted, "actual_win_rate": actual,
            "actual_over_predicted_win_rate": actual / predicted if count else None,
            "signed_calibration_bias_actual_minus_predicted": actual - predicted if count else None,
            "absolute_calibration_bias": abs(actual - predicted) if count else None,
            "stake_jpy": float(rows.stake.sum()),
            "indicative_expected_gross_jpy": expected_gross,
            "saved_netkeiba_actual_gross_jpy": gross,
            "actual_over_indicative_expected_gross": gross / expected_gross if expected_gross else None}


def selected_calibration(cohort, bets):
    """Compute the actual frozen selected-ticket cohort, not race Top1 ECE.

    Ticket outcome/odds/probability metadata is ignored. All values are joined
    from the independently validated cohort, and actual money uses saved payout.
    Multiple tickets per race are correlated: these are descriptive diagnostics,
    not independent Bernoulli observations or a formal calibration confidence band.
    """
    settlement = betting.settle_bets(cohort, bets)
    values = settlement.merge(cohort[["race_id", "horse_number", "probability", "odds"]],
                              on=["race_id", "horse_number"], how="left", validate="one_to_one")
    if not values.probability.map(math.isfinite).all() or not values.odds.map(math.isfinite).all():
        raise ValueError("roi-audit-ticket-input-join-failed")
    bins = []
    for lower, upper in zip(ODDS_EDGES[:-1], ODDS_EDGES[1:]):
        part = values.loc[values.odds.ge(lower) & values.odds.lt(upper)]
        bins.append({"odds_lower_inclusive": lower,
                     "odds_upper_exclusive": None if math.isinf(upper) else upper,
                     **_calibration_record(part)})
    return {**_calibration_record(values), "bet_races": int(values.race_id.nunique()),
            "multiple_ticket_races": int(values.groupby("race_id").size().gt(1).sum()),
            "fixed_odds_bins": bins, "top1_ece_reused_for_selected_bets": False,
            "diagnostic_only": True, "formal_calibration_band": False,
            "tickets_within_race_not_independent": True,
            "expected_money_scope": "probability times historical result odds times stake; not executable quote",
            "actual_money_scope": "saved netkeiba individual win payout; not odds-based settlement",
            "eligible_for_ev": False, "production_changed": False}


def _bounded_file(base, relative):
    candidate = base / relative
    if candidate.is_symlink():
        raise ValueError("roi-audit-symlink-forbidden")
    candidate = candidate.resolve()
    if not candidate.is_relative_to(base.resolve()) or not candidate.is_file():
        raise ValueError("roi-audit-artifact-boundary")
    return candidate


def verify_run(run):
    run = Path(run).resolve()
    if not run.is_relative_to(ROOT / "reports/generated/db-shared-blend46-roi"):
        raise ValueError("roi-audit-run-boundary")
    result = driver.read(run / "results.json")
    if result.get("status") != "completed" or result.get("schema") != "db-shared-blend46-roi-result-v1":
        raise ValueError("roi-audit-completed-study-required")
    locked = driver.read(run / "protocol.json")
    expected = driver.read(run / "protocol-hash.json")["sha256"]
    if driver.object_hash(locked) != expected or result["protocol_sha256"] != expected:
        raise ValueError("roi-audit-protocol-hash")
    manifest = driver.read(run / "artifact-manifest.json")
    if manifest["protocol_sha256"] != expected:
        raise ValueError("roi-audit-manifest-protocol")
    required = {"results.json", "protocol.json", "protocol-hash.json", "frozen-selection.json",
                "frozen-selection-hash.json"}
    if not required <= set(manifest["files"]):
        raise ValueError("roi-audit-manifest-required-files")
    for relative, receipt in manifest["files"].items():
        path = _bounded_file(run, relative)
        if path.stat().st_size != receipt["bytes"] or driver.sha(path) != receipt["sha256"]:
            raise ValueError("roi-audit-manifest-file-changed")
    for relative, expected_hash in locked["source_hashes"].items():
        path = _bounded_file(ROOT, relative)
        if driver.sha(path) != expected_hash:
            raise ValueError("roi-audit-study-code-changed")
    selections = driver.read(run / "frozen-selection.json")
    selection_hash = driver.read(run / "frozen-selection-hash.json")["sha256"]
    if driver.object_hash(selections) != selection_hash or selections != result["frozen_selections"]:
        raise ValueError("roi-audit-frozen-selection-changed")
    if any(result.get(key) is not False for key in ("models_published", "models_activated", "forward_changed",
            "independent_future_validation", "executable_odds_verified", "test_oracle_selection_performed",
            "selection_used_test_outcomes")) or result.get("actual_bets") != 0:
        raise ValueError("roi-audit-research-only-contract")
    return run, result, locked, manifest


def _same_source_cohort(left, right):
    columns = ["race_id", "race_date", "horse_number", "horse_id", "finish", "venue", "surface", "odds",
               "popularity", "tansho_payout", "expected_runner_count", "raw_score"]
    keys = ["race_id", "horse_number"]
    a = left[columns].sort_values(keys).reset_index(drop=True)
    b = right[columns].sort_values(keys).reset_index(drop=True)
    pd.testing.assert_frame_equal(a, b, check_dtype=False, check_exact=True)


def run_audit(run, output=DEFAULT_OUTPUT):
    from evaluate_db_shared_venues import preservation_receipt
    from threadpoolctl import threadpool_limits
    run, result, locked, manifest = verify_run(run)
    protocol, source, prior, snapshot, before = driver.preflight()
    if (protocol != locked["protocol"] or before != result["preservation_before"]
            or before != result["preservation_after"]):
        raise ValueError("roi-audit-original-preservation-or-protocol-mismatch")
    methods, _ranks, _returns, data_audit = driver.load_data(source, prior, snapshot)
    if data_audit != result["data_audit"]:
        raise ValueError("roi-audit-source-data-reconstruction-mismatch")
    test = {name: driver.phases(frame, protocol)["TEST"] for name, frame in methods.items()}
    base = test["blend50"]
    replay_receipts = []
    for name in ("logistic_blend", "lightgbm_blend", "logistic_market", "lightgbm_market"):
        artifact_name, parquet_name = name + ".research.json", name + "-TEST.parquet"
        if artifact_name not in manifest["files"] or parquet_name not in manifest["files"]:
            raise ValueError("roi-audit-ai-artifact-missing")
        artifact = driver.read(_bounded_file(run, artifact_name))
        frame = pd.read_parquet(_bounded_file(run, parquet_name))
        _same_source_cohort(base, frame)
        # Only source inputs are passed to the saved research model.
        with threadpool_limits(limits=2):
            replay = betting.predict_offset_model(artifact, base.drop(columns=["finish", "tansho_payout"]))
        if not np.array_equal(replay, frame.probability.to_numpy(float)):
            raise ValueError("roi-audit-ai-prediction-replay-difference")
        test[name] = frame
        replay_receipts.append({"method": name, "model_sha256": driver.sha(run / artifact_name),
            "test_frame_sha256": driver.sha(run / parquet_name), "outcome_columns_passed_to_inference": False,
            "exact_probability_replay": True, "identical_source_cohort": True})
    test["market"] = betting.replace_probabilities(base, betting.market_probabilities(base))
    policy_rows = result["policy_tables"]["TEST"]
    if len(policy_rows) != 31 or len({row["policy_id"] for row in policy_rows}) != 31:
        raise ValueError("roi-audit-full-policy-table-required")
    ledgers = []
    check_fields = ("runner_count", "race_count", "date_count", "date_from", "date_to", "bets", "hits", "stake",
                    "payout", "profit", "roi_percent", "bet_races", "race_coverage", "dates_with_bets",
                    "date_coverage", "max_drawdown_jpy", "yearly")
    for row in policy_rows:
        method, rule = ("market", "top1") if row["policy_id"] == "market_favorite" else row["policy_id"].split(":", 1)
        bets = betting.select_bets(test[method], rule)
        summary = betting.summarize_policy(test[method], bets)
        if any(summary[field] != row[field] for field in check_fields):
            raise ValueError("roi-audit-independent-ledger-mismatch")
        ledgers.append({"policy_id": row["policy_id"], "bets": summary["bets"], "stake": summary["stake"],
                       "payout": summary["payout"], "roi_percent": summary["roi_percent"], "ledger_equal": True})
    selected = []
    for method in METHODS:
        frozen = result["frozen_selections"][method]
        choice = frozen["highest_candidate"]
        if choice is None:
            selected.append({"method": method, "diagnostic_skipped": True, "reason": "no SELECT-eligible candidate"})
            continue
        prefix, rule = choice["policy_id"].split(":", 1)
        if prefix != method or frozen["test_outcomes_consumed"] is not False:
            raise ValueError("roi-audit-frozen-method-choice-mismatch")
        bets = betting.select_bets(test[method], rule)
        selected.append({"method": method, "policy_id": choice["policy_id"],
                         "selected_for_next_research": frozen["selected_policy_id"] == choice["policy_id"],
                         "choice_scope": "existing SELECT-frozen highest candidate, not retuned",
                         **selected_calibration(test[method], bets)})
    paired = []
    for kind in ("logistic", "lightgbm"):
        combo, control = kind + "_blend", kind + "_market"
        frozen = result["frozen_selections"][combo]["highest_candidate"]
        if frozen is None:
            continue
        rule = frozen["policy_id"].split(":", 1)[1]
        _same_source_cohort(test[combo], test[control])
        policies = {name: betting.select_bets(test[name], rule) for name in (combo, control)}
        with threadpool_limits(limits=2):
            direct = betting.bootstrap_policy_comparisons(base, policies, control,
                repetitions=50000, family_size=2)
        metrics = {name: betting.probability_metrics(test[name]) for name in (combo, control)}
        paired.append({"combo": combo, "market_control": control, "frozen_rule": rule,
            "nll_point_delta_combo_minus_market_control": metrics[combo]["nll"] - metrics[control]["nll"],
            "brier_point_delta_combo_minus_market_control": metrics[combo]["brier"] - metrics[control]["brier"],
            "direct_paired_bootstrap": direct,
            "claim_scope": "secondary exploratory odds-only ablation; not primary 31-policy family or future proof"})
    after = preservation_receipt()
    verify_run(run)
    if before != after or driver.sha(snapshot) != protocol["snapshot_sha256"]:
        raise ValueError("roi-audit-protected-state-or-snapshot-changed")
    payload = {"schema": "db-shared-blend46-roi-external-audit-v1", "status": "completed",
        "source_run": str(run), "source_results_sha256": driver.sha(run / "results.json"),
        "source_manifest_sha256": driver.sha(run / "artifact-manifest.json"),
        "audit_code_sha256": driver.sha(Path(__file__)), "selected_ticket_calibration": selected,
        "all_test_policy_ledger_recomputations": ledgers, "ai_replay": replay_receipts,
        "direct_combo_market_ablation": paired, "data_audit_equal": True,
        "preservation_before": before, "preservation_after": after,
        "models_refitted": False, "rules_retuned": False, "production_changed": False,
        "forward_changed": False, "actual_purchase": False, "executable_odds_verified": False,
        "audit_diagnostics_not_used_to_change_frozen_choices": True}
    output = Path(output).resolve()
    if not output.is_relative_to(ROOT / "reports/generated") or output.exists() or output.suffix != ".json":
        raise ValueError("roi-audit-new-external-output-required")
    driver.save(output, payload)
    return payload


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("run", type=Path)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    args = parser.parse_args()
    result = run_audit(args.run, args.output)
    print(json.dumps({"status": result["status"], "output": str(args.output.resolve()),
                      "selected_methods": len(result["selected_ticket_calibration"])}, ensure_ascii=False))
