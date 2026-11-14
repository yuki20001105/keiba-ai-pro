"""Approval-bound offline style/field comparison over the frozen prior cohort."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
import sqlite3
import time
from types import SimpleNamespace

import joblib
import numpy as np
import pandas as pd
from threadpoolctl import threadpool_limits

from betting import adaptive_strategy as axis
from betting.research_opponent_pairs import select_tickets, settle
from experiments.common import REPO_ROOT
from experiments.goal_loop import BASE, _other_experiments_settled
from experiments.orchestrator import driver_lock
from experiments.search import freeze_json
from training import db_context_features as context
from training import db_context_fit as orchestration
from training import db_shared_fit as legacy
from training import db_shared_grouped as grouped
from training import pace_features as features
from training.target_comparison import _protected, paired_comparison, race_metrics


SCHEMA = "keiba-pace-goal-loop-v1"
PROTOCOL = {
    "schema": SCHEMA, "goal": "Improve winner NLL and ranking with past-only style/field features; separately measure fixed-two historical ROI.",
    "groups": {name: list(columns) for name, columns in features.GROUPS.items()},
    "normalization_arm": grouped.ARM, "presets": list(legacy.PRESETS),
    "seed": 42, "workers": 1, "threads": 2, "maximum_fits": 22, "maximum_seconds": 3600,
    "bankroll_yen": 1000000, "points": 3, "stake_per_point_yen": 500,
    "axis_threshold": .65, "axis_model": "past-only expanding quarterly logistic C=1/max_iter=1500/seed=42",
    "roi_scope": "Original frozen 630 numeric-priority races; results are conditional on the old model's cohort, not whole-TEST strategy effectiveness.",
    "primary_selection": "Candidate numeric priority AND candidate-bound axis top3 >=0.65; no 2024 warmup purchases.",
    "paired_selection": "Original axis-gated race IDs; only partner ranking changes; not candidate purchase authority.",
    "minimum_nll_improvement": .005, "maximum_rank_degradation": .01,
    "maximum_top1_degradation": .005, "bootstrap_samples": 2000,
    "evaluation_label": "retrospective_known_exposed", "promotion_eligible": False,
    "no_network": True, "no_live_bets": True, "supervised_pace_forecast": False,
}


def sha(path):
    with Path(path).open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def read(path):
    return json.loads(Path(path).read_text(encoding="utf-8"))


def write(path, value):
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    Path(path).write_text(json.dumps(value, ensure_ascii=False, allow_nan=False, indent=2) + "\n",
                          encoding="utf-8")


def _sources():
    return {**grouped.source_hashes(), **context.feature_source_hashes(),
        "training.db_context_fit": sha(orchestration.__file__),
        "training.pace_features": sha(features.__file__),
        "training.pace_comparison": sha(__file__),
        "training.pace_verification": sha(Path(__file__).with_name("pace_verification.py")),
        "training.target_comparison": sha(Path(__file__).with_name("target_comparison.py")),
        "betting.adaptive_strategy": sha(axis.__file__),
        "betting.research_opponent_pairs": sha(REPO_ROOT / "python-api" / "betting" / "research_opponent_pairs.py"),
        "scripts.reproduce_adaptive_betting": sha(REPO_ROOT / "scripts" / "reproduce_adaptive_betting.py"),
        "scripts.run_pace_goal_loop": sha(REPO_ROOT / "scripts" / "run_pace_goal_loop.py")}


def _event(root, kind, detail):
    paths = sorted((root / "progress").glob("*.json"))
    freeze_json(root / "progress" / f"{len(paths):06d}.json", {
        "sequence": len(paths), "previous_sha256": sha(paths[-1]) if paths else None,
        "kind": kind, "time": time.time(), "detail": detail})


def _task(root):
    task = read(root / "task-ledger.json")
    if (task["protocol"] != PROTOCOL or task["sources"] != _sources()
            or task["protected"] != _protected()):
        raise ValueError("pace-goal-source-or-production-changed")
    if Path(task["inputs"]["features.parquet"]["path"]).resolve() != (root / "features.parquet").resolve():
        raise ValueError("pace-approved-feature-path-mismatch")
    for entry in task["inputs"].values():
        if sha(entry["path"]) != entry["sha256"]:
            raise ValueError("pace-goal-input-changed")
    previous = None
    for index, path in enumerate(sorted((root / "progress").glob("*.json"))):
        event = read(path)
        if event["sequence"] != index or event["previous_sha256"] != previous:
            raise ValueError("pace-progress-chain-invalid")
        previous = sha(path)
    return task


def check_approval(task, approval, task_sha):
    for entry in task["inputs"].values():
        if sha(entry["path"]) != entry["sha256"]:
            raise ValueError("pace-approval-input-changed")
    if (approval["task_sha256"] != task_sha or approval["production_approval"] is not False
            or approval["acquisition_approved"] is not False):
        raise ValueError("pace-approval-invalid")


def prepare(root, source, baseline_report, kit, previous):
    with driver_lock(BASE, root):
        _other_experiments_settled(root)
        if (root / "task-ledger.json").exists():
            return _task(root)
        if root.exists() and any(root.iterdir()):
            raise ValueError("unused-pace-experiment-root-required")
        root.mkdir(parents=True, exist_ok=True)
        sources, protected = _sources(), _protected()
        meta = read(baseline_report)
        if source.resolve() != Path(meta["source_cohort"]).resolve():
            raise ValueError("original-baseline-cohort-required")
        kit_manifest = read(kit / "kit-manifest.json")
        for name, digest in kit_manifest["files"].items():
            if sha(kit / name) != digest:
                raise ValueError("original-kit-input-changed")
        snapshots = list((source / "snapshots").glob("*.db"))
        if len(snapshots) != 1:
            raise ValueError("single-frozen-snapshot-required")
        snapshot = snapshots[0]
        original = {
            "target": source / "target.parquet", "original_features": source / "features.parquet",
            "snapshot": snapshot, "baseline": Path(meta["saved_artifact"]),
            "baseline_report": baseline_report, "kit_manifest": kit / "kit-manifest.json",
            "baseline_predictions": kit / "20261102-market-axis-full-test-v1" / "cohort_predictions.parquet",
            "wide": kit / "20261102-market-axis-full-test-v1" / "verified_payouts.json",
            "trifecta": kit / "20261102-concentrated-million-v1" / "verified_trifecta_payouts.json",
            "previous_features": previous / "features_and_targets.parquet",
            "previous_forecasts": previous / "forecasts.parquet",
            "previous_decisions": previous / "decision_ledger.parquet",
            "previous_protocol": previous / "protocol.json",
        }
        inputs = {name: {"path": str(path.resolve()), "sha256": sha(path)}
                  for name, path in original.items()}
        if inputs["baseline"]["sha256"] != meta["artifact_sha256"]:
            raise ValueError("original-baseline-artifact-changed")
        target, old = pd.read_parquet(original["target"]), pd.read_parquet(original["original_features"])
        if list(old.columns) != list(grouped.FEATURE_COLUMNS):
            raise ValueError("unchanged-original46-required")
        splits = legacy.date_partitions(target)
        receipts = {name: legacy._partition_receipt(target.iloc[index]) for name, index in splits.items()}
        if receipts != meta["partitions"]:
            raise ValueError("original-partitions-changed")
        print("Loading frozen prior-history population and passage evidence", flush=True)
        _, history, coverage = context.load_db_frames(
            snapshot, "2013-01", pd.to_datetime(target.race_date.max(), format="%Y%m%d").strftime("%Y-%m-%d"))
        print("Building style9 and six field proxies; preserving archived raw46", flush=True)
        extra = features.build_style_features(target, history)
        inputs_frame = pd.concat([old, extra], axis=1).loc[:, list(features.FEATURE_COLUMNS)]
        pd.testing.assert_frame_equal(inputs_frame[list(grouped.FEATURE_COLUMNS)], old, check_flags=False)
        inputs_frame.to_parquet(root / "features.parquet", index=False)
        diagnostics = {
            "historical_population": coverage, "context_audit": extra.attrs["context_audit"],
            "feature_definitions": {**{name: context.FEATURE_LABELS[name] for name in features.STYLE_COLUMNS},
                                    **features.DEFINITIONS},
            "feature_missing_rate_by_partition": {
                part: extra.iloc[index].isna().mean().to_dict() for part, index in splits.items()},
            "style_observed_runner_rate_by_partition": {
                part: float(extra.iloc[index].ctx_style_count5.gt(0).mean())
                for part, index in splits.items()},
            "pace_label_blocker": "Zero nonempty lap_sectional records in the original 32700-race cohort; S/M/H supervised forecasts are not trained.",
            "same_day_outcomes_used": False, "current_outcomes_consumed": False,
        }
        with sqlite3.connect(snapshot.resolve().as_uri() + "?mode=ro", uri=True) as db:
            lap_ids = {row[0] for row in db.execute(
                "SELECT race_id FROM races_ultimate WHERE length(json_extract(data,'$.lap_sectional'))>2")}
        diagnostics["cohort_races_with_nonempty_laps"] = len(set(target.race_id) & lap_ids)
        if diagnostics["cohort_races_with_nonempty_laps"] != 0:
            raise ValueError("pace-label-diagnosis-changed-requires-new-protocol")
        priority = pd.read_parquet(original["previous_features"])
        selected_ids = set(priority.loc[priority.numeric_priority, "race_id"])
        dividends = read(original["trifecta"])
        if len(selected_ids) != 630 or not selected_ids.issubset(dividends):
            raise ValueError("original-priority-and-official-payout-scope-required")
        diagnostics["roi_priority_races"] = 630
        diagnostics["full_test_races"] = receipts["TEST"]["race_count"]
        freeze_json(root / "diagnostics.json", diagnostics)
        for name in ("features.parquet", "diagnostics.json"):
            inputs[name] = {"path": str((root / name).resolve()), "sha256": sha(root / name)}
        task = {"schema": SCHEMA, "protocol": PROTOCOL, "inputs": inputs,
                "sources": sources, "protected": protected, "partitions": receipts,
                "approval_scope": "user-requested-offline-style-learning-and-retrospective-fixed-two-replay"}
        if sources != _sources() or protected != _protected():
            raise ValueError("pace-preparation-source-or-production-changed")
        freeze_json(root / "task-ledger.json", task)
        _event(root, "prepared", {"task_sha256": sha(root / "task-ledger.json")})
        return {"status": "awaiting_approval", "partitions": receipts,
                "task_sha256": sha(root / "task-ledger.json"), "diagnostics": diagnostics}


def approve(root, actor):
    with driver_lock(BASE, root):
        _task(root)
        if not isinstance(actor, str) or not actor or len(actor) > 100:
            raise ValueError("bounded-approval-actor-required")
        approval = {"task_sha256": sha(root / "task-ledger.json"), "actor": actor,
                    "production_approval": False, "acquisition_approved": False}
        freeze_json(root / "approval.json", approval)
        _event(root, "approved", approval)
        return {"status": "ready", **approval}


def _prepare_inputs(target, frame):
    if (list(frame.columns) != list(features.FEATURE_COLUMNS) or len(frame) != len(target)
            or not frame.index.equals(target.index)):
        raise ValueError("aligned-fixed-pace-features-required")
    raw = frame.reindex(columns=list(legacy.FEATURE_COLUMNS))
    checked, _ = legacy._prepare(target, raw)
    if not checked[["race_id", "horse_number"]].equals(target[["race_id", "horse_number"]]):
        raise ValueError("canonical-pace-position-order-required")
    return checked, frame.copy()


def fit_candidate(target, frame, splits, name, progress=None):
    def baseline(data):
        return grouped.grouping.fit_group_baseline(grouped._conditions(data), grouped.ARM)

    def teacher(data, fitted):
        return grouped.grouping.teacher(grouped._conditions(data), fitted)

    accuracy = grouped._bind(legacy.accuracy_metrics, {
        "_inverse_speed": grouped._inverse, "apply_speed_deviation_baseline": teacher})
    adapter = SimpleNamespace(**{**vars(legacy), "fit_speed_deviation_baseline": baseline,
        "apply_speed_deviation_baseline": teacher, "accuracy_metrics": accuracy,
        "_inverse_speed": grouped._inverse})
    # Same binding pattern as db_shared_grouped: no mutation of sealed helpers.
    fitted = grouped._bind(orchestration.fit_on_partitions, {
        "legacy": adapter, "prepare": _prepare_inputs, "source_hashes": _sources,
        "FEATURE_COLUMNS": features.FEATURE_COLUMNS, "feature_source_hashes": _sources,
        "FEATURE_PIPELINE": SCHEMA, "SCHEMA": SCHEMA, "__file__": __file__,
    })
    bundle, result, output = fitted(target, frame, splits, columns=features.GROUPS[name],
        n_threads=2, progress=progress, preset_parallel=False)
    bundle["probability_validation"].update(validated=False, eligible_for_ev=False,
        status="unverified", reference_only=True)
    bundle["target_normalization"] = {
        "arm": grouped.ARM, "training_only": True,
        "baseline_sha256": grouped._baseline_hash(bundle["baseline"])}
    result["target_normalization"] = bundle["target_normalization"]
    return bundle, result, output


def predict_axis(bundle, frame, base_sha):
    if bundle["base_model_sha256"] != base_sha:
        raise ValueError("axis-base-model-mismatch")
    if frame.empty or frame.race_date.min() <= bundle["fitted_through"]:
        raise ValueError("axis-history-must-be-strictly-past")
    model = bundle["model"]
    if "constant_probability" in model:
        return np.full(len(frame), model["constant_probability"])
    x = (frame[model["numeric"]].to_numpy(float) - model["mean"]) / model["scale"]
    columns = [x]
    for name, labels in zip(axis.CATEGORICAL, model["categories"]):
        columns.append(np.column_stack([frame[name].to_numpy() == label for label in labels]))
    x = np.column_stack(columns)
    if not np.isfinite(x).all():
        raise ValueError("finite-axis-inputs-required")
    logits = x @ np.asarray(model["coefficient"]).T + model["intercept"]
    return 1 / (1 + np.exp(-np.clip(logits[:, 0], -700, 700)))


def settle_book(field, dividends, *, selected):
    if selected and any(not dividends.get(kind) for kind in ("ワイド", "三連単")):
        raise ValueError("complete-official-markets-required")
    legs = select_tickets(field.to_dict("records"), axis.RECIPE_DEFINITIONS["candidate2"]) if selected else None
    return settle(legs, dividends)


def _forecast(root, name, predictions, base_sha, budget):
    rows = []
    for rid, part in predictions.groupby("race_id", sort=True):
        x = axis.race_features(part)
        favorite = part.sort_values(["odds", "horse_number"]).iloc[0]
        rows.append({"race_id": rid, "race_date": part.race_date.iloc[0], **x,
                     "axis": int(favorite.finish <= 3), "numeric_priority": axis.numeric_priority(part)})
    data = pd.DataFrame(rows)
    forecasts = []
    quarters = pd.to_datetime(data.race_date, format="%Y%m%d").dt.to_period("Q").astype(str)
    for quarter in sorted(quarters.loc[quarters.ge("2025Q1")].unique()):
        budget()
        cutoff = pd.Period(quarter, freq="Q").start_time.strftime("%Y%m%d")
        train, test = data.loc[data.race_date.lt(cutoff)], data.loc[quarters.eq(quarter)]
        with threadpool_limits(limits=1):
            model = axis._fit_model(train, "axis")
        budget()
        bundle = {"schema": "pace-model-bound-axis-v1", "base_model_sha256": base_sha,
            "fitted_through": train.race_date.max(), "training_races": len(train),
            "training_sha256": legacy._frame_digest(train[[
                "race_id", "race_date", *axis.NUMERIC, *axis.CATEGORICAL, "axis"]]),
            "model": model, "reference_only": True, "purchase_eligible": False}
        write(root / name / "axis_models" / f"{quarter}.json", bundle)
        forecast = test[["race_id", "race_date", "numeric_priority"]].copy()
        forecast["p_axis"] = predict_axis(bundle, test, base_sha)
        forecast["fit_through"] = bundle["fitted_through"]
        forecast["quarter"] = quarter
        forecasts.append(forecast)
    data.to_parquet(root / name / "axis_features.parquet", index=False)
    forecast = pd.concat(forecasts, ignore_index=True)
    forecast.to_parquet(root / name / "axis_forecasts.parquet", index=False)
    return forecast.set_index("race_id")


def _risk(rows):
    from reproduce_adaptive_betting import risk_summary

    result = risk_summary(rows)
    result["roi_percent"] = result["gross_return_rate_percent"]
    bought = rows.loc[rows.cost_yen.gt(0)]
    payout = bought.return_yen.sort_values(ascending=False)
    result["largest_race_return_yen"] = int(payout.iloc[0]) if len(payout) else 0
    result["largest_race_share_of_return_percent"] = (
        float(100 * payout.iloc[0] / payout.sum()) if len(payout) and payout.sum() else None)
    result["roi_without_largest_return_race_percent"] = (
        float(100 * (payout.sum() - payout.iloc[0]) / bought.cost_yen.sum())
        if len(payout) and bought.cost_yen.sum() else None)
    return result


def _replay(root, task, fields, forecasts):
    priority = pd.read_parquet(task["inputs"]["previous_features"]["path"])
    cohort = priority.loc[priority.numeric_priority, ["race_id", "race_date"]].copy()
    old = pd.read_parquet(task["inputs"]["previous_decisions"]["path"])
    old = old.loc[old.method.eq("candidate2")].set_index("race_id")
    wide, trifecta = read(task["inputs"]["wide"]["path"]), read(task["inputs"]["trifecta"]["path"])
    ledger = []
    for name, raw in fields.items():
        groups = {rid: part for rid, part in raw.groupby("race_id", sort=False)}
        for scope in ("model_bound", "same_old_gate"):
            for row in cohort.itertuples(index=False):
                previous = old.loc[row.race_id]
                old_enabled = bool(previous.cost_yen > 0)
                if name == "baseline" or scope == "same_old_gate":
                    selected = old_enabled
                else:
                    selected = (row.race_id in forecasts[name].index
                        and bool(forecasts[name].loc[row.race_id, "numeric_priority"])
                        and forecasts[name].loc[row.race_id, "p_axis"] >= .65)
                dividends = {**wide[row.race_id], "三連単": trifecta[row.race_id]}
                result = settle_book(groups[row.race_id], dividends, selected=selected)
                ledger.append({"model": name, "scope": scope, "race_id": row.race_id,
                    "race_date": row.race_date, "selected": bool(selected), **result})
                if name == "baseline":
                    for key in ("cost_yen", "return_yen", "ticket_count", "combinations"):
                        first, second = result[key], previous[key]
                        if isinstance(first, list):
                            second = list(second)
                        if first != second:
                            raise ValueError(f"original-fixed-two-reproduction-failed:{key}")
    ledger = pd.DataFrame(ledger)
    ledger.to_parquet(root / "decision_ledger.parquet", index=False)
    summary = {}
    for (name, scope), rows in ledger.groupby(["model", "scope"], sort=True):
        summary[f"{name}__{scope}"] = {
            period: _risk(rows.loc[rows.race_date.ge("20250101")] if period == "2025_2026"
                          else rows.loc[rows.race_date.str.startswith(period)])
            for period in ("2025_2026", "2025", "2026")}
    baseline = summary["baseline__model_bound"]["2026"]
    if (baseline["bet_races"] != 150 or baseline["net_yen"] != 500650
            or abs(baseline["roi_percent"] - 322.5111111111111) > 1e-9):
        raise ValueError("original-2026-fixed-two-money-mismatch")
    return summary


def run(root):
    with driver_lock(BASE, root), threadpool_limits(limits=2):
        _other_experiments_settled(root)
        task = _task(root)
        check_approval(task, read(root / "approval.json"), sha(root / "task-ledger.json"))
        if (root / "run-state.json").exists():
            raise RuntimeError("pace-run-already-started-no-automatic-retry")
        import psutil
        if psutil.virtual_memory().available < 6 * 1024**3:
            raise RuntimeError("insufficient-research-memory-headroom")
        started = time.monotonic()

        def budget(*_args):
            if time.monotonic() - started > PROTOCOL["maximum_seconds"]:
                raise TimeoutError("pace-budget-exhausted")

        write(root / "run-state.json", {"status": "running", "started_at": time.time()})
        _event(root, "started", {"maximum_fits": 22, "maximum_seconds": 3600})
        target = pd.read_parquet(task["inputs"]["target"]["path"])
        inputs = pd.read_parquet(task["inputs"]["features.parquet"]["path"])
        splits = legacy.date_partitions(target)
        baseline = pd.read_parquet(task["inputs"]["baseline_predictions"]["path"])
        fields, forecasts, results = {"baseline": baseline}, {}, {}
        metrics, reference_races = race_metrics(baseline, baseline.score, baseline.probability)
        reference_races.to_parquet(root / "baseline_race_metrics.parquet", index=False)
        results["baseline"] = {"feature_count": 46, "metrics": metrics,
            "source_model_sha256": task["inputs"]["baseline"]["sha256"],
            "refitted": False}
        for name in features.GROUPS:
            budget()
            print(f"Fitting {name}: {len(features.GROUPS[name])} features, original TRAIN/CAL/TEST", flush=True)
            bundle, fit, output = fit_candidate(target, inputs, splits, name, progress=budget)
            folder = root / name
            folder.mkdir(parents=True, exist_ok=True)
            if fit["partitions"] != task["partitions"]:
                raise ValueError("candidate-partitions-changed")
            model_path = folder / "model.joblib"
            joblib.dump(bundle, model_path, compress=3)
            saved = joblib.load(model_path)
            encoded = saved["encoder"].transform(inputs.iloc[splits["TEST"]][list(features.GROUPS[name])])
            predicted = legacy._finite_predictions(saved["model"], encoded, 2)
            np.testing.assert_allclose(predicted, output["scores"], rtol=0, atol=0)
            prediction = output["target"].copy()
            prediction["score"], prediction["probability"] = output["scores"], output["probabilities"]
            if not prediction[["race_id", "horse_number"]].equals(baseline[["race_id", "horse_number"]]):
                raise ValueError("candidate-and-baseline-test-identity-mismatch")
            prediction.to_parquet(folder / "predictions.parquet", index=False)
            write(folder / "fit.json", fit)
            importance = pd.DataFrame({"feature": saved["model"].feature_name(),
                "gain": saved["model"].feature_importance("gain"),
                "split": saved["model"].feature_importance("split")})
            importance.sort_values("gain", ascending=False).to_csv(folder / "feature_importance.csv", index=False)
            measured, races = race_metrics(prediction, prediction.score, prediction.probability)
            races.to_parquet(folder / "race_metrics.parquet", index=False)
            results[name] = {"feature_count": len(features.GROUPS[name]), "metrics": measured,
                "physical_rmse_mps": fit["evaluation"]["primary"]["rmse"],
                "model_sha256": sha(model_path), "selected_preset": fit["selected_preset"],
                "paired_against_baseline": paired_comparison(reference_races, races)}
            fields[name] = prediction
            print(f"Fitting strictly-past quarterly axis forecasts for {name}", flush=True)
            forecasts[name] = _forecast(root, name, prediction, sha(model_path), budget)
            _event(root, "candidate_completed", {"name": name, "model_sha256": sha(model_path)})
        print("Replaying original fixed-two books and matching the published baseline", flush=True)
        budget()
        summary = _replay(root, task, fields, forecasts)
        budget()
        scope = set(pd.read_parquet(task["inputs"]["previous_features"]["path"]).query("numeric_priority").race_id)
        outside = {}
        for name in features.GROUPS:
            data = pd.read_parquet(root / name / "axis_features.parquet")
            outside[name] = int((data.numeric_priority & ~data.race_id.isin(scope)
                                & data.race_date.ge("20250101")).sum())
        report = {"schema": SCHEMA, "protocol": PROTOCOL, "accuracy": results,
            "fixed_two": summary, "new_numeric_candidates_outside_old_roi_scope": outside,
            "diagnostics": read(root / "diagnostics.json"), "elapsed_seconds": time.monotonic() - started,
            "model_registered": False, "active_model_changed": False,
            "production_eligible": False, "live_effectiveness_confirmed": False}
        write(root / "report.json", report)
        _task(root)
        budget()
        write(root / "run-state.json", {"status": "completed", "elapsed_seconds": time.monotonic() - started})
        _event(root, "completed", {"report_sha256": sha(root / "report.json")})
        manifest = {str(path.relative_to(root)): sha(path) for path in sorted(root.rglob("*"))
                    if path.is_file() and path.name != "artifact-manifest.json"}
        freeze_json(root / "artifact-manifest.json", {"schema": SCHEMA, "files": manifest})
        return report
