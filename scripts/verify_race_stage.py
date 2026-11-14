"""Verify one race before resuming full-period pedigree acquisition."""
from __future__ import annotations

import argparse
import asyncio
from contextlib import closing
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import sqlite3
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / "python-api"), str(ROOT / "keiba"), str(ROOT / "scripts")]

import aiohttp
import pandas as pd

import repair_pedigree_identities as repair
from experiments import dataset as ds
from experiments.common import file_sha256, read_json
from experiments.features import _band
from experiments.search import freeze_json
from scraping.fetch_pipeline import FetchAccessBlocked, decode_html_body, fetch_context, fetch_text
from scraping.race_history_evidence import backfill_conditionals, parse_history_evidence


async def sources(root, required):
    archive = root / "history-sources.db"
    with closing(sqlite3.connect(archive)) as db:
        db.execute("CREATE TABLE sources(url TEXT PRIMARY KEY,final_url TEXT,status INTEGER,body BLOB,fetched_at REAL)")
        with closing(repair.readonly(ROOT / "keiba" / "data" / "fetch_cache.db")) as cache:
            for race in required:
                url = f"https://db.netkeiba.com/race/{race}/"
                row = cache.execute("SELECT normalized_url,final_url,status,body,fetched_at "
                                    "FROM http_cache WHERE normalized_url=?", (url,)).fetchone()
                if row is not None:
                    db.execute("INSERT INTO sources VALUES(?,?,?,?,?)", row)
        db.commit()
        with fetch_context(job_id=root.name) as metrics:
            async with aiohttp.ClientSession() as session:
                for race in sorted(required):
                    url = f"https://db.netkeiba.com/race/{race}/"
                    if db.execute("SELECT 1 FROM sources WHERE url=? AND status=200", (url,)).fetchone():
                        continue
                    try:
                        response, _ = await fetch_text(
                            session, url, min_interval_sec=2.0, max_retries=1, allow_redirects=False,
                            total_timeout_sec=30, max_body_bytes=2_000_000)
                    except FetchAccessBlocked as exc:
                        freeze_json(root / "collection.json",
                                    {"network_requests": metrics.get("network_requests", 0),
                                     "stop_reason": "provider_blocked", "error": str(exc)})
                        return
                    db.execute("INSERT OR REPLACE INTO sources VALUES(?,?,?,?,?)",
                               (url, response.url, response.status, response.body,
                                (response.cache_snapshot or {}).get("fetched_at")))
                    db.commit()
        freeze_json(root / "collection.json", {"network_requests": metrics.get("network_requests", 0)})


def run(root, baseline, race_id):
    root, baseline = root.resolve(), baseline.resolve(strict=True)
    if root.parent != (ROOT / "reports" / "generated" / "data-quality" / "feature-gap").resolve():
        raise ValueError("race-stage-output-outside-feature-gap")
    root.mkdir()
    database = baseline / "research.db"
    data_path = baseline / "dataset" / "dataset.parquet"
    manifest = read_json(baseline / "dataset" / "dataset_manifest.json")
    if file_sha256(data_path) != manifest["dataset_sha256"]:
        raise ValueError("race-stage-dataset-hash-mismatch")
    source_hash = file_sha256(database)
    frame = pd.read_parquet(data_path)
    target = frame.loc[frame.race_id.eq(race_id)].copy()
    meta, raw = repair.race_rows(database, race_id)
    if (len(target) != len(raw) or len(raw) != int(meta["num_horses"])
            or target.horse_id.nunique() != len(raw)):
        raise ValueError("race-stage-incomplete-runner-cohort")
    features = manifest["feature_sets"]["M03"]
    missing_before = target[features].isna().sum()
    groups = {}
    for _, row in target.iterrows():
        groups[row.horse_id] = [
            name for name in ("horse_course", "horse_distance_band")
            if row[f"safe_{name}_starts"] == 0
            and row[[f"safe_{name}_{field}" for field in ("win_rate", "top3_rate", "avg_speed_mps")]].isna().all()
        ]
    candidates, required, rejected = [], {}, []
    with closing(repair.readonly(database)) as db:
        for horse, names in groups.items():
            if not names:
                continue
            for rid, payload, metadata in db.execute(
                "SELECT r.race_id,r.data,m.data FROM race_results_ultimate r "
                "JOIN races_ultimate m USING(race_id) WHERE json_extract(r.data,'$.horse_id')=?", (horse,)
            ):
                normalized, reason = ds._race_metadata(rid, json.loads(metadata))
                if normalized is None or normalized["race_date"] >= str(target.race_date.iloc[0]):
                    continue
                row, reason = ds._runner(rid, json.loads(payload), normalized)
                if reason or not (row["finish"] >= 1 and row["time_seconds"] > 0):
                    rejected.append({"race_id": rid, "horse_id": horse, "reason": reason or "result-unavailable"})
                    continue
                wanted = (
                    "horse_course" in names and row["venue"] == target.venue.iloc[0] and row["surface"] == target.surface.iloc[0]
                    or "horse_distance_band" in names
                    and _band(row["distance"]) == _band(target.distance.iloc[0])
                )
                if wanted:
                    candidates.append(row)
                    required[rid] = normalized
        expected = {rid: [json.loads(payload) for payload, in db.execute(
            "SELECT data FROM race_results_ultimate WHERE race_id=?", (rid,))] for rid in required}
    freeze_json(root / "task-ledger.json", {
        "race_id": race_id, "source": str(database), "source_sha256": source_hash,
        "dataset_sha256": manifest["dataset_sha256"], "protected": repair._protected(),
        "code_hashes": {**repair.source_manifest(), **{str(path): file_sha256(path) for path in (
            Path(__file__), ROOT / "python-api" / "scraping" / "race_history_evidence.py",
            ROOT / "python-api" / "scraping" / "last3f_evidence.py")}},
        "research_only_conditional_history_policy": True, "production_activation": False,
    })
    asyncio.run(sources(root, required))
    proofs = {}
    with closing(repair.readonly(root / "history-sources.db")) as db:
        for rid, normalized in required.items():
            url = f"https://db.netkeiba.com/race/{rid}/"
            source = db.execute("SELECT final_url,status,body,fetched_at FROM sources WHERE url=?", (url,)).fetchone()
            if source is None or source[1] != 200 or source[3] is None or source[0] != url:
                rejected.append({"race_id": rid, "reason": "source-transport-unconfirmed"})
                continue
            final_url, status, body, captured = source
            digest = hashlib.sha256(body).hexdigest()
            try:
                proofs[rid] = parse_history_evidence(
                    decode_html_body(body), race_id=rid, source_url=final_url,
                    observed_at_utc=datetime.fromtimestamp(captured, timezone.utc).isoformat(),
                    raw_body_sha256=digest, expected_date=normalized["race_date"],
                    expected_runners=expected[rid], raw_body=body,
                    allow_cached_transport_identity=True, cache_receipt={
                        "normalized_url": url, "status": status, "body_sha256": digest, "fetched_at": captured})
            except ValueError as exc:
                rejected.append({"race_id": rid, "reason": str(exc)})
    confirmed = []
    for candidate in candidates:
        proof = proofs.get(candidate["race_id"])
        if proof is None:
            continue
        matching = [row for row in proof["rows"] if row["horse_id"] == candidate["horse_id"]]
        if (len(matching) != 1 or matching[0]["finish"] != candidate["finish"]
                or matching[0]["time_seconds"] is None
                or abs(matching[0]["time_seconds"] - candidate["time_seconds"]) > 1e-6):
            rejected.append({"race_id": candidate["race_id"], "horse_id": candidate["horse_id"],
                             "reason": "source-finish-or-time-disagrees"})
            continue
        confirmed.append(candidate)
    improved, changes = backfill_conditionals(target, confirmed)
    improved[["horse_number", "horse_id", *features]].to_csv(root / "race-features.csv", index=False)
    freeze_json(root / "race-raw.json", {"race": meta, "runners": raw})
    freeze_json(root / "history-proofs.json", proofs)
    freeze_json(root / "history-confirmed.json", confirmed)
    freeze_json(root / "history-changes.json", changes)
    freeze_json(root / "history-rejected.json", rejected)
    after = improved[features].isna().sum()
    remainder = []
    for _, row in improved.iterrows():
        columns = [name for name in features if pd.isna(row[name])]
        if columns:
            remainder.append({
                "horse_id": row.horse_id, "horse_number": int(row.horse_number), "columns": columns,
                "reason": "No source-confirmed prior same-condition record in frozen DB; not proof of complete career.",
            })
    task = read_json(root / "task-ledger.json")
    if file_sha256(database) != source_hash or repair._protected() != task["protected"]:
        raise ValueError("race-stage-frozen-source-changed")
    sire_missing = sum(not repair._present(row.get("sire")) for row in raw)
    damsire_missing = sum(not repair._present(row.get("damsire")) for row in raw)
    result = {
        "verified": True, "race_id": race_id, "runners": len(raw), "model_features": len(features),
        "raw_sire_missing": sire_missing,
        "raw_damsire_missing": damsire_missing,
        "raw_sire_id_missing": sum(not repair._present(row.get("sire_id")) for row in raw),
        "raw_damsire_id_missing": sum(not repair._present(row.get("damsire_id")) for row in raw),
        "feature_missing_before": int(missing_before.sum()), "feature_missing_after": int(after.sum()),
        "missing_features_after": {name: int(count) for name, count in after.items() if count},
        "confirmed_history_rows": len(confirmed), "source_races": len(proofs), "remaining": remainder,
        "all_72_features_zero_missing": int(after.sum()) == 0,
        "parent_names_zero_missing": sire_missing == 0 and damsire_missing == 0,
        "full_period_acquisition_resume_approved": False,
        "production_result_db_or_model_changed": False, "historical_prestart_availability_proven": False,
    }
    freeze_json(root / "verification.json", result)
    freeze_json(root / "artifacts.json", {str(path.relative_to(root)): file_sha256(path)
                                        for path in sorted(root.rglob("*")) if path.is_file()})
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, required=True)
    parser.add_argument("--baseline", type=Path, required=True)
    parser.add_argument("--race-id", default="202506050811")
    args = parser.parse_args()
    print(json.dumps(run(args.root, args.baseline, args.race_id), ensure_ascii=False))


if __name__ == "__main__":
    main()
