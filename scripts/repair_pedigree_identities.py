"""Repair research-only missing parent fields from source-linked ancestor IDs."""
from __future__ import annotations

import argparse
import asyncio
from collections import Counter
from contextlib import closing
from datetime import datetime, timezone
import hashlib
from itertools import zip_longest
import json
from pathlib import Path
import re
import shutil
import sqlite3
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / "python-api"), str(ROOT / "keiba")]

import pandas as pd

from experiments.common import file_sha256, json_sha256, read_json, source_manifest
from experiments.dataset import build_dataset
from experiments.goal_diagnostics import diagnose_dataset
from experiments.search import freeze_json
from scraping import pedigree_identity as identity
from scraping.fetch_pipeline import (
    FetchAccessBlocked, decode_html_body, fetch_context, fetch_text,
)
from scraping.missingness_goal import IDENTITIES, PEDIGREE_FEATURES, _protected, _summary
from scraping.pedigree_gap_repair import revision
from scraping.safe_repair import _journal, digest, encode, initialize_journal
from scraping.saved_horse_reuse import _present


def now():
    return datetime.now(timezone.utc).isoformat()


def readonly(path):
    return sqlite3.connect(Path(path).resolve(strict=True).as_uri() + "?mode=ro", uri=True)


def proof_from_row(horse, row):
    url, final_url, status, body, captured = row
    if status != 200 or final_url.rstrip("/") != url.rstrip("/") or captured <= 0:
        raise ValueError("pedigree-source-transport-unconfirmed")
    proof = {
        "horse_id": horse, "source_url": url,
        "raw_body_sha256": hashlib.sha256(body).hexdigest(),
        "source_observed_at_utc": datetime.fromtimestamp(captured, timezone.utc).isoformat(),
        "reconstructed_at_utc": now(),
        "parents": identity.parse_parent_identities(decode_html_body(body), horse, url),
    }
    proof["proof_sha256"] = json_sha256(proof)
    return proof


def archive_cached_sources(path, horses):
    with closing(readonly(ROOT / "keiba" / "data" / "fetch_cache.db")) as source, closing(
        sqlite3.connect(path)
    ) as target:
        target.execute("""CREATE TABLE sources (
            url TEXT PRIMARY KEY, final_url TEXT, status INTEGER, body BLOB, fetched_at REAL)""")
        source.execute("BEGIN")
        urls = [f"https://{host}/horse/ped/{horse}/"
                for horse in sorted(horses) for host in sorted(identity.HOSTS)]
        for offset in range(0, len(urls), 400):
            batch = urls[offset:offset + 400]
            rows = source.execute(
                "SELECT normalized_url,final_url,status,body,fetched_at FROM http_cache "
                f"WHERE normalized_url IN ({','.join('?' for _ in batch)})", batch,
            ).fetchall()
            target.executemany("INSERT INTO sources VALUES(?,?,?,?,?)", rows)
        target.commit()
        source.commit()


def parse_archive(path):
    proofs, rejected, conflicts = {}, [], set()
    with closing(readonly(path)) as db:
        for row in db.execute("SELECT url,final_url,status,body,fetched_at FROM sources ORDER BY url"):
            horse = re.fullmatch(r"https://[^/]+/horse/ped/([A-Za-z0-9]+)/", row[0])[1]
            try:
                proof = proof_from_row(horse, row)
            except ValueError as error:
                rejected.append({"horse_id": horse, "url": row[0], "reason": str(error)})
                continue
            if horse in conflicts:
                continue
            if horse in proofs:
                if any(proofs[horse]["parents"][field]["horse_id"] != proof["parents"][field]["horse_id"]
                       for field in identity.FIELDS):
                    conflicts.add(horse)
                    proofs.pop(horse)
                    rejected.append({"horse_id": horse, "url": row[0],
                                     "reason": "pedigree-source-parent-id-conflict-between-endpoints"})
                continue
            proofs[horse] = proof
    return proofs, rejected


async def collect_race_sources(path, horses, known):
    import aiohttp

    receipts = []
    with fetch_context(job_id=path.parent.name) as metrics:
        async with aiohttp.ClientSession() as session:
            for horse in sorted(horses - known):
                url = f"https://db.netkeiba.com/horse/ped/{horse}/"
                receipt = {"horse_id": horse, "url": url, "started_at_utc": now()}
                try:
                    fetched, _ = await fetch_text(
                        session, url, max_retries=1, allow_redirects=False,
                        min_interval_sec=2.0, total_timeout_sec=30, max_body_bytes=2_000_000,
                        resume_key=f"{path.parent.name}:race:{horse}",
                    )
                    receipt.update(status=fetched.status, source=fetched.source)
                    stamp = (fetched.cache_snapshot or {}).get("fetched_at")
                    if stamp is None:
                        stamp = datetime.now(timezone.utc).timestamp()
                    with closing(sqlite3.connect(path)) as db:
                        db.execute("INSERT OR REPLACE INTO sources VALUES(?,?,?,?,?)",
                                   (url, fetched.url, fetched.status, fetched.body, stamp))
                        db.commit()
                except FetchAccessBlocked as error:
                    receipt.update(status="provider_blocked", error=str(error))
                receipts.append(receipt)
                if receipt["status"] == "provider_blocked":
                    break
    return {"network_requests": metrics.get("network_requests", 0), "receipts": receipts,
            "maximum_selected_horses": len(horses - known), "historical_capture_time_verified": False}


def race_rows(path, race_id):
    with closing(readonly(path)) as db:
        metadata = db.execute("SELECT data FROM races_ultimate WHERE race_id=?", (race_id,)).fetchone()
        rows = [json.loads(raw) for raw, in db.execute(
            "SELECT data FROM race_results_ultimate WHERE race_id=?", (race_id,)
        )]
    return json.loads(metadata[0]), sorted(rows, key=lambda row: row["horse_number"])


def execute(source, target, proofs, horses, batch):
    shutil.copyfile(source, target)
    initialize_journal(target)
    counts, held = Counter(), Counter()
    with closing(sqlite3.connect(target)) as db:
        rows = db.execute("SELECT id,race_id,data FROM race_results_ultimate ORDER BY id")
        pending = []
        for row_id, race_id, raw in rows:
            if not re.fullmatch(r"\d{12}", race_id) or not "01" <= race_id[4:6] <= "10":
                continue
            item = json.loads(raw)
            horse = item.get("horse_id")
            if horse not in horses or horse not in proofs:
                continue
            if all(_present(item.get(field)) for field in identity.FIELDS):
                continue
            try:
                updated, fields = identity.fill_missing_parents(item, proofs[horse])
            except ValueError as error:
                held[str(error)] += 1
                continue
            if fields:
                pending.append((row_id, race_id, raw, encode(updated), proofs[horse], fields))
        for offset in range(0, len(pending), 100):
            with db:
                for row_id, race_id, before, after, proof, fields in pending[offset:offset + 100]:
                    prior = revision(db)
                    cursor = db.execute(
                        "UPDATE race_results_ultimate SET data=? WHERE id=? AND data=?",
                        (after, row_id, before),
                    )
                    if cursor.rowcount != 1:
                        raise ValueError("pedigree-source-before-image-changed")
                    current = revision(db)
                    if current[0] != prior[0] or current[1] != prior[1] + 1:
                        raise ValueError("pedigree-source-revision-did-not-advance")
                    _journal(db, batch, "race_results_ultimate", row_id, race_id, before, after, {
                        "repair_kind": "source_linked_parent_identity", "proof_sha256": proof["proof_sha256"],
                        "filled_fields": fields, "source_revision_before": prior, "source_revision_after": current,
                        "historical_capture_time_verified": False,
                    })
                    counts["updated_rows"] += 1
        db.execute("PRAGMA wal_checkpoint(TRUNCATE)")
        db.execute("PRAGMA journal_mode=DELETE")
    return {"counts": dict(counts), "held_rows": dict(held), "maximum_rows_per_transaction": 100}


def verify_rows(source, repaired, proofs, batch):
    changed, seen = 0, set()
    with closing(readonly(source)) as before, closing(readonly(repaired)) as after:
        journals = {int(key): (old, new, json.loads(evidence)) for key, old, new, evidence in after.execute(
            "SELECT row_key,before_data,after_data,evidence_json FROM data_quality_repair_journal_v1 "
            "WHERE batch_id=?", (batch,)
        )}
        for old, new in zip_longest(
            before.execute("SELECT id,race_id,data FROM race_results_ultimate ORDER BY id"),
            after.execute("SELECT id,race_id,data FROM race_results_ultimate ORDER BY id"),
        ):
            if old is None or new is None or old[:2] != new[:2]:
                raise ValueError("pedigree-repair-row-identity-changed")
            if old[2] == new[2]:
                continue
            item = json.loads(old[2])
            proof = proofs[item["horse_id"]]
            expected, fields = identity.fill_missing_parents(item, proof)
            journal = journals.get(old[0])
            if (not fields or encode(expected) != new[2] or journal is None
                    or journal[:2] != (old[2], new[2])
                    or journal[2]["proof_sha256"] != proof["proof_sha256"]):
                raise ValueError("pedigree-repair-unproven-row-change")
            changed += 1
            seen.add(old[0])
    if seen != set(journals):
        raise ValueError("pedigree-repair-journal-coverage-mismatch")
    return changed


def run(root, baseline, race_id):
    root, baseline = Path(root).resolve(), Path(baseline).resolve(strict=True)
    parent = ROOT / "reports" / "generated" / "data-quality" / "feature-gap"
    if root.parent != parent.resolve() or root.exists():
        raise ValueError("pedigree-repair-requires-new-feature-gap-directory")
    root.mkdir()
    source, before_dataset = baseline / "research.db", baseline / "iteration-02"
    before = pd.read_parquet(before_dataset / "dataset.parquet")
    missing = before.safe_sire_starts.isna() | before.safe_damsire_starts.isna()
    horses = set(before.loc[missing, "horse_id"])
    meta, race_before = race_rows(source, race_id)
    race_missing = {item["horse_id"] for item in race_before
                    if any(not _present(item.get(field)) for field in identity.FIELDS)}
    if not race_before or len(race_before) != int(meta["num_horses"]) or len(race_missing) > 18:
        raise ValueError("pedigree-example-race-incomplete")
    task = {
        "schema": "source-linked-pedigree-repair-v1", "race_id": race_id,
        "source": str(source), "before_dataset": str(before_dataset), "protected": _protected(),
        "source_sha256": file_sha256(source), "before_dataset_sha256": file_sha256(before_dataset / "dataset.parquet"),
        "code_hashes": {**source_manifest(), **{str(path): file_sha256(path) for path in (
            Path(__file__), Path(identity.__file__), ROOT / "python-api" / "scraping" / "safe_repair.py",
            ROOT / "python-api" / "scraping" / "fetch_pipeline.py",
        )}},
        "precedence": "Provider child and parent IDs may fill an empty row despite unidentified names in other rows.",
        "existing_names_and_outcomes_unchanged": True, "historical_capture_time_verified": False,
        "maximum_network_requests": len(race_missing), "full_remote_backfill_authorized_in_this_run": False,
    }
    freeze_json(root / "task-ledger.json", task)
    archive = root / "source-archive.db"
    archive_cached_sources(archive, horses | race_missing)
    proofs, rejected = parse_archive(archive)
    collection = asyncio.run(collect_race_sources(archive, race_missing, set(proofs)))
    freeze_json(root / "collection.json", collection)
    proofs, rejected = parse_archive(archive)
    freeze_json(root / "proofs.json", proofs)
    freeze_json(root / "rejected-sources.json", rejected)
    execution = execute(source, root / "research.db", proofs, horses | race_missing, root.name)
    freeze_json(root / "execution.json", execution)
    build_dataset(root / "research.db", root / "dataset")
    after = pd.read_parquet(root / "dataset" / "dataset.parquet")
    pd.testing.assert_frame_equal(before[IDENTITIES], after[IDENTITIES])
    pd.testing.assert_frame_equal(before.drop(columns=PEDIGREE_FEATURES), after.drop(columns=PEDIGREE_FEATURES))
    changed = verify_rows(source, root / "research.db", proofs, root.name)
    _, race_after = race_rows(root / "research.db", race_id)
    freeze_json(root / "race-example.json", {"race": meta, "before": race_before, "after": race_after})
    columns = ["horse_number", "horse_id", "safe_sire_starts", "safe_sire_win_rate",
               "safe_damsire_starts", "safe_damsire_win_rate"]
    after.loc[after.race_id.eq(race_id), columns].to_csv(root / "race-features.csv", index=False)
    summaries = [_summary(diagnose_dataset(path, [2023, 2024, 2025])) for path in
                 (before_dataset, root / "dataset")]
    if (file_sha256(source) != task["source_sha256"] or _protected() != task["protected"]
            or collection["network_requests"] > task["maximum_network_requests"]):
        raise ValueError("pedigree-source-or-protected-state-changed")
    result = {
        "schema": task["schema"], "verified": True, "same_cohort": True,
        "updated_raw_rows": changed, "proof_horses": len(proofs), "summaries": summaries,
        "race_missing_before": len(race_missing),
        "race_missing_after": sum(any(not _present(item.get(field)) for field in identity.FIELDS)
                                  for item in race_after),
        "race_parent_feature_missing_cells": int(
            after.loc[after.race_id.eq(race_id), PEDIGREE_FEATURES].isna().sum().sum()),
        "global_zero_goal_met": summaries[-1]["missing_feature_cells"] == 0,
        "network_requests": collection["network_requests"], "production_result_db_or_model_changed": False,
        "historical_prestart_availability_proven": False,
    }
    freeze_json(root / "artifacts.json", {str(path.relative_to(root)): file_sha256(path)
                                        for path in sorted(root.rglob("*")) if path.is_file()})
    freeze_json(root / "verification.json", result)
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, required=True)
    parser.add_argument("--baseline", type=Path, required=True)
    parser.add_argument("--race-id", default="202506050811")
    args = parser.parse_args()
    result = run(args.root, args.baseline, args.race_id)
    print(json.dumps({key: value for key, value in result.items() if key != "summaries"},
                     ensure_ascii=False, allow_nan=False))
    return 0 if result["race_missing_after"] == 0 else 2


if __name__ == "__main__":
    raise SystemExit(main())
