"""Audit residual public sources and apply only proven class/clock observations."""
from __future__ import annotations

import argparse
import asyncio
from contextlib import closing
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import shutil
import sqlite3
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / "python-api"), str(ROOT / "keiba")]

import aiohttp
from bs4 import BeautifulSoup
import pandas as pd

from experiments.common import file_sha256, read_json
from experiments.dataset import build_dataset
from experiments.search import freeze_json
from scraping.acquisition_horse_history import prepare_history
from scraping.fetch_pipeline import FetchAccessBlocked, decode_html_body, fetch_context, fetch_text
from scraping.horse_history_ajax import history_url
from scraping.last3f_evidence import classify_last3f
from scraping.missingness_goal import _protected
from scraping.race_class import explicit_race_class
from scraping.race_history_evidence import parse_history_evidence
from scraping.quality import classify_race_quality, record_race_quality
from scraping.safe_repair import _journal, encode, initialize_journal
from scraping.saved_horse_reuse import _present

BASE = ROOT / "reports" / "generated" / "data-quality" / "feature-gap"
IDENTITY = ["race_id", "horse_id", "horse_number", "race_date"]


def readonly(path):
    return sqlite3.connect(path.resolve(strict=True).as_uri() + "?mode=ro", uri=True)


def race_evidence(html, **kwargs):
    proof = parse_history_evidence(html, **kwargs)
    soup = BeautifulSoup(html, "lxml")
    nodes = soup.select("p.smalltxt,div.smalltxt,.RaceData02,.RaceHeader_Value,.mainrace_data h1")
    proof["explicit_race_class"] = explicit_race_class(*(node.get_text(" ", strip=True) for node in nodes))
    table = next(table for table in soup.select("table.race_table_01,table.RaceTable01,table.ResultsByRaceDetail")
                 if "着順" in [th.get_text(strip=True) for th in table.find_all("th")])
    headers = [th.get_text(strip=True) for th in table.find_all("th")]
    raw_finishes = {}
    for tr in table.find_all("tr"):
        cells = tr.find_all("td")
        if len(cells) > max(headers.index("馬番"), headers.index("着順")):
            raw_finishes[int(cells[headers.index("馬番")].get_text(strip=True))] = cells[
                headers.index("着順")].get_text(strip=True)
    for row in proof["rows"]:
        row["finish_source_raw"] = raw_finishes[row["horse_number"]]
    return proof


async def audit(root, baseline, cohort, reuse=None):
    source = baseline / "research.db"
    frame = pd.read_parquet(cohort / "applied-dataset.parquet")
    root.mkdir()
    code = [Path(__file__), ROOT / "python-api" / "scraping" / "acquisition_horse_history.py",
            ROOT / "python-api" / "scraping" / "last3f_evidence.py",
            ROOT / "python-api" / "scraping" / "race_history_evidence.py",
            ROOT / "python-api" / "scraping" / "quality.py",
            ROOT / "python-api" / "scraping" / "race_class.py"]
    protected = _protected()
    source_sha = file_sha256(source)
    freeze_json(root / "contract.json", {
        "source": str(source), "source_sha256": source_sha,
        "cohort": str(cohort), "cohort_sha256": file_sha256(cohort / "applied-dataset.parquet"),
        "protected": protected, "code_hashes": {str(path): file_sha256(path) for path in code},
        "research_only": True, "network_interval_seconds": 2,
        "historical_prestart_capture_proven": False,
        "public_career_sample_is_not_global_coverage": True,
        "replayed_source_archive": str(reuse) if reuse else None,
        "replayed_source_archive_sha256": file_sha256(reuse / "sources.db") if reuse else None,
    })
    classes = frame.loc[frame.safe_race_class.isna(), "race_id"].drop_duplicates().tolist()
    clock_targets = frame.loc[frame.safe_past3_count.gt(0) & frame.safe_past3_avg_last3f_time.isna()]
    history = pd.read_parquet(cohort / "history-observations.parquet")
    clocks = {}
    for target in clock_targets.itertuples():
        prior = history.loc[history.horse_id.eq(target.horse_id) & history.race_date.lt(target.race_date)]
        for row in prior.sort_values(["race_date", "race_id"]).tail(3).itertuples():
            if not pd.notna(row.last_3f_time):
                clocks.setdefault(row.race_id, set()).add(row.horse_id)
    samples = frame.loc[frame.safe_prior_starts.eq(0)].groupby(
        frame.race_date.str[:4], sort=True).head(1)
    receipts, proofs, careers, errors = [], [], [], []
    with closing(readonly(source)) as original, closing(sqlite3.connect(root / "sources.db")) as archive:
        archive.execute("CREATE TABLE sources(url TEXT PRIMARY KEY,final_url TEXT,status INTEGER,body BLOB,fetched_at REAL)")
        with fetch_context(job_id=root.name) as metrics:
            async with aiohttp.ClientSession() as session:
                async def receive(url, cached_race=False):
                    cached = None
                    if reuse:
                        with closing(readonly(reuse / "sources.db")) as previous:
                            cached = previous.execute("SELECT url,final_url,status,body,fetched_at "
                                                      "FROM sources WHERE url=? AND status=200", (url,)).fetchone()
                    if cached_race and cached is None:
                        with closing(readonly(ROOT / "keiba" / "data" / "fetch_cache.db")) as cache:
                            cached = cache.execute(
                                "SELECT normalized_url,final_url,status,body,fetched_at FROM http_cache "
                                "WHERE normalized_url=? AND status=200", (url,)).fetchone()
                    if cached is not None:
                        row, origin = cached, "frozen_cached_response"
                    else:
                        result, _ = await fetch_text(
                            session, url, max_retries=1, allow_redirects=False, min_interval_sec=2,
                            total_timeout_sec=30, max_body_bytes=2_000_000, force_refresh=True)
                        row = (url, result.url, result.status, result.body,
                               (result.cache_snapshot or {}).get("fetched_at"))
                        origin = result.source
                    archive.execute("INSERT OR REPLACE INTO sources VALUES(?,?,?,?,?)", row)
                    archive.commit()
                    receipts.append({"url": url, "source": origin, "status": row[2]})
                    if row[2] != 200 or row[1].rstrip("/") != url.rstrip("/") or not row[4]:
                        raise ValueError(f"residual-transport-unverified: {url}; status={row[2]}")
                    return decode_html_body(row[3]), row

                for race in dict.fromkeys([*classes, *clocks]):
                    url = f"https://db.netkeiba.com/race/{race}/"
                    try:
                        html, row = await receive(url, cached_race=True)
                        metadata = json.loads(original.execute(
                            "SELECT data FROM races_ultimate WHERE race_id=?", (race,)).fetchone()[0])
                        runners = [json.loads(raw) for raw, in original.execute(
                            "SELECT data FROM race_results_ultimate WHERE race_id=?", (race,))]
                        digest = hashlib.sha256(row[3]).hexdigest()
                        proof = race_evidence(
                            html, race_id=race, source_url=url, expected_date=metadata["date"],
                            expected_runners=runners, observed_at_utc=datetime.fromtimestamp(
                                row[4], timezone.utc).isoformat(), raw_body_sha256=digest, raw_body=row[3],
                            allow_cached_transport_identity=True,
                            cache_receipt={"normalized_url": url, "status": 200,
                                           "body_sha256": digest, "fetched_at": row[4]})
                        proof["requested_class_repair"] = race in classes
                        proof["requested_clock_horses"] = sorted(clocks.get(race, set()))
                        proof["requested_prior_history_confirmation"] = False
                        proofs.append(proof)
                    except FetchAccessBlocked as exc:
                        errors.append({"race_id": race, "reason": str(exc), "provider_stop": True})
                        break
                    except ValueError as exc:
                        errors.append({"race_id": race, "reason": str(exc)})
                for target in samples.itertuples():
                    try:
                        profile, profile_row = await receive(f"https://db.netkeiba.com/horse/{target.horse_id}/")
                        response, response_row = await receive(history_url(target.horse_id))
                        _, evidence = prepare_history(profile, response, target.horse_id, target.race_date)
                        evidence.update({
                            "horse_id": target.horse_id, "target_race_id": target.race_id,
                            "profile_source": profile_row[0], "profile_sha256": hashlib.sha256(profile_row[3]).hexdigest(),
                            "ajax_source": response_row[0], "ajax_sha256": hashlib.sha256(response_row[3]).hexdigest(),
                            "profile_received_at_unix": profile_row[4], "ajax_received_at_unix": response_row[4],
                        })
                        evidence["confirmed_no_public_prior_start"] = (
                            evidence["coverage_matches_published_career_count"]
                            and evidence["strictly_prior_started_rows"] == 0
                            and evidence["unknown_prior_start_status_rows"] == 0)
                        inventory = evidence["strictly_prior_race_inventory"]
                        evidence["prior_races_absent_from_selected_history"] = [
                            item for item in inventory if item["race_id"] is not None
                            and not ((history.race_id.eq(item["race_id"]))
                                     & history.horse_id.eq(target.horse_id)).any()]
                        careers.append(evidence)
                    except FetchAccessBlocked as exc:
                        errors.append({"horse_id": target.horse_id, "reason": str(exc), "provider_stop": True})
                        break
                    except ValueError as exc:
                        errors.append({"horse_id": target.horse_id, "reason": str(exc)})
                prior_ids = dict.fromkeys(item["race_id"] for career in careers
                    for item in career["prior_races_absent_from_selected_history"] if item["scope"] == "jra")
                for race in prior_ids:
                    url = f"https://db.netkeiba.com/race/{race}/"
                    try:
                        html, row = await receive(url, cached_race=True)
                        metadata_row = original.execute("SELECT data FROM races_ultimate WHERE race_id=?", (race,)).fetchone()
                        if metadata_row is None:
                            raise ValueError("prior-race-not-yet-stored; full-result-acquisition-required")
                        metadata = json.loads(metadata_row[0])
                        runners = [json.loads(raw) for raw, in original.execute(
                            "SELECT data FROM race_results_ultimate WHERE race_id=?", (race,))]
                        digest = hashlib.sha256(row[3]).hexdigest()
                        proof = race_evidence(html, race_id=race, source_url=url, expected_date=metadata["date"],
                            expected_runners=runners, observed_at_utc=datetime.fromtimestamp(row[4], timezone.utc).isoformat(),
                            raw_body_sha256=digest, raw_body=row[3], allow_cached_transport_identity=True,
                            cache_receipt={"normalized_url": url, "status": 200, "body_sha256": digest, "fetched_at": row[4]})
                        proof.update(requested_class_repair=False, requested_clock_horses=[],
                                     requested_prior_history_confirmation=True)
                        proofs.append(proof)
                    except FetchAccessBlocked as exc:
                        errors.append({"race_id": race, "reason": str(exc), "provider_stop": True})
                        break
                    except ValueError as exc:
                        errors.append({"race_id": race, "reason": str(exc)})
            network = metrics.get("network_requests", 0)
    if file_sha256(source) != source_sha or _protected() != protected:
        raise ValueError("residual-audit-input-or-production-changed")
    freeze_json(root / "race-proofs.json", proofs)
    freeze_json(root / "career-audit.json", careers)
    result = {
        "verified_source_receipts": len(proofs), "class_races_requested": len(classes),
        "class_races_with_explicit_published_label": sum(
            bool(p["explicit_race_class"]) for p in proofs if p["requested_class_repair"]),
        "clock_races_requested": len(clocks), "career_samples": len(careers),
        "confirmed_no_public_prior_start_samples": sum(p["confirmed_no_public_prior_start"] for p in careers),
        "errors": errors, "receipts": receipts, "network_requests": network,
        "production_changed": False, "research_only": True,
    }
    freeze_json(root / "verification.json", result)
    freeze_json(root / "artifacts.json", {p.name: file_sha256(p) for p in root.iterdir() if p.is_file()})
    return result


def apply(root, baseline, evidence):
    source = baseline / "research.db"
    task = read_json(evidence / "contract.json")
    for path, digest in task["code_hashes"].items():
        if file_sha256(Path(path)) != digest:
            raise ValueError("residual-evidence-parser-changed")
    for name, digest in read_json(evidence / "artifacts.json").items():
        if file_sha256(evidence / name) != digest:
            raise ValueError("residual-evidence-artifact-changed")
    if file_sha256(Path(task["source"])) != task["source_sha256"] or _protected() != task["protected"]:
        raise ValueError("residual-evidence-input-or-production-changed")
    root.mkdir()
    source_sha = file_sha256(source)
    freeze_json(root / "contract.json", {
        "source": str(source), "source_sha256": source_sha, "evidence": str(evidence),
        "evidence_sha256": file_sha256(evidence / "verification.json"), "protected": task["protected"],
        "code_sha256": file_sha256(Path(__file__)), "research_only": True,
    })
    target = root / "research.db"
    shutil.copyfile(source, target)
    initialize_journal(target)
    mutations, held = [], []
    proofs = read_json(evidence / "race-proofs.json")
    with closing(sqlite3.connect(target)) as db:
        quality_confirmations = []
        for proof in proofs:
            race = proof["race_id"]
            if proof["requested_prior_history_confirmation"]:
                metadata = json.loads(db.execute("SELECT data FROM races_ultimate WHERE race_id=?", (race,)).fetchone()[0])
                verified_horses = []
                for row_id, raw in db.execute("SELECT id,data FROM race_results_ultimate WHERE race_id=?", (race,)).fetchall():
                    item = json.loads(raw)
                    observed = next(row for row in proof["rows"] if row["horse_id"] == item["horse_id"])
                    from experiments.dataset import _race_metadata, _runner
                    meta, meta_reason = _race_metadata(race, metadata)
                    if meta_reason:
                        raise ValueError("prior-history-metadata-conflict")
                    canonical, reason = _runner(race, item, meta)
                    if reason:
                        raise ValueError("prior-history-source-identity-conflict")
                    if observed["finish"] is not None:
                        if (observed["time_seconds"] is None or canonical["finish"] != observed["finish"]
                                or abs(canonical["time_seconds"] - observed["time_seconds"]) > 1e-6):
                            raise ValueError("prior-history-source-outcome-conflict")
                    elif observed["finish_source_raw"] in {"取", "取消", "除", "除外", "中", "中止", "失", "失格"}:
                        if pd.notna(canonical["finish"]):
                            raise ValueError("prior-history-known-finish-conflict")
                        item.update(finish_position=observed["finish_source_raw"], _finish_source=proof["source"])
                        fresh = encode(item)
                        with db:
                            cursor = db.execute("UPDATE race_results_ultimate SET data=? WHERE id=? AND data=?",
                                                (fresh, row_id, raw))
                            if cursor.rowcount != 1:
                                raise ValueError("prior-history-before-image-changed")
                            _journal(db, root.name, "race_results_ultimate", row_id, race, raw, fresh, proof["source"])
                    else:
                        raise ValueError("prior-history-nonfinish-not-published")
                    verified_horses.append(item)
                report = classify_race_quality({"race_info": metadata, "horses": verified_horses})
                if not report.valid_for_date_completion:
                    raise ValueError("prior-history-source-quality-not-complete")
                quality_confirmations.append((race, metadata["date"], report))
                mutations.append({"race_id": race, "kind": "source_confirmed_prior_race_quality"})
            if proof["requested_class_repair"] and proof["explicit_race_class"]:
                raw = db.execute("SELECT data FROM races_ultimate WHERE race_id=?", (race,)).fetchone()[0]
                item = json.loads(raw)
                if not _present(item.get("race_class")):
                    item.update(race_class=proof["explicit_race_class"], _race_class_source=proof["source"])
                    fresh = encode(item)
                    with db:
                        cursor = db.execute("UPDATE races_ultimate SET data=? WHERE race_id=? AND data=?",
                                            (fresh, race, raw))
                        if cursor.rowcount != 1:
                            raise ValueError("residual-class-before-image-changed")
                        _journal(db, root.name, "races_ultimate", race, race, raw, fresh, proof["source"])
                    mutations.append({"race_id": race, "kind": "explicit_class", "value": item["race_class"]})
            for horse in proof["requested_clock_horses"]:
                observed = next(row for row in proof["rows"] if row["horse_id"] == horse)
                if observed["last3f_state"] != "valid":
                    held.append({"race_id": race, "horse_id": horse, "state": observed["last3f_state"]})
                    continue
                row_id, raw = db.execute(
                    "SELECT id,data FROM race_results_ultimate WHERE race_id=? AND "
                    "json_extract(data,'$.horse_id')=?", (race, horse)).fetchone()
                item = json.loads(raw)
                state, value = classify_last3f(item.get("last_3f_time"))
                if state == "valid":
                    if value != observed["last3f_seconds"]:
                        raise ValueError("residual-clock-existing-observation-conflict")
                    continue
                item.update(last_3f_time=observed["last3f_seconds"], _last3f_source=proof["source"])
                fresh = encode(item)
                with db:
                    cursor = db.execute("UPDATE race_results_ultimate SET data=? WHERE id=? AND data=?",
                                        (fresh, row_id, raw))
                    if cursor.rowcount != 1:
                        raise ValueError("residual-clock-before-image-changed")
                    _journal(db, root.name, "race_results_ultimate", row_id, race, raw, fresh, proof["source"])
                mutations.append({"race_id": race, "horse_id": horse, "kind": "published_clock",
                                  "value": observed["last3f_seconds"]})
        db.execute("PRAGMA wal_checkpoint(TRUNCATE)")
        db.execute("PRAGMA journal_mode=DELETE")
    for race, day, report in quality_confirmations:
        record_race_quality(target, race_id=race, race_date=day, report=report,
                            source_status="source_verified_full_result", record_observation=False)
    with closing(sqlite3.connect(target)) as db:
        db.execute("PRAGMA wal_checkpoint(TRUNCATE)")
        db.execute("PRAGMA journal_mode=DELETE")
    build_dataset(target, root / "dataset")
    before = pd.read_parquet(baseline / "dataset" / "dataset.parquet")
    after = pd.read_parquet(root / "dataset" / "dataset.parquet")
    allowed = ["race_class", "safe_race_class", "last_3f_time",
               *[f"safe_past{w}_avg_last3f_time" for w in (3, 5, 10)]]
    pd.testing.assert_frame_equal(before[IDENTITY], after[IDENTITY])
    pd.testing.assert_index_equal(before.columns, after.columns)
    unchanged = [column for column in before if column not in allowed]
    pd.testing.assert_frame_equal(before[unchanged], after[unchanged])
    if file_sha256(source) != source_sha or _protected() != task["protected"]:
        raise ValueError("residual-application-input-or-production-changed")
    result = {"verified": True, "mutations": mutations, "held_observations": held,
              "same_target_cohort": True, "unrelated_features_unchanged": True, "production_changed": False}
    freeze_json(root / "verification.json", result)
    return result


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--action", choices=["audit", "apply"], required=True)
    parser.add_argument("--root", type=Path, required=True)
    parser.add_argument("--baseline", type=Path, required=True)
    parser.add_argument("--cohort", type=Path)
    parser.add_argument("--evidence", type=Path)
    parser.add_argument("--reuse-sources", type=Path)
    args = parser.parse_args()
    root, baseline = args.root.resolve(), args.baseline.resolve(strict=True)
    if root.parent != BASE.resolve() or root.exists():
        parser.error("a new output root under feature-gap is required")
    if args.action == "audit":
        if args.cohort is None:
            parser.error("--cohort is required for audit")
        result = asyncio.run(audit(root, baseline, args.cohort.resolve(strict=True),
                                  args.reuse_sources.resolve(strict=True) if args.reuse_sources else None))
    else:
        if args.evidence is None:
            parser.error("--evidence is required for apply")
        result = apply(root, baseline, args.evidence.resolve(strict=True))
    print(json.dumps(result, ensure_ascii=False), flush=True)
