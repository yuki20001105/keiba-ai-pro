"""Bounded saved-data workflow replay; NOT a live release or profitability gate.

Provider observations are read from the real inventory through mode=ro. All
SQLite writes and training protocol exercises are confined to a fresh OS temp
directory. No network, fit, model activation, purchase, API restart or research
ledger mutation is allowed. Settlement choices are deterministic horse-number
fixtures, NOT predictions or probability/calibration evidence.
"""
from __future__ import annotations

import argparse
from collections import Counter
from contextlib import closing, contextmanager, redirect_stdout
from datetime import datetime, timezone
import hashlib
import importlib
import io
import json
import logging
from pathlib import Path
import re
import shutil
import socket
import sqlite3
import subprocess
import sys
import tempfile
import time
import types
from typing import Any
from unittest.mock import patch
from urllib.parse import parse_qs, unquote, urlsplit
import uuid

ROOT = Path(__file__).resolve().parents[1]
REPORT_ROOT = ROOT / "reports" / "generated" / "local-workflow-proof"
SOURCE = ROOT / "keiba" / "data" / "keiba_ultimate.db"
OWNER = "11111111-1111-4111-8111-111111111111"
PROOF_OWNER = "isolated-workflow-proof"
TYPES = {"単勝": 1, "馬連": 2, "ワイド": 2, "馬単": 2, "三連複": 3, "三連単": 3}
RUNTIME_SOURCES = (
    "scripts/verify_local_workflow.py", "python-api/scraping/storage.py",
    "python-api/scraping/quality.py", "python-api/scraping/operational_saga_runtime.py",
    "python-api/training/local_retrain.py", "python-api/training/eligibility.py",
    "python-api/training/approved_execution.py", "python-api/training/execution_bundle.py",
    "python-api/training/retrain_worker.py", "keiba/keiba_ai/db_ultimate_loader.py",
    "python-api/betting/performance.py", "python-api/betting/value_policy.py",
    "python-api/experiments/dataset.py", "python-api/experiments/features.py",
    "python-api/experiments/training.py", "python-api/experiments/evaluation.py",
    "keiba/keiba_ai/speed_deviation.py", "keiba/keiba_ai/constants.py",
)


def digest(value: Any) -> str:
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True,
        separators=(",", ":"), allow_nan=False).encode("utf-8")).hexdigest()


def file_digest(path: Path) -> str:
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


@contextmanager
def isolated_io(workspace: Path, source: Path):
    """Defense in depth for these fixed Python paths, not an OS sandbox."""
    workspace, source = workspace.resolve(), source.resolve(strict=True)
    original = sqlite3.connect
    counters = {"network_denied": 0, "sqlite_denied": 0, "source_ro_connections": 0}

    def deny_network(*_args, **_kwargs):
        counters["network_denied"] += 1
        raise PermissionError("Workflow replay cannot access the network")

    def connect(database, *args, **kwargs):
        text = str(database)
        if text == ":memory:":
            return original(database, *args, **kwargs)
        is_uri = kwargs.get("uri") is True and text.startswith("file:")
        parsed = urlsplit(text) if is_uri else None
        raw = unquote(parsed.path) if parsed else text
        # file:///C:/... is the standard Path.as_uri form on Windows.
        if sys.platform == "win32" and re.match(r"^/[A-Za-z]:/", raw):
            raw = raw[1:]
        path = Path(raw).resolve()
        readonly = bool(parsed and parse_qs(parsed.query).get("mode") == ["ro"])
        allowed = readonly if path == source else path.is_relative_to(workspace)
        if not allowed:
            counters["sqlite_denied"] += 1
            raise PermissionError("SQLite target is outside the isolated replay")
        connection = original(database, *args, **kwargs)
        if readonly:
            connection.execute("PRAGMA query_only=ON")
            if path == source:
                counters["source_ro_connections"] += 1
        return connection

    with patch.object(socket.socket, "connect", deny_network), \
         patch.object(socket.socket, "connect_ex", deny_network), \
         patch.object(socket.socket, "sendto", deny_network), \
         patch.object(socket, "create_connection", deny_network), \
         patch.object(socket, "getaddrinfo", deny_network), \
         patch.object(sqlite3, "connect", connect):
        yield counters


@contextmanager
def readonly(path: Path):
    database = sqlite3.connect(path.resolve().as_uri() + "?mode=ro", uri=True, timeout=10)
    database.row_factory = sqlite3.Row
    database.execute("PRAGMA query_only=ON")
    database.execute("BEGIN")
    try:
        yield database
    finally:
        database.close()


def load_source(source: Path, limit: int) -> tuple[list[dict], dict]:
    if type(limit) is not int or not 1 <= limit <= 32:
        raise ValueError("Replay limit must be between 1 and 32")
    with readonly(source) as database:
        inventory = {
            "races": database.execute("SELECT COUNT(*) FROM races_ultimate").fetchone()[0],
            "runner_rows": database.execute("SELECT COUNT(*) FROM race_results_ultimate").fetchone()[0],
            "dividend_rows": database.execute("SELECT COUNT(*) FROM return_tables_ultimate").fetchone()[0],
        }
        candidates = database.execute("""
            SELECT r.race_id,r.data FROM races_ultimate r
            WHERE (r.race_id LIKE '2021%' OR r.race_id LIKE '2022%')
              AND EXISTS (SELECT 1 FROM return_tables_ultimate d WHERE d.race_id=r.race_id)
            ORDER BY r.race_id LIMIT ?
        """, (limit,)).fetchall()
        sources = []
        for record in candidates:
            info = json.loads(record["data"])
            horses = []
            for row in database.execute("SELECT data FROM race_results_ultimate WHERE race_id=? ORDER BY id", (record["race_id"],)):
                item = json.loads(row["data"])
                horses.extend(item if isinstance(item, list) else [item])
            dividends = [dict(row) for row in database.execute(
                "SELECT bet_type,combinations,payout,popularity FROM return_tables_ultimate WHERE race_id=? ORDER BY id", (record["race_id"],))]
            sources.append({"race_info": info, "horses": horses, "return_tables": dividends})
        inventory.update(
            selection="first race_id-ordered 2021–2022 races with saved dividends; no model/ROI selection",
            selected_races=len(sources), selected_runner_rows=sum(len(s["horses"]) for s in sources),
            source_rows_sha256=digest(sources),
        )
        return sources, inventory


def load_runtime_modules():
    api = str(ROOT / "python-api")
    if api not in sys.path:
        sys.path.insert(0, api)
    # Storage only consumes logger. Avoid app_config's environment/auth/logger
    # initialization; the actual storage/quality functions remain unchanged.
    logger_dependency = types.ModuleType("app_config")
    logger_dependency.logger = logging.getLogger("isolated-workflow-proof")
    with patch.dict(sys.modules, {"app_config": logger_dependency}):
        storage = importlib.import_module("scraping.storage")
    return (
        storage,
        importlib.import_module("scraping.quality"),
        importlib.import_module("scraping.operational_saga_runtime"),
        importlib.import_module("training.local_retrain"),
        importlib.import_module("training.eligibility"),
        importlib.import_module("betting.performance"),
    )


def replay_storage(workspace: Path, sources: list[dict], storage, quality) -> tuple[Path, dict]:
    path = workspace / "inventory.db"
    storage._init_sqlite_db(path)
    reports = []
    for source in sources:
        classified = quality.classify_race_quality(source).as_dict()
        before = digest(source)
        assert storage._save_race_sqlite_only(source, path, overwrite=True)
        assert storage._save_race_sqlite_only(source, path, overwrite=True)
        assert digest(source) == before
        reports.append(classified)
    with readonly(path) as database:
        counts = {
            "races": database.execute("SELECT COUNT(*) FROM races_ultimate").fetchone()[0],
            "runners": database.execute("SELECT COUNT(*) FROM race_results_ultimate").fetchone()[0],
            "dividends": database.execute("SELECT COUNT(*) FROM return_tables_ultimate").fetchone()[0],
        }
        actual = []
        for source in sources:
            rid = source["race_info"]["race_id"]
            info = json.loads(database.execute("SELECT data FROM races_ultimate WHERE race_id=?", (rid,)).fetchone()[0])
            horses = [json.loads(row[0]) for row in database.execute("SELECT data FROM race_results_ultimate WHERE race_id=? ORDER BY id", (rid,))]
            dividends = [dict(row) for row in database.execute("SELECT bet_type,combinations,payout,popularity FROM return_tables_ultimate WHERE race_id=? ORDER BY id", (rid,))]
            actual.append({"race_info": info, "horses": horses, "return_tables": dividends})
    equality = digest(actual) == digest(sources)
    assert equality and counts["races"] == len(sources)
    assert counts["runners"] == sum(len(s["horses"]) for s in sources)
    return path, {"passed": True, "observations_preserved_exactly": equality,
        "repeated_overwrite_no_duplicate_rows": True, "saved_counts": counts,
        "quality_reports": reports, "external_scrape_verified": False}


def replay_scrape_ledger(workspace: Path, saga, source_hash: str) -> dict:
    config = saga.OperationalSagaConfig(mode=saga.OperationalSagaMode.LOCAL_SQLITE,
        environment="test", sqlite_path=workspace / "scrape-saga.db", worker_enabled=True,
        remote_effects_enabled=True, execution_unlock_enabled=True, lease_seconds=5)
    store = saga.SQLiteOperationalSagaStore(config)
    store.initialize()
    payload = {"start_date": "20210101", "end_date": "20210131", "force_rescrape": False, "dry_run": False}
    request = saga.EnqueueRequest(str(uuid.uuid4()), str(uuid.uuid4()), OWNER, digest(payload), payload)
    assert store.enqueue(request, 1000).code is saga.MutationCode.APPLIED
    assert store.enqueue(request, 1000).code is saga.MutationCode.DUPLICATE
    claim = store.claim_next("proof-old-worker", 1001, 5).claim
    assert store.claim_next("proof-other-worker", 1002, 5).code is saga.MutationCode.NOT_FOUND
    restarted = saga.SQLiteOperationalSagaStore(config)
    newer = restarted.claim_next("proof-restarted-worker", 1007, 5).claim
    assert newer.fencing_token > claim.fencing_token
    assert newer.idempotency_key == claim.idempotency_key
    effect = saga.EffectResult({"saved_observation_hash": source_hash, "external_fetch": False}, source_hash)
    assert restarted.complete(claim, effect, 1008).code is saga.MutationCode.CONFLICT
    assert restarted.complete(newer, effect, 1008).code is saga.MutationCode.APPLIED
    assert restarted.complete(newer, effect, 1008).code is saga.MutationCode.DUPLICATE
    stopped = saga.EnqueueRequest(str(uuid.uuid4()), str(uuid.uuid4()), OWNER, digest(payload), payload)
    restarted.enqueue(stopped, 1010)
    cancelled_claim = restarted.claim_next("proof-stop-worker", 1011, 5).claim
    assert restarted.request_cancel(stopped.job_id, "other-owner", 1012).code is saga.MutationCode.NOT_FOUND
    assert restarted.request_cancel(stopped.job_id, OWNER, 1012).job["status"] == "cancelling"
    # A process exit after a durable stop must not turn into another acquisition.
    final = saga.SQLiteOperationalSagaStore(config)
    assert final.claim_next("proof-restarted-worker", 1017, 5).code is saga.MutationCode.NOT_FOUND
    assert final.get_job(stopped.job_id, OWNER)["status"] == "cancelled"
    assert final.complete(cancelled_claim, effect, 1017).code is saga.MutationCode.CONFLICT
    return {"passed": True, "duplicate_enqueue_preserved": True,
        "live_lease_prevents_second_claim": True, "expired_lease_reclaimed_same_effect_key": True,
        "stale_fence_write_rejected": True, "receipt_idempotent": True,
        "owner_isolation": True, "durable_stop_survives_restart": True,
        "scope": "real operational store, isolated clock/IO effect; not a network batch-worker proof"}


def replay_training(workspace: Path, source_db: Path, sources: list[dict], local, eligibility, binding: tuple[str, str]) -> dict:
    snapshot = local.create_sqlite_snapshot(source_db.resolve(), (workspace / "snapshots").resolve())
    assert file_digest(snapshot.path) == snapshot.sha256
    keiba = str(ROOT / "keiba")
    if keiba not in sys.path:
        sys.path.insert(0, keiba)
    loader = importlib.import_module("keiba_ai.db_ultimate_loader")
    # The eligibility policy consumes canonical race_date/time_seconds, not
    # raw provider date/finish_time. Use the same real loader rather than
    # inventing an alias parser or marking all unnormalized rows ineligible.
    with redirect_stdout(io.StringIO()):
        frame = loader.load_ultimate_training_frame(snapshot.path, read_only=True)
    selection = eligibility.select_training_eligible_rows(frame, target="speed_deviation",
        training_date_from="2021-01", training_date_to="2022-12")
    # Freeze real protocol source files in the test workspace; concurrent app
    # editing cannot invalidate this small protocol-only contract mid-exercise.
    source_root = workspace / "protocol-source"
    for relative in ("python-api/training/local_retrain.py", "python-api/training/eligibility.py"):
        destination = source_root / relative
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(ROOT / relative, destination)
    source_hash = local.compute_source_tree_sha256(source_root)
    commit = subprocess.run(["git", "rev-parse", "HEAD"], cwd=ROOT, capture_output=True, text=True, check=True).stdout.strip()
    now = datetime.now(timezone.utc)
    clock = lambda: now
    store = local.LocalRetrainStore(workspace / "train-jobs.db", clock=clock)
    parameters = {"force_sync": False, "test_size": .2, "cv_folds": 5, "use_sqlite": True,
        "ultimate_mode": True, "use_optimizer": True, "use_optuna": False,
        "optuna_trials": 50, "optuna_timeout": 300,
        "training_date_from": "2021-01", "training_date_to": "2022-12"}
    preparing = str(uuid.uuid4())
    store.create_job(job_id=preparing, owner_id=OWNER, request_payload=parameters,
        preparation_token="1" * 64, preparation_ttl_seconds=30)
    _, failed = local.LocalRetrainStore(store.db_path, clock=clock).recover_after_process_restart()
    assert preparing in failed and store.get_job(preparing).failure_code == "restart-during-snapshot"
    job_id = str(uuid.uuid4())
    created = store.create_job(job_id=job_id, owner_id=OWNER, request_payload=parameters,
        preparation_token="2" * 64, preparation_ttl_seconds=30)
    contract = local.LocalTrainingContract.create(job_id=job_id, owner_id=OWNER, snapshot=snapshot,
        target="speed_deviation", model_type="lightgbm", selected_features=("distance",), removed_features=(),
        candidate_commit_sha=commit, source_tree_sha256=source_hash,
        active_model_id=binding[0], active_model_sha256=binding[1],
        training_parameters=parameters, future_fields={"finish_position", "finish_time"}, created_at=now)
    queued = store.queue_job(job_id=job_id, expected_version=created.record_version,
        preparation_token="2" * 64, snapshot=snapshot, contract=contract)
    def gateway_for(current_store):
        return local.LocalRetrainGateway(current_store, artifact_root=(workspace / "models" / "artifacts").resolve(),
            published_model_root=(workspace / "models").resolve(), source_root=source_root.resolve(),
            candidate_commit_provider=lambda: commit, active_model_binding_provider=lambda: binding)
    gateway = gateway_for(store)
    old = gateway.claim(worker_id="proof-train-worker", job_id=job_id,
        expected_version=queued.record_version, ttl_seconds=30)
    recovered_store = local.LocalRetrainStore(store.db_path, clock=clock)
    redispatch, failures = recovered_store.recover_after_process_restart()
    assert job_id in redispatch and not failures
    rejected = False
    try:
        gateway.heartbeat(lease=old, ttl_seconds=30)
    except local.RetrainLeaseLost:
        rejected = True
    assert rejected
    current = recovered_store.get_job(job_id)
    new_gateway = gateway_for(recovered_store)
    new = new_gateway.claim(worker_id="proof-train-new-worker", job_id=job_id,
        expected_version=current.record_version, ttl_seconds=30)
    assert new.fencing_token > old.fencing_token
    new_gateway.start(lease=new)  # Protocol transition only; NEVER fit a model.
    _, failures = local.LocalRetrainStore(store.db_path, clock=clock).recover_after_process_restart()
    assert job_id in failures and recovered_store.get_job(job_id).failure_code == "restart-during-training"
    smoke = smoke_model_fit(workspace, snapshot.path)
    return {"passed": True, "snapshot_sha256": snapshot.sha256, "snapshot_integrity": True,
        "loader": "keiba_ai.db_ultimate_loader.load_ultimate_training_frame(read_only=True)",
        "loaded_rows": len(frame), "loaded_columns": len(frame.columns),
        "eligibility": selection.manifest.as_dict(), "preparation_restart_fail_closed": True,
        "claimed_restart_redispatch": True, "stale_lease_rejected": rejected,
        "new_fence_monotonic": True, "running_restart_fail_closed": True,
        "model_fit_executed": smoke["model_fit_executed"],
        "approved_training_job_fit_executed": False, "artifact_published": False,
        "scope": "saved-data eligibility/snapshot and protocol-only one-feature lease contract; separate bounded smoke fit below, not candidate selection",
        "separate_saved_data_smoke_fit": smoke}


def smoke_model_fit(workspace: Path, snapshot_path: Path) -> dict:
    """Real fixed bounded fit/save/reload; last date holdout, no ROI/tuning."""
    import joblib
    import lightgbm as lgb
    import numpy as np
    import pandas as pd
    dataset = importlib.import_module("experiments.dataset")
    training = importlib.import_module("experiments.training")
    evaluation = importlib.import_module("experiments.evaluation")
    speed = importlib.import_module("keiba_ai.speed_deviation")
    started = time.perf_counter()
    # These are the frozen canonical source, completeness and PIT builders.
    dataset_root = workspace / "model-smoke-dataset"
    manifest = dataset.build_dataset(snapshot_path, dataset_root)
    frame = training._validate_frame(pd.read_parquet(dataset_root / "dataset.parquet"))
    dates = sorted(frame["race_date"].dropna().astype(str).unique().tolist())
    features, categorical = training._features(manifest, {"candidate_id": "M02"})
    common = {"kind": "isolated_saved_data_model_workflow_smoke",
        "scope": "fit/save/reload functionality only, not model quality/adoption/ROI evidence",
        "dataset_sha256": manifest["dataset_sha256"], "snapshot_sha256": manifest["snapshot_sha256"],
        "candidate_races": manifest["race_count"], "candidate_rows": manifest["row_count"],
        "requested_base_features": len(manifest["feature_sets"]["M01"]),
        "requested_total_features": len(features), "requested_features": features,
        "point_in_time_audit": manifest["point_in_time_audit"],
        "quality_gate": manifest["training_eligibility"],
        "complete_runner_exclusions": manifest["exclusion_reason_counts"]["complete_runner_target_cohort"],
        "model_adopted": False, "used_for_buying": False, "roi_evaluation": "NOT_PERFORMED",
        "dates_available": dates}
    if len(dates) < 2:
        return {**common, "status": "NOT_RUN", "model_fit_executed": False,
            "reason": "Only one eligible calendar date; strict TRAIN < holdout split unavailable",
            "temporal_split_verified": False, "elapsed_seconds": round(time.perf_counter() - started, 4)}
    last = dates[-1]
    train = frame.loc[frame["race_date"].lt(last)].reset_index(drop=True)
    valid = frame.loc[frame["race_date"].eq(last)].reset_index(drop=True)
    assert not train.empty and not valid.empty and str(train["race_date"].max()) < str(valid["race_date"].min())
    assert set(train["race_id"]).isdisjoint(valid["race_id"])
    encoder = training.FoldEncoder(features, categorical)
    x_train = encoder.fit_transform(train)
    x_valid = encoder.transform(valid)
    baseline = speed.fit_speed_deviation_baseline(train)
    y_train = speed.apply_speed_deviation_baseline(train, baseline)
    y_valid = speed.apply_speed_deviation_baseline(valid, baseline)
    # Fixed parameters declared before any held-out labels are evaluated.
    params, rounds = training._params({"device": "cpu", "num_threads": 2, "seed": 42,
        "params": {"num_boost_round": 30, "num_leaves": 7, "min_data_in_leaf": 5}}, ranking=False)
    model = lgb.train(params, lgb.Dataset(x_train, label=y_train,
        categorical_feature=[c for c in categorical if c in encoder.feature_columns]), num_boost_round=rounds)
    predicted = np.asarray(model.predict(x_valid, num_threads=2), dtype=float)
    assert predicted.shape == (len(valid),) and np.isfinite(predicted).all()
    bundle = {"schema": "local-workflow-smoke-artifact-v1", "model": model,
        "encoder": encoder, "speed_deviation_baseline": baseline,
        "model_workflow_smoke_only": True, "dataset_sha256": manifest["dataset_sha256"],
        "requested_features": features, "feature_columns": list(encoder.feature_columns),
        "train_max_date": str(train["race_date"].max()), "validation_date": last,
        "params": params, "rounds": rounds}
    artifact = workspace / "model-smoke.joblib"
    joblib.dump(bundle, artifact)
    artifact_sha = file_digest(artifact)
    assert artifact_sha == file_digest(artifact)
    # Load only our just-generated trusted artifact, never an arbitrary pickle.
    restored = joblib.load(artifact)
    restored_x = restored["encoder"].transform(valid)
    restored_scores = np.asarray(restored["model"].predict(restored_x, num_threads=2), dtype=float)
    exact = np.array_equal(predicted, restored_scores)
    assert exact and np.array_equal(x_valid.to_numpy(), restored_x.to_numpy(), equal_nan=True)
    assert restored["speed_deviation_baseline"] == baseline
    assert file_digest(artifact) == artifact_sha
    evaluated = valid.copy()
    evaluated["prediction"] = predicted
    evaluated["actual"] = y_valid.to_numpy()
    evaluated["actual_speed_mps"] = speed.raw_speed_mps(valid).to_numpy()
    evaluated["prediction_speed_mps"] = training._inverse_speed(predicted, valid, baseline)
    races = evaluation.race_evaluation_records(evaluated, task="speed_regression")
    aggregate = evaluation.evaluate_race_records(races, bootstrap_samples=0)["overall"]
    metrics = {key: aggregate[key] for key in ("race_count", "runner_count", "rank_correlation",
        "rank_correlation_races", "constant_score_or_result_races", "rmse", "rmse_speed_mps")}
    return {**common, "status": "PASS", "model_fit_executed": True,
        "train": {"races": int(train["race_id"].nunique()), "rows": len(train),
            "from": str(train["race_date"].min()), "to": str(train["race_date"].max())},
        "holdout": {"races": int(valid["race_id"].nunique()), "rows": len(valid), "date": last},
        "temporal_split_verified": True, "train_only_encoder": True, "train_only_target_baseline": True,
        "heldout_used_for_tuning_or_early_stopping": False,
        "fitted_features": encoder.feature_columns, "dropped_features": encoder.dropped_columns,
        "num_threads": 2, "boost_rounds": rounds, "artifact_sha256": artifact_sha,
        "artifact_size_bytes": artifact.stat().st_size, "artifact_retention": "temporary smoke artifact, removed with isolated workspace",
        "reload_exact_scores": exact, "reload_max_absolute_score_difference": float(np.max(np.abs(predicted - restored_scores))),
        "workflow_smoke_metrics_only": metrics,
        "rmse_units": {"rmse": "TRAIN-normalized speed-deviation units", "rmse_speed_mps": "metres per second"},
        "elapsed_seconds": round(time.perf_counter() - started, 4)}


def fixed_tickets(source: dict) -> list[dict]:
    # Number-only deterministic fixture. No finish, odds, payout or model value
    # is read in this choice function. Its returns are NOT a strategy backtest.
    numbers = sorted(int(horse["horse_number"]) for horse in source["horses"])
    if len(set(numbers)) != len(numbers) or len(numbers) < 4:
        raise ValueError("Settlement fixture needs at least four distinct runners")
    return [{"bet_type": kind, "combination": "-".join(map(str, numbers[:count])),
        "horse_numbers": numbers[:count], "stake": 100} for kind, count in TYPES.items()]


def settlement_replay(workspace: Path, source_db: Path, sources: list[dict], performance) -> dict:
    ledger = workspace / "performance.db"
    # Deliberately seed test pending records: no invented calibration/semantic
    # approval is supplied to save_snapshot/record_selection. The settlement
    # engine and persistence paths are real; live offer acceptance is NOT tested.
    records = []
    with performance._database(ledger) as database:
        for source in sources:
            try:
                tickets = fixed_tickets(source)
            except (ValueError, KeyError, TypeError):
                continue
            info = source["race_info"]
            payload = {"race_info": info, "predictions": [{"horse_number": h["horse_number"], "horse_id": h.get("horse_id")} for h in source["horses"]],
                "model_id": "workflow-fixture-not-a-model", "recommendation": {
                    "policy_version": performance.POLICY_VERSION, "decision": "skip", "input_verified": False,
                    "reason": "isolated settlement engine fixture", "reason_codes": ["fixture_not_prediction"]}}
            record_id = str(uuid.uuid4())
            database.execute("INSERT INTO betting_performance_records VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
                (record_id, PROOF_OWNER, "fixture-not-offer", info["race_id"],
                performance._race_date(info.get("date") or info.get("race_date")), "paper",
                performance._encoded(payload), performance._encoded(tickets), "pending", None,
                datetime.now(timezone.utc).isoformat(), None))
            records.append(info["race_id"])
    if not records:
        raise ValueError("No usable settlement fixture source")
    missing = performance.list_performance(ledger, workspace / "not-fetched.db", PROOF_OWNER, "paper")
    assert all(record["status"] == "pending" and record["actual_return"] is None for record in missing["records"])
    assert not (workspace / "not-fetched.db").exists()
    # With official results but without dividends, even losing selections must
    # not become fake losses. Copy a consistent RO snapshot into a temp-only DB.
    without_dividends = workspace / "payout-wait.db"
    with readonly(source_db) as source:
        with closing(sqlite3.connect(without_dividends)) as destination:
            source.backup(destination)
            destination.execute("DELETE FROM return_tables_ultimate")
            destination.commit()
    waiting = performance.list_performance(ledger, without_dividends, PROOF_OWNER, "paper")
    assert all(record["actual_return"] is None for record in waiting["records"])
    restored = performance.list_performance(ledger, source_db, PROOF_OWNER, "paper")
    repeated = performance.list_performance(ledger, source_db, PROOF_OWNER, "paper")
    assert digest(restored) == digest(repeated)
    other = performance.list_performance(ledger, source_db, "other-owner", "paper")
    actual = performance.list_performance(ledger, source_db, PROOF_OWNER, "actual")
    assert other["total_count"] == actual["total_count"] == 0
    outcomes = [{"race_id": record["race_id"], "status": record["status"],
        "fixture_stake": record["total_cost"], "official_return": record["actual_return"],
        "reason": record["settlement_reason"], "tickets": [{
            "bet_type": ticket["bet_type"], "combination": ticket["combination"],
            "official_return": ticket.get("actual_return"), "status": ticket.get("status")
        } for ticket in record["tickets"]]} for record in restored["records"]]
    statuses = dict(Counter(row["status"] for row in outcomes))
    # Do not serialize a fixture ROI: outcome-independent number picks are not
    # a pre-race executed model experiment and have no profitability standing.
    return {"passed": True, "official_dividends_only": True,
        "missing_result_keeps_pending": True, "missing_payout_not_fake_loss": True,
        "restart_reload_idempotent": True, "owner_isolation": True,
        "actual_purchase_records": 0, "fixture_races": len(outcomes),
        "status_counts": statuses, "settled_all_fixture_races": statuses.get("settled", 0) == len(outcomes),
        "rows": outcomes, "roi_evaluation": "NOT_PERFORMED",
        "live_offer_acceptance_verified": False,
        "scope": "real official-result settlement engine, synthetic pending tickets; not predictions/live order eligibility"}


def active_binding() -> tuple[tuple[str, str], str]:
    pointer = ROOT / "python-api" / "models" / ".active_model.json"
    content = pointer.read_bytes()
    model_id = json.loads(content)["model_id"]
    if not isinstance(model_id, str) or re.fullmatch(r"model_[A-Za-z0-9_-]{1,180}", model_id) is None:
        raise ValueError("Invalid active pointer")
    return (model_id, file_digest(pointer.parent / f"{model_id}.joblib")), hashlib.sha256(content).hexdigest()


def collect_report(source: Path = SOURCE, limit: int = 12) -> dict:
    before_binding, before_pointer = active_binding()
    source_code_hashes = {relative: file_digest(ROOT / relative) for relative in RUNTIME_SOURCES}
    with tempfile.TemporaryDirectory(prefix="keiba-workflow-proof-") as temporary:
        workspace = Path(temporary).resolve()
        with isolated_io(workspace, source) as counters:
            sources, inventory = load_source(source, limit)
            if not sources:
                raise ValueError("Historical saved-dividend cohort is empty")
            storage, quality, saga, local, eligibility, performance = load_runtime_modules()
            replay_db, storage_report = replay_storage(workspace, sources, storage, quality)
            ledger_report = replay_scrape_ledger(workspace, saga, inventory["source_rows_sha256"])
            training_report = replay_training(workspace, replay_db, sources, local, eligibility, before_binding)
            settlement_report = settlement_replay(workspace, replay_db, sources, performance)
            after_sources, _ = load_source(source, limit)
            source_unchanged = digest(after_sources) == digest(sources)
            after_binding, after_pointer = active_binding()
            protected = source_unchanged and before_binding == after_binding and before_pointer == after_pointer
            code_unchanged = source_code_hashes == {relative: file_digest(ROOT / relative) for relative in RUNTIME_SOURCES}
            assert protected and code_unchanged and counters["network_denied"] == counters["sqlite_denied"] == 0
            checks = dict(counters)
    return {"schema": "local-saved-data-workflow-proof-v1", "observed_at": datetime.now(timezone.utc).isoformat(),
        "scope": "bounded real saved-data isolated replay; NOT authenticated live E2E or model/ROI validation",
        "source_inventory": inventory, "acquisition_saved_data_replay": storage_report,
        "acquisition_ledger_recovery": ledger_report, "training_protocol_recovery": training_report,
        "official_settlement_recovery": settlement_report,
        "protected_state": {"selected_source_rows_unchanged": source_unchanged,
            "active_model_unchanged": before_binding == after_binding,
            "active_pointer_unchanged": before_pointer == after_pointer,
            "active_model_id": before_binding[0], "active_model_sha256": before_binding[1],
            "forward_collector_not_accessed": True},
        "runtime_source_hashes": source_code_hashes,
        "runtime_sources_unchanged_during_replay": code_unchanged,
        "io_checks": checks, "isolated_replay_passed": True,
        "release_complete": False, "live_five_feature_workflow_verified": False,
        "remaining_live_gates": ["bounded authorized external acquisition through UI/API",
            "release candidate chronological Spearman/RMSE evaluation (this lightweight fit is not quality validation)",
            "candidate prediction/probability evidence and nonzero paper acceptance through authenticated API",
            "real UI stop/restart recovery (isolated store tests do not prove this)"]}


def write_report(report: dict, output: Path) -> Path:
    output = output.resolve()
    if output.suffix != ".json" or not output.is_relative_to(REPORT_ROOT.resolve()):
        raise ValueError("Evidence must be a new JSON in the local workflow proof directory")
    output.parent.mkdir(parents=True, exist_ok=True)
    with output.open("x", encoding="utf-8") as stream:
        json.dump(report, stream, ensure_ascii=False, indent=2, allow_nan=False)
        stream.write("\n")
    return output


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source-db", type=Path, default=SOURCE)
    parser.add_argument("--race-limit", type=int, default=12)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
    output = args.output or REPORT_ROOT / f"saved-data-workflow-{stamp}.json"
    report = collect_report(args.source_db.resolve(strict=True), args.race_limit)
    saved = write_report(report, output)
    print(json.dumps({"isolated_replay_passed": True, "release_complete": False,
        "races": report["source_inventory"]["selected_races"],
        "settlement_states": report["official_settlement_recovery"]["status_counts"],
        "report": str(saved)}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
