"""Follow audited career links to whole result sources; repair only a separate research copy."""
from __future__ import annotations

import argparse
import asyncio
from contextlib import closing
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import re
import sqlite3
import sys
import unicodedata

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "python-api"))
sys.path.insert(0, str(ROOT / "keiba"))
sys.path.insert(0, str(Path(__file__).resolve().parent))

import aiohttp
import pandas as pd
from bs4 import BeautifulSoup

import audit_residual_acquisition as original
from experiments import dataset as ds
from experiments.common import file_sha256, read_json
from experiments.search import freeze_json
from scraping.fetch_pipeline import (
    FetchAccessBlocked, decode_html_body, fetch_context, fetch_text, get_fetch_control_status,
)
from scraping.missingness_goal import _protected
from scraping.quality import classify_race_quality
import verify_residual_acquisition as independent

BASE = ROOT / "reports" / "generated" / "data-quality" / "feature-gap"
NONFINISH = {"取", "取消", "除", "除外", "中", "中止", "失", "失格"}


def confirmable_result(proof, metadata, runners):
    meta, reason = ds._race_metadata(proof["race_id"], metadata)
    if reason is not None:
        raise ValueError(f"linked-history-outside-contract: {reason}")
    by_horse = {row["horse_id"]: row for row in proof["rows"]}
    updated = []
    for item in runners:
        canonical, reason = ds._runner(proof["race_id"], item, meta)
        if reason is not None:
            raise ValueError(f"linked-history-runner-conflict: {reason}")
        observed = by_horse[item["horse_id"]]
        if observed["finish"] is not None:
            if (pd.isna(canonical["finish"]) or canonical["finish"] != observed["finish"]
                    or observed["time_seconds"] is None or pd.isna(canonical["time_seconds"])
                    or abs(canonical["time_seconds"] - observed["time_seconds"]) > 1e-6):
                raise ValueError("linked-history-outcome-conflict-requires-separate-repair")
            updated.append(item)
        else:
            if observed["finish_source_raw"] not in NONFINISH or pd.notna(canonical["finish"]):
                raise ValueError("linked-history-nonfinish-not-explicit-or-conflicting")
            updated.append({**item, "finish_position": observed["finish_source_raw"]})
    report = classify_race_quality({"race_info": metadata, "horses": updated})
    if not report.valid_for_date_completion:
        raise ValueError("linked-history-whole-result-quality-not-complete")


def validate_source_course(html, metadata, race_id=None):
    soup = BeautifulSoup(html, "lxml")
    nodes = soup.select(".data_intro dl.racedata,.data_intro p.smalltxt,.RaceData01,"
                        ".RaceHeader_Value,.mainrace_data p.smalltxt")
    text = unicodedata.normalize("NFKC", " ".join(node.get_text(" ", strip=True) for node in nodes))
    if ds.JUMP_MARKER.search(text):
        raise ValueError("linked-history-published-jump-outside-contract")
    courses = {(ds.SURFACES[surface], int(distance)) for surface, distance in re.findall(
        r"(芝|ダート|ダ)(?:右|左|直線|外|内|[\s・･])*(\d{3,4})m", text)}
    identity = metadata.get("race_id") if race_id is None else race_id
    meta, reason = ds._race_metadata(identity, metadata)
    if reason is not None or courses != {(meta["surface"], int(meta["distance"]))}:
        raise ValueError("linked-history-published-course-not-exactly-confirmed")


async def audit(root, coverage, maximum):
    task = read_json(coverage / "contract.json")
    if read_json(coverage / "replay-verification.json").get("verified") is not True:
        raise ValueError("linked-history-coverage-not-independently-verified")
    for name, digest in read_json(coverage / "artifacts.json").items():
        if file_sha256(coverage / name) != digest:
            raise ValueError("linked-history-coverage-artifact-changed")
    source = Path(task["source"])
    if file_sha256(source) != task["source_sha256"] or _protected() != task["protected"]:
        raise ValueError("linked-history-source-or-production-changed")
    root.mkdir()
    files = [Path(__file__), Path(original.__file__), ds.__file__,
             ROOT / "python-api" / "scraping" / "acquisition_horse_history.py",
             ROOT / "python-api" / "scraping" / "horse_history_ajax.py",
             ROOT / "python-api" / "scraping" / "quality.py",
             ROOT / "python-api" / "scraping" / "race_history_evidence.py",
             ROOT / "python-api" / "scraping" / "last3f_evidence.py",
             ROOT / "python-api" / "scraping" / "race_class.py"]
    freeze_json(root / "contract.json", {
        "source": str(source), "source_sha256": task["source_sha256"], "coverage": str(coverage),
        "coverage_artifacts_sha256": file_sha256(coverage / "artifacts.json"),
        "cohort": task["cohort"], "protected": task["protected"], "research_only": True,
        "code_hashes": {str(p): file_sha256(Path(p)) for p in files},
        "historical_prestart_capture_proven": False, "network_interval_seconds": 2,
        "maximum_result_sources": maximum, "production_writes_authorized": False,
        "repair_policy": "Explicit nonfinisher label and whole-result quality; no invented outcomes.",
    })
    planned = read_json(coverage / "history-repair-plan.json")
    targets = list(dict.fromkeys(row["race_id"] for row in planned
                                if row["action"] == "audit_full_result_before_history_import"))
    proofs, held, receipts = [], [], []
    with closing(original.readonly(source)) as db, closing(
        original.readonly(coverage / "sources.db")
    ) as careers, closing(sqlite3.connect(root / "sources.db")) as archive, fetch_context(
        job_id=root.name
    ) as metrics:
        archive.execute("CREATE TABLE sources(url TEXT PRIMARY KEY,final_url TEXT,status INTEGER,body BLOB,fetched_at REAL)")
        archive.executemany("INSERT INTO sources VALUES(?,?,?,?,?)",
                            careers.execute("SELECT url,final_url,status,body,fetched_at FROM sources"))
        archive.commit()
        async with aiohttp.ClientSession() as session:
            failures, stop = 0, "source_limit"
            for race in targets[:maximum]:
                control = get_fetch_control_status()
                if control.get("blocked_reason") or control.get("cooldown_until", 0) > datetime.now(
                    timezone.utc
                ).timestamp():
                    stop = "provider_blocked_or_cooldown"
                    break
                if failures >= 3:
                    stop = "consecutive_unaccepted_sources"
                    break
                url = f"https://db.netkeiba.com/race/{race}/"
                try:
                    raw = db.execute("SELECT data FROM races_ultimate WHERE race_id=?", (race,)).fetchone()
                    if raw is None:
                        raise ValueError("linked-history-full-new-result-import-required")
                    metadata = json.loads(raw[0])
                    day = ds._date(ds._first(metadata, "date", "race_date"))
                    references = [row for row in planned if row["race_id"] == race
                                  and row["action"] == "audit_full_result_before_history_import"]
                    if any(day != row["race_date"] or not day < row["target_date"] for row in references):
                        raise ValueError("linked-history-inventory-date-conflict")
                    runners = [json.loads(row[0]) for row in db.execute(
                        "SELECT data FROM race_results_ultimate WHERE race_id=?", (race,))]
                    if not runners:
                        raise ValueError("linked-history-full-new-runner-import-required")
                    with closing(original.readonly(ROOT / "keiba" / "data" / "fetch_cache.db")) as cache:
                        cached = cache.execute(
                            "SELECT normalized_url,final_url,status,body,fetched_at FROM http_cache "
                            "WHERE normalized_url=? AND status=200", (url,),
                        ).fetchone()
                    if cached is None:
                        response, _ = await fetch_text(
                            session, url, min_interval_sec=2, max_retries=1, allow_redirects=False,
                            total_timeout_sec=120, max_body_bytes=2_000_000, force_refresh=True,
                        )
                        row = (url, response.url, response.status, response.body,
                               (response.cache_snapshot or {}).get("fetched_at"))
                        fetch_error = response.error
                    else:
                        row = cached
                        fetch_error = None
                    archive.execute("INSERT OR REPLACE INTO sources VALUES(?,?,?,?,?)", row)
                    archive.commit()
                    receipts.append({"race_id": race, "status": row[2], "reused_cached_body": cached is not None,
                                     "fetch_error": fetch_error})
                    if row[2] != 200 or row[1].rstrip("/") != url.rstrip("/") or not row[4]:
                        raise ValueError(f"linked-history-result-transport-unverified: {fetch_error}")
                    digest = hashlib.sha256(row[3]).hexdigest()
                    html = decode_html_body(row[3])
                    validate_source_course(html, metadata, race)
                    proof = original.race_evidence(
                        html, race_id=race, source_url=url,
                        expected_date=metadata["date"], expected_runners=runners,
                        observed_at_utc=datetime.fromtimestamp(row[4], timezone.utc).isoformat(),
                        raw_body_sha256=digest, raw_body=row[3], allow_cached_transport_identity=True,
                        cache_receipt={"normalized_url": url, "status": 200, "body_sha256": digest,
                                       "fetched_at": row[4]},
                    )
                    proof.update(requested_class_repair=False, requested_clock_horses=[],
                                 requested_prior_history_confirmation=True)
                    confirmable_result(proof, metadata, runners)
                    proofs.append(proof)
                    failures = 0
                except FetchAccessBlocked as exc:
                    held.append({"race_id": race, "reason": str(exc), "provider_stop": True})
                    stop = "provider_blocked"
                    break
                except ValueError as exc:
                    held.append({"race_id": race, "reason": str(exc), "requires_separate_repair": True})
                    failures += 1
            if len(proofs) + len(held) == len(targets):
                stop = "all_linked_result_targets_checked"
        network = metrics.get("network_requests", 0)
    freeze_json(root / "race-proofs.json", proofs)
    freeze_json(root / "career-audit.json", read_json(coverage / "career-audit.json"))
    result = {
        "verified_source_receipts": len(proofs), "linked_result_races": len(targets),
        "source_targets_remaining": len(targets) - len(proofs) - len(held),
        "held": held, "receipts": receipts, "network_requests": network, "stop_reason": stop,
        "production_changed": False, "research_only": True,
    }
    if file_sha256(source) != task["source_sha256"] or _protected() != task["protected"]:
        raise ValueError("linked-history-source-or-production-changed-during-audit")
    freeze_json(root / "verification.json", result)
    freeze_json(root / "artifacts.json", {p.name: file_sha256(p) for p in root.iterdir() if p.is_file()})
    return result


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--action", choices=["audit", "apply"], required=True)
    parser.add_argument("--root", type=Path, required=True)
    parser.add_argument("--coverage", type=Path)
    parser.add_argument("--baseline", type=Path)
    parser.add_argument("--evidence", type=Path)
    parser.add_argument("--max-races", type=int, default=50)
    args = parser.parse_args()
    root = args.root.resolve()
    if root.parent != BASE.resolve() or root.exists() or args.max_races < 0:
        parser.error("a new feature-gap root and nonnegative result-source limit are required")
    if args.action == "audit":
        if args.coverage is None:
            parser.error("--coverage is required")
        result = asyncio.run(audit(root, args.coverage.resolve(strict=True), args.max_races))
    else:
        if args.baseline is None or args.evidence is None:
            parser.error("--baseline and --evidence are required")
        evidence = args.evidence.resolve(strict=True)
        if not read_json(evidence / "race-proofs.json"):
            parser.error("there are no whole-source-confirmed repairs to apply")
        result = original.apply(root, args.baseline.resolve(strict=True), evidence)
        result["independent_replay"] = independent.verify(root)
    print(json.dumps(result, ensure_ascii=False), flush=True)
