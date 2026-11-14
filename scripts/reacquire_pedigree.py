"""Checkpoint source-backed pedigree reacquisition without changing production."""
from __future__ import annotations

import argparse
import asyncio
from contextlib import closing
import json
from pathlib import Path
import sqlite3
import sys
import time

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / "python-api"), str(ROOT / "keiba"), str(ROOT / "scripts")]

import aiohttp
import pandas as pd

import repair_pedigree_identities as repair
from experiments.common import file_sha256, read_json, write_json
from experiments.orchestrator import driver_lock
from experiments.search import freeze_json
from scraping.fetch_pipeline import FetchAccessBlocked, fetch_context, fetch_text, get_fetch_control_status


def status(root):
    with closing(sqlite3.connect(root / "source-archive.db")) as db:
        counts = dict(db.execute("SELECT status,COUNT(*) FROM queue GROUP BY status"))
        network = db.execute("SELECT COALESCE(SUM(network_requests),0) FROM queue").fetchone()[0]
    return {"at_utc": repair.now(), "counts": counts, "network_requests": network,
            "remaining": counts.get("pending", 0), "historical_prestart_availability_proven": False}


def validate(root):
    task = read_json(root / "task-ledger.json")
    for path, expected in task["code_hashes"].items():
        if file_sha256(Path(path)) != expected:
            raise ValueError(f"pedigree-reacquisition-code-changed: {path}")
    for key, hash_key in (("source", "source_sha256"), ("before_dataset", "before_dataset_sha256")):
        path = Path(task[key])
        if key == "before_dataset":
            path /= "dataset.parquet"
        if file_sha256(path) != task[hash_key]:
            raise ValueError("pedigree-reacquisition-frozen-input-changed")
    if repair._protected() != task["protected"]:
        raise ValueError("pedigree-reacquisition-protected-state-changed")
    return task


def prepare(root, baseline):
    baseline = baseline.resolve(strict=True)
    source, dataset = baseline / "research.db", baseline / "dataset"
    before = pd.read_parquet(dataset / "dataset.parquet")
    missing = before.safe_sire_starts.isna() | before.safe_damsire_starts.isna()
    targets = before.loc[missing].groupby("horse_id").size().sort_values(ascending=False)
    if any(not repair.identity.ID.fullmatch(horse) for horse in targets.index):
        raise ValueError("pedigree-reacquisition-target-id-invalid")
    root.mkdir()
    task = {
        "schema": "resumable-pedigree-reacquisition-v1", "source": str(source),
        "source_sha256": file_sha256(source), "before_dataset": str(dataset),
        "before_dataset_sha256": file_sha256(dataset / "dataset.parquet"),
        "protected": repair._protected(), "target_horses": len(targets),
        "target_missing_rows": int(missing.sum()),
        "cohort_policy": "All eligible rows in the frozen baseline, including 2026; reports also fix 2016-2025.",
        "network_interval_seconds": 2.0, "attempts_per_horse_per_run": 1,
        "code_hashes": {**repair.source_manifest(), **{
            str(path): file_sha256(path) for path in (
                Path(__file__), Path(repair.__file__), Path(repair.identity.__file__),
                ROOT / "python-api" / "scraping" / "fetch_pipeline.py",
                ROOT / "python-api" / "scraping" / "request_pacing.py",
                ROOT / "python-api" / "scraping" / "safe_repair.py",
            )}},
        "historical_capture_time_verified": False, "production_writes_authorized": False,
    }
    freeze_json(root / "task-ledger.json", task)
    archive = root / "source-archive.db"
    repair.archive_cached_sources(archive, set(targets.index))
    proofs, rejected = repair.parse_archive(archive)
    freeze_json(root / "initial-rejected-sources.json", rejected)
    with closing(sqlite3.connect(archive)) as db:
        db.execute("""CREATE TABLE queue (
            horse_id TEXT PRIMARY KEY, affected_rows INTEGER NOT NULL,
            status TEXT NOT NULL, proof_json TEXT, error TEXT, attempted_at TEXT,
            fetch_source TEXT, http_status INTEGER, network_requests INTEGER NOT NULL DEFAULT 0)""")
        for horse, rows in targets.items():
            proof = proofs.get(horse)
            db.execute("INSERT INTO queue(horse_id,affected_rows,status,proof_json) VALUES(?,?,?,?)",
                       (horse, int(rows), "accepted" if proof else "pending",
                        json.dumps(proof, ensure_ascii=False) if proof else None))
        db.commit()
    write_json(root / "progress.json", status(root))


async def collect(root, maximum):
    archive = root / "source-archive.db"
    consecutive_failures, attempted, stop_reason = 0, 0, "request_limit"
    with closing(sqlite3.connect(archive)) as db, fetch_context(job_id=root.name) as metrics:
        async with aiohttp.ClientSession() as session:
            while maximum is None or attempted < maximum:
                control = get_fetch_control_status()
                if control.get("blocked_reason"):
                    stop_reason = "provider_blocked"
                    break
                if control.get("cooldown_until", 0) > time.time():
                    stop_reason = "provider_cooldown"
                    break
                row = db.execute(
                    "SELECT horse_id FROM queue WHERE status='pending' "
                    "ORDER BY affected_rows DESC,horse_id LIMIT 1"
                ).fetchone()
                if row is None:
                    stop_reason = "all_targets_attempted"
                    break
                horse = row[0]
                url = f"https://db.netkeiba.com/horse/ped/{horse}/"
                started, before_network = repair.now(), metrics.get("network_requests", 0)
                proof, error, outcome, http_status, fetch_source = None, None, "failed", None, None
                try:
                    fetched, _ = await fetch_text(
                        session, url, max_retries=1, allow_redirects=False,
                        min_interval_sec=2.0, total_timeout_sec=30, max_body_bytes=2_000_000,
                        force_refresh=True, resume_key=f"{root.name}:pedigree:{horse}",
                    )
                    http_status, fetch_source = fetched.status, fetched.source
                    stamp = (fetched.cache_snapshot or {}).get("fetched_at")
                    db.execute("INSERT OR REPLACE INTO sources VALUES(?,?,?,?,?)",
                               (url, fetched.url, fetched.status, fetched.body, stamp))
                    if fetched.status == 200 and stamp is not None:
                        try:
                            proof = repair.proof_from_row(
                                horse, (url, fetched.url, fetched.status, fetched.body, stamp))
                            outcome = "accepted"
                        except ValueError as exc:
                            outcome, error = "source_rejected", str(exc)
                    else:
                        error = fetched.error or f"http-{fetched.status}; source-stamp={stamp}"
                        if fetched.status == 429:
                            outcome = "rate_limited"
                except FetchAccessBlocked as exc:
                    outcome, error = "provider_blocked", str(exc)
                db.execute(
                    "UPDATE queue SET status=?,proof_json=?,error=?,attempted_at=?,fetch_source=?,"
                    "http_status=?,network_requests=network_requests+? WHERE horse_id=?",
                    (outcome, json.dumps(proof, ensure_ascii=False) if proof else None, error, started,
                     fetch_source, http_status, metrics.get("network_requests", 0) - before_network, horse),
                )
                db.commit()
                attempted += 1
                consecutive_failures = 0 if outcome == "accepted" else consecutive_failures + 1
                progress = {**status(root), "last_horse_id": horse, "last_outcome": outcome,
                            "last_error": error, "state": "collecting"}
                write_json(root / "progress.json", progress)
                if attempted % 25 == 0:
                    print(json.dumps(progress, ensure_ascii=False), flush=True)
                if outcome in {"provider_blocked", "rate_limited"}:
                    stop_reason = outcome
                    break
                if consecutive_failures >= 3:
                    stop_reason = "three_consecutive_unaccepted_sources"
                    break
    result = {**status(root), "attempted_this_run": attempted, "stop_reason": stop_reason,
              "state": "collection_stopped"}
    write_json(root / "progress.json", result)
    return result


def materialize(root, task, collection):
    proofs = {}
    with closing(sqlite3.connect(root / "source-archive.db")) as db:
        for horse, raw in db.execute("SELECT horse_id,proof_json FROM queue WHERE status='accepted'"):
            proofs[horse] = json.loads(raw)
    freeze_json(root / "proofs.json", proofs)
    freeze_json(root / "collection.json", collection)
    source = Path(task["source"])
    execution = repair.execute(source, root / "research.db", proofs, set(proofs), root.name)
    freeze_json(root / "execution.json", execution)
    repair.build_dataset(root / "research.db", root / "dataset")
    before = pd.read_parquet(Path(task["before_dataset"]) / "dataset.parquet")
    after = pd.read_parquet(root / "dataset" / "dataset.parquet")
    pd.testing.assert_frame_equal(before[repair.IDENTITIES], after[repair.IDENTITIES])
    pd.testing.assert_frame_equal(before.drop(columns=repair.PEDIGREE_FEATURES),
                                  after.drop(columns=repair.PEDIGREE_FEATURES))
    changed = repair.verify_rows(source, root / "research.db", proofs, root.name)
    summaries = [repair._summary(repair.diagnose_dataset(path, [2023, 2024, 2025]))
                 for path in (Path(task["before_dataset"]), root / "dataset")]
    validate(root)
    result = {
        "verified": True, "same_cohort_nonpedigree_unchanged": True, "updated_raw_rows": changed,
        "accepted_horses": len(proofs), "collection": collection, "summaries": summaries,
        "global_zero_goal_met": summaries[-1]["missing_feature_cells"] == 0,
        "production_result_db_or_model_changed": False, "historical_prestart_availability_proven": False,
    }
    freeze_json(root / "artifacts.json", {str(path.relative_to(root)): file_sha256(path)
                for path in sorted(root.rglob("*")) if path.is_file()
                and path.name not in {".experiment-driver.lock", "driver.json", "progress.json"}})
    freeze_json(root / "verification.json", result)
    write_json(root / "progress.json", {**collection, "state": "verified", "updated_raw_rows": changed})
    return result


def run(root, baseline, maximum):
    root = root.resolve()
    parent = ROOT / "reports" / "generated" / "data-quality" / "feature-gap"
    if root.parent != parent.resolve():
        raise ValueError("pedigree-reacquisition-requires-feature-gap-directory")
    if not root.exists():
        if baseline is None:
            raise ValueError("pedigree-reacquisition-baseline-required")
        prepare(root, baseline)
    if (root / "verification.json").exists():
        raise ValueError("pedigree-reacquisition-completed; use a new root and this run as baseline")
    with driver_lock(root, root):
        task = validate(root)
        collection = (read_json(root / "collection.json") if (root / "collection.json").exists()
                      else asyncio.run(collect(root, maximum)))
        return materialize(root, task, collection)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, required=True)
    parser.add_argument("--baseline", type=Path)
    limits = parser.add_mutually_exclusive_group()
    limits.add_argument("--max-horses", type=int, default=100)
    limits.add_argument("--all", action="store_true")
    args = parser.parse_args()
    if args.max_horses < 1:
        parser.error("--max-horses must be positive")
    result = run(args.root, args.baseline, None if args.all else args.max_horses)
    print(json.dumps({key: value for key, value in result.items() if key != "summaries"},
                     ensure_ascii=False), flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
