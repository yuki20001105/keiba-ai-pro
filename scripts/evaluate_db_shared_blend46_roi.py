"""Isolated historical return study of frozen46 blend predictions and small meta models."""
from __future__ import annotations

import argparse
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
import hashlib
import json
import math
import os
from pathlib import Path
import sqlite3
import time
import uuid

for variable in ("OMP_NUM_THREADS", "MKL_NUM_THREADS", "OPENBLAS_NUM_THREADS"):
    os.environ[variable] = "2"

ROOT = Path(__file__).resolve().parents[1]
CONFIG = ROOT / "config/research/db-shared-blend46-roi-20261012.json"
OUTPUT = ROOT / "reports/generated/db-shared-blend46-roi"


def sha(path):
    digest = hashlib.sha256()
    with Path(path).open("rb") as stream:
        while block := stream.read(1024 * 1024):
            digest.update(block)
    return digest.hexdigest()


def read(path):
    return json.loads(Path(path).read_text(encoding="utf-8"))


def save(path, value):
    with Path(path).open("x", encoding="utf-8") as stream:
        json.dump(value, stream, ensure_ascii=False, indent=2, allow_nan=False)


def object_hash(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False,
                                    separators=(",", ":"), allow_nan=False).encode()).hexdigest()


def preflight():
    from evaluate_db_shared_venues import preservation_receipt
    protocol = read(CONFIG)
    source = (ROOT / protocol["prediction_run"]).resolve()
    if not source.is_relative_to(ROOT / "reports/generated") or source.is_symlink():
        raise ValueError("roi-source-boundary")
    if sha(source / "results.json") != protocol["prediction_results_sha256"]:
        raise ValueError("roi-source-result-hash")
    result = read(source / "results.json")
    if (result.get("status") != "completed" or result.get("roi_evaluated") is not False
            or result.get("models_research_only") is not True or len(result["fold_results"]) != 11):
        raise ValueError("roi-frozen-prediction-contract")
    manifest = read(source / "artifact-manifest.json")
    for relative, receipt in manifest["files"].items():
        path = (source / relative).resolve()
        if (not path.is_relative_to(source) or path.is_symlink()
                or path.stat().st_size != receipt["bytes"] or sha(path) != receipt["sha256"]):
            raise ValueError("roi-source-manifest-mismatch")
    locked = read(source / "protocol.json")
    for relative, expected in locked["source_manifest"].items():
        if sha(ROOT / relative) != expected:
            raise ValueError("roi-old-study-source-changed")
    snapshot = ROOT / "keiba/data/model_retrain_snapshots" / (protocol["snapshot_sha256"] + ".db")
    if sha(snapshot) != protocol["snapshot_sha256"] or any(Path(str(snapshot) + suffix).exists() for suffix in ("-wal", "-shm", "-journal")):
        raise ValueError("roi-snapshot-hash-or-journal")
    before = preservation_receipt()
    if before != result["preservation_after"]:
        raise ValueError("roi-protected-state-not-original")
    return protocol, source, result, snapshot, before


def load_data(source, result, snapshot):
    import numpy as np
    import pandas as pd
    frames = []
    artifact_receipts = []
    for worker in result["fold_results"]:
        if worker["device"] != "cpu" or worker["feature_count"] != 46:
            raise ValueError("roi-primary-cpu46-required")
        receipt = worker["artifacts"]["runners"]
        path = Path(receipt["path"]).resolve()
        if not path.is_relative_to(source / "primary") or sha(path) != receipt["sha256"]:
            raise ValueError("roi-runner-artifact-binding")
        frame = pd.read_parquet(path)
        if len(frame) != receipt["rows"]:
            raise ValueError("roi-runner-count-binding")
        frame = frame.loc[frame.split.eq("TEST")].copy()
        year = int(worker["fold"]["test_year"])
        if (not frame.race_date.astype(str).str[:4].eq(str(year)).all()
                or worker["partitions"]["TRAIN"]["date_to"] >= frame.race_date.min()
                or worker["partitions"]["CAL"]["date_to"] >= frame.race_date.min()):
            raise ValueError("roi-base-training-or-calibration-boundary")
        frames.append(frame)
        artifact_receipts.append({"year": year, "path": str(path), "sha256": receipt["sha256"]})
    predictions = pd.concat(frames, ignore_index=True)
    if predictions.duplicated(["strategy", "race_id", "horse_number"]).any():
        raise ValueError("roi-duplicate-prediction-including-cal")
    base = predictions.loc[predictions.strategy.eq("blend50")].copy().reset_index(drop=True)
    if len(base) != 453857 or base.race_id.nunique() != 32519:
        raise ValueError("roi-full-source-cohort-required")
    keys = ["race_id", "horse_number"]
    race_ids = sorted(base.race_id.unique())
    raw_rows, payout_parts = [], []
    connection = sqlite3.connect(snapshot.as_uri() + "?mode=ro", uri=True)
    try:
        connection.execute("PRAGMA query_only=ON")
        for start in range(0, len(race_ids), 500):
            batch = race_ids[start:start + 500]
            query = ",".join("?" for _ in batch)
            for race_id, data in connection.execute(f"SELECT race_id,data FROM race_results_ultimate WHERE race_id IN ({query})", batch):
                item = json.loads(data)
                raw_rows.append({"race_id": str(race_id), "horse_number": int(item["horse_number"]),
                    "source_horse_id": str(item.get("horse_id", "")),
                    "source_finish": int(item["finish_position"]), "odds": item.get("odds"),
                    "popularity": item.get("popularity"), "source_shutuba": item.get("_shutuba", False)})
            payout_parts.append(pd.read_sql_query(
                f"SELECT race_id,bet_type,combinations,payout FROM return_tables_ultimate WHERE race_id IN ({query})",
                connection, params=batch))
    finally:
        connection.close()
    raw = pd.DataFrame(raw_rows)
    if raw.duplicated(keys).any():
        raise ValueError("roi-duplicate-source-runner")
    joined = base.merge(raw, how="left", on=keys, validate="one_to_one", indicator=True)
    if (not joined._merge.eq("both").all() or not joined.finish.eq(joined.source_finish).all()
            or not joined.horse_id.astype(str).eq(joined.source_horse_id).all()
            or joined.source_shutuba.eq(True).any()):
        raise ValueError("roi-result-source-join-conflict")
    returns = pd.concat(payout_parts, ignore_index=True)
    returns["bet_type"] = returns.bet_type.replace({"3連複": "三連複", "3連単": "三連単"})
    if returns.duplicated(["race_id", "bet_type", "combinations"]).any():
        raise ValueError("roi-duplicate-return-key")
    win = returns.loc[returns.bet_type.eq("単勝")].copy()
    if not win.combinations.astype(str).str.fullmatch(r"[1-9]\d?").all():
        raise ValueError("roi-invalid-win-payout-horse-key")
    win["horse_number"] = win.combinations.astype(int)
    if not (win.payout.ge(100) & win.payout.mod(10).eq(0)).all():
        raise ValueError("roi-invalid-win-payout")
    winners = base.loc[base.finish.eq(1)].groupby("race_id").horse_number.agg(lambda values: frozenset(values))
    payout_winners = win.groupby("race_id").horse_number.agg(lambda values: frozenset(values))
    confirmed = [race_id for race_id in race_ids if race_id in payout_winners and winners[race_id] == payout_winners[race_id]]
    unknown = sorted(set(race_ids) - set(confirmed))
    conflict = [race_id for race_id in unknown if race_id in payout_winners]
    if conflict:
        raise ValueError("roi-payout-winner-key-set-conflict")
    odds = pd.to_numeric(joined.odds, errors="coerce").astype(float)
    bad_market = sorted(joined.loc[~np.isfinite(odds) | odds.lt(1), "race_id"].unique())
    joined["odds"] = odds
    joined = joined.merge(win[keys + ["payout"]].rename(columns={"payout": "tansho_payout"}), how="left", on=keys, validate="one_to_one")
    joined["expected_runner_count"] = joined.groupby("race_id").horse_number.transform("size")
    joined["race_payout_complete"] = joined.race_id.isin(confirmed)
    dead = sorted(winners.loc[winners.map(len).ne(1)].index)
    missing_probability = sorted(joined.loc[~np.isfinite(joined.win_probability), "race_id"].unique())
    # Dead-heat predictions may be finite; it is their single-winner metrics,
    # not necessarily the saved runner probabilities, that are ineligible.
    if set(missing_probability) - set(dead):
        raise ValueError("roi-unexpected-base-probability-missing")
    excluded = set(dead) | set(unknown) | set(bad_market)
    allowed = joined.loc[~joined.race_id.isin(excluded)].copy().rename(columns={"win_probability": "probability"})
    methods = {}
    required = ["race_id", "race_date", "horse_number", "horse_id", "finish", "venue", "surface",
                "odds", "popularity", "tansho_payout", "expected_runner_count", "probability", "raw_score"]
    for strategy in ("global", "venue_only", "blend50"):
        scores = predictions.loc[predictions.strategy.eq(strategy), keys + ["win_probability", "raw_score"]]
        if set(map(tuple, scores[keys].to_numpy())) != set(map(tuple, base[keys].to_numpy())):
            raise ValueError("roi-full-method-key-cohort-mismatch")
        frame = allowed.drop(columns=["probability", "raw_score"]).merge(scores, on=keys, how="left", validate="one_to_one")
        frame = frame.rename(columns={"win_probability": "probability"})[required].sort_values(["race_date", *keys]).reset_index(drop=True)
        if not frame.probability.map(math.isfinite).all():
            raise ValueError("roi-common-method-cohort")
        methods[strategy] = frame
    from db_shared_blend46_betting import validate_cohort
    receipts = {strategy: validate_cohort(frame) for strategy, frame in methods.items()}
    if len({object_hash(receipt) for receipt in receipts.values()}) != 1:
        raise ValueError("roi-method-period-count-mismatch")
    year_audit = []
    for year, group in joined.groupby(joined.race_date.str[:4]):
        ids = set(group.race_id)
        year_audit.append({"year": int(year), "input_races": len(ids), "input_runners": len(group),
            "payout_confirmed_races": len(ids & set(confirmed)), "dead_heat_races": len(ids & set(dead)),
            "common_roi_races": len(ids - excluded)})
    audit = {"prediction_artifacts": artifact_receipts, "input_races": len(race_ids), "input_runners": len(base),
        "payout_confirmed_races": len(confirmed), "pending_payout_race_ids": unknown,
        "whole_dead_heat_race_ids": dead, "whole_missing_market_race_ids": bad_market,
        "common_cohort": receipts["blend50"], "yearly": year_audit,
        "payout_source": "saved_netkeiba_result_return_table", "payout_join": "race_id + horse_number; all winner keys checked",
        "payout_independently_verified_with_jra": False, "odds_observed_before_start_verified": False,
        "odds_settlement_substitution": False, "snapshot_opened_read_only": True,
        "unknown_payout_replaced_with_zero": False, "roi_scope": "confirmed common subset; exclusions retained above"}
    return methods, joined, returns, audit


def phases(frame, protocol):
    return {name: frame.loc[frame.race_date.between(*bounds)].copy().reset_index(drop=True)
            for name, bounds in protocol["phases"].items()}


def compact_summary(summary):
    return {key: value for key, value in summary.items() if key != "daily_ledger"}


def execute():
    start = time.perf_counter()
    protocol, source, prior, snapshot, before = preflight()
    import numpy as np
    import pandas as pd
    from threadpoolctl import threadpool_limits
    import db_shared_blend46_betting as betting
    import db_shared_blend46_ticket_diagnostics as tickets
    from evaluate_db_shared_venues import preservation_receipt
    run = OUTPUT / (datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ") + "-" + uuid.uuid4().hex[:8])
    run.mkdir(parents=True, exist_ok=False)
    code = [Path(__file__), CONFIG, Path(betting.__file__), Path(tickets.__file__)]
    pins = {path.relative_to(ROOT).as_posix(): sha(path) for path in code}
    locked = {"protocol": protocol, "source_hashes": pins, "prediction_results_sha256": sha(source / "results.json"),
        "prediction_manifest_sha256": sha(source / "artifact-manifest.json"), "preservation_before": before}
    save(run / "protocol.json", locked)
    save(run / "protocol-hash.json", {"sha256": object_hash(locked)})
    def progress(stage, **extra):
        print(json.dumps({"stage": stage, "elapsed_seconds": time.perf_counter() - start, **extra}), flush=True)
    try:
        progress("read_only_market_payout_join")
        methods, all_ranks, payout_table, audit = load_data(source, prior, snapshot)
        save(run / "data-audit.json", audit)
        split = {name: phases(frame, protocol) for name, frame in methods.items()}
        models, timings = {}, {}
        def fit_one(kind, market_only):
            name = kind + ("_market" if market_only else "_blend")
            clock = time.perf_counter()
            artifact = betting.fit_offset_model(split["blend50"]["TRAIN"], split["blend50"]["CAL"], kind=kind, market_only=market_only)
            save(run / (name + ".research.json"), artifact)
            return name, artifact, time.perf_counter() - clock
        progress("parallel_ai_fit", cpu_workers=3, threads_per_worker=2)
        with threadpool_limits(limits=2), ThreadPoolExecutor(max_workers=3) as pool:
            futures = [pool.submit(fit_one, kind, market) for kind in ("logistic", "lightgbm") for market in (False, True)]
            for future in as_completed(futures):
                name, artifact, duration = future.result()
                models[name], timings[name] = artifact, duration
                progress("ai_fit_completed", method=name, seconds=duration)
        # Only inference is allowed on SELECT/TEST after this point; no refit.
        for name, artifact in models.items():
            split[name] = {}
            for phase in ("SELECT", "TEST"):
                original = split["blend50"][phase]
                p = betting.predict_offset_model(artifact, original.drop(columns=["finish", "tansho_payout"]))
                serialized = read(run / (name + ".research.json"))
                replay = betting.predict_offset_model(serialized, original.iloc[:0] if original.empty else original.drop(columns=["finish", "tansho_payout"]))
                if not np.array_equal(p, replay):
                    raise ValueError("roi-ai-serialization-parity")
                split[name][phase] = betting.replace_probabilities(original, p)
                split[name][phase].to_parquet(run / f"{name}-{phase}.parquet", index=False)
        for phase in ("SELECT", "TEST"):
            base = split["blend50"][phase]
            market = betting.replace_probabilities(base, betting.market_probabilities(base))
            split.setdefault("market", {})[phase] = market
        selections, probability, policy_tables, bootstrap = {}, {}, {}, {}
        family_methods = [*protocol["base_model_methods"], *protocol["ai_methods"]]
        selection_policies = {method + ":" + rule: betting.select_bets(split[method]["SELECT"], rule)
            for method in family_methods for rule in protocol["rules"]}
        selection_policies["market_favorite"] = betting.select_bets(split["market"]["SELECT"], "top1")
        progress("selection_bootstrap", policy_count=len(selection_policies), repetitions=protocol["bootstrap_repetitions"])
        with threadpool_limits(limits=2):
            boot_select = betting.bootstrap_policy_comparisons(split["blend50"]["SELECT"], selection_policies,
                protocol["reference"], repetitions=protocol["bootstrap_repetitions"], family_size=31)
        bootstrap["SELECT"] = boot_select
        selections["overall"] = betting.choose_policy_on_select(split["blend50"]["SELECT"], selection_policies, boot_select)
        for method in family_methods:
            selected = {key: value for key, value in selection_policies.items() if key.startswith(method + ":")}
            family_boot = {**boot_select, "candidates": [record for record in boot_select["candidates"] if record["policy_id"] in selected]}
            selections[method] = betting.choose_policy_on_select(split["blend50"]["SELECT"], selected, family_boot)
        blend_candidate = selections["blend50"]["highest_candidate"]
        diagnostic_blend_rule = blend_candidate["policy_id"].split(":", 1)[1] if blend_candidate else "top1"
        selections["diagnostic_reference"] = {"method": "blend50", "rule": diagnostic_blend_rule,
            "minimum_coverage_fallback_top1": blend_candidate is None, "purchase_recommendation": False}
        # Freeze choices before opening policy returns or model metrics on TEST.
        save(run / "frozen-selection.json", selections)
        save(run / "frozen-selection-hash.json", {"sha256": object_hash(selections)})
        progress("choices_frozen_test_evaluation")
        test_policies = {method + ":" + rule: betting.select_bets(split[method]["TEST"], rule)
            for method in family_methods for rule in protocol["rules"]}
        test_policies["market_favorite"] = betting.select_bets(split["market"]["TEST"], "top1")
        with threadpool_limits(limits=2):
            bootstrap["TEST"] = betting.bootstrap_policy_comparisons(split["blend50"]["TEST"], test_policies,
                protocol["reference"], repetitions=protocol["bootstrap_repetitions"], family_size=31)
        for phase, policies in (("SELECT", selection_policies), ("TEST", test_policies)):
            probability[phase] = {method: betting.probability_metrics(frame[phase]) for method, frame in split.items() if phase in frame}
            policy_tables[phase] = [{"policy_id": record["policy_id"], **compact_summary(record["summary"]),
                "roi_ci95": record["roi_ci95"], "roi_ci_simultaneous": record["roi_ci_simultaneous"],
                "paired_profit_difference_ci95_vs_market": record["paired_profit_difference_ci95"],
                "payout_sensitivity": betting.payout_sensitivity(split["blend50"][phase], policies[record["policy_id"]])}
                for record in bootstrap[phase]["candidates"]]
        all_year_baselines = []
        for method, frame in methods.items():
            bets = betting.select_bets(frame, "top1")
            all_year_baselines.append({"method": method, **compact_summary(betting.summarize_policy(frame, bets))})
        # Diagnostic odds-only controls inherit each AI's SELECT-frozen rule; never optimized on TEST.
        control_summaries, paired_diagnostics = [], {}
        diagnostic_policies = {"blend50_fixed": test_policies["blend50:" + diagnostic_blend_rule]}
        for kind in ("logistic", "lightgbm"):
            combo, control = kind + "_blend", kind + "_market"
            choice = selections[combo]["highest_candidate"]
            if choice is None:
                continue
            rule = choice["policy_id"].split(":", 1)[1]
            for method in (combo, control):
                bets = betting.select_bets(split[method]["TEST"], rule)
                diagnostic_policies[method + ":" + rule] = bets
                control_summaries.append({"method": method, "frozen_rule": rule,
                    **compact_summary(betting.summarize_policy(split[method]["TEST"], bets)),
                    "payout_sensitivity": betting.payout_sensitivity(split[method]["TEST"], bets),
                    "selection_scope": "combo AI's SELECT-frozen choice; market control not independently rule-selected"})
        with threadpool_limits(limits=2):
            paired_diagnostics = betting.bootstrap_policy_comparisons(split["blend50"]["TEST"], diagnostic_policies,
                "blend50_fixed", repetitions=protocol["bootstrap_repetitions"], family_size=31)
        diagnostic = {}
        # Price-free multi-ticket-type diagnostics, no ROI selection or fitted probabilities.
        for phase, bounds in {"ALL": ("20160101", "20260913"), "TEST": protocol["phases"]["TEST"]}.items():
            rank_frame = all_ranks.loc[all_ranks.race_date.between(*bounds) & all_ranks.race_payout_complete].copy()
            diagnostic[phase] = tickets.diagnose_rank_tickets(rank_frame, payout_table.loc[payout_table.race_id.isin(rank_frame.race_id)])
        after = preservation_receipt()
        if before != after or sha(snapshot) != protocol["snapshot_sha256"]:
            raise ValueError("roi-protected-state-or-snapshot-changed")
        if any(sha(ROOT / path) != expected for path, expected in pins.items()) or sha(source / "results.json") != protocol["prediction_results_sha256"]:
            raise ValueError("roi-source-changed")
        result = {"schema": "db-shared-blend46-roi-result-v1", "status": "completed", "data_audit": audit,
            "protocol_sha256": object_hash(locked), "phases": {name: betting.validate_cohort(frame) for name, frame in split["blend50"].items()},
            "all_year_top1_baselines": all_year_baselines, "probability_metrics": probability,
            "policy_tables": policy_tables, "frozen_selections": selections,
            "control_diagnostics": control_summaries, "paired_control_diagnostics": paired_diagnostics,
            "rank_ticket_diagnostics": diagnostic,
            "ai_fit_seconds": timings, "ai_model_metadata": {name: {key: value for key, value in artifact.items()
                if key not in ("model_text", "means", "standard_deviations", "coefficients")} for name, artifact in models.items()},
            "preservation_before": before, "preservation_after": after,
            "elapsed_seconds": time.perf_counter() - start, "actual_bets": 0,
            "models_published": False, "models_activated": False, "forward_changed": False,
            "independent_future_validation": False, "executable_odds_verified": False,
            "test_oracle_selection_performed": False, "selection_used_test_outcomes": False}
        save(run / "results.json", result)
        save(run / "bootstrap-select.json", bootstrap["SELECT"])
        save(run / "bootstrap-test.json", bootstrap["TEST"])
        paths = {path.relative_to(run).as_posix(): {"sha256": sha(path), "bytes": path.stat().st_size}
            for path in run.rglob("*") if path.is_file()}
        save(run / "artifact-manifest.json", {"protocol_sha256": object_hash(locked), "files": paths})
        progress("completed", output=str(run), seconds=result["elapsed_seconds"])
        return result
    except BaseException as exc:
        save(run / "failure.json", {"error": type(exc).__name__ + ": " + str(exc),
            "elapsed_seconds": time.perf_counter() - start, "preservation_after": preservation_receipt()})
        raise


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--execute", action="store_true")
    args = parser.parse_args()
    if args.execute:
        execute()
    else:
        protocol, source, result, snapshot, before = preflight()
        print(json.dumps({"status": "preflight_passed", "source": str(source),
                          "active_model": before["active_model_id"], "phases": protocol["phases"]}))
