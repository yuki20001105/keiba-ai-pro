"""Check actual Ajax-loaded career coverage without changing saved campaigns."""
from __future__ import annotations

import argparse
import asyncio
from contextlib import closing
import hashlib
import json
from pathlib import Path
import sqlite3
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / "python-api"), str(ROOT / "keiba"), str(ROOT / "scripts")]

import aiohttp

from audit_arima_history_gaps import parse_profile
import repair_pedigree_identities as repair
from experiments.common import file_sha256, read_json
from experiments.search import freeze_json
from scraping.fetch_pipeline import FetchAccessBlocked, decode_html_body, fetch_context, fetch_text
from scraping.horse_history_ajax import combine_profile_history, history_url


async def run(root, stage):
    root, stage = root.resolve(), stage.resolve(strict=True)
    if root.parent != (ROOT / "reports" / "generated" / "data-quality" / "feature-gap").resolve():
        raise ValueError("horse-history-audit-output-outside-feature-gap")
    root.mkdir()
    profiles = stage / "career-sources.db"
    remaining = read_json(stage / "verification.json")["remaining"]
    freeze_json(root / "task-ledger.json", {
        "stage_verification_sha256": file_sha256(stage / "verification.json"),
        "profile_archive_sha256": file_sha256(profiles),
        "code_hashes": {str(path): file_sha256(path) for path in (
            Path(__file__), ROOT / "scripts" / "audit_arima_history_gaps.py",
            ROOT / "python-api" / "scraping" / "horse_history_ajax.py")},
        "network_interval_seconds": 2, "historical_prestart_availability_proven": False,
        "production_writes_authorized": False,
    })
    results, errors = [], []
    with closing(sqlite3.connect(root / "source-archive.db")) as archive, closing(
        repair.readonly(profiles)
    ) as before, fetch_context(job_id=root.name) as metrics:
        archive.execute("CREATE TABLE sources(url TEXT PRIMARY KEY,final_url TEXT,status INTEGER,body BLOB,fetched_at REAL)")
        async with aiohttp.ClientSession() as session:
            for item in remaining:
                horse = item["horse_id"]
                profile_url = f"https://db.netkeiba.com/horse/{horse}/"
                profile, profile_stamp = before.execute(
                    "SELECT body,fetched_at FROM sources WHERE url=?", (profile_url,)).fetchone()
                url = history_url(horse)
                try:
                    response, payload = await fetch_text(
                        session, url, min_interval_sec=2, max_retries=1, allow_redirects=False,
                        total_timeout_sec=30, max_body_bytes=2_000_000)
                except FetchAccessBlocked as exc:
                    errors.append({"horse_id": horse, "reason": str(exc)})
                    break
                stamp = (response.cache_snapshot or {}).get("fetched_at")
                archive.execute("INSERT INTO sources VALUES(?,?,?,?,?)",
                                (url, response.url, response.status, response.body, stamp))
                archive.commit()
                if response.status != 200 or response.url != url or stamp is None:
                    errors.append({"horse_id": horse, "reason": "ajax-transport-unconfirmed"})
                    continue
                try:
                    merged = combine_profile_history(decode_html_body(profile), payload, horse)
                    result = parse_profile(merged, horse, "20251228", item["columns"])
                except ValueError as exc:
                    errors.append({"horse_id": horse, "reason": str(exc)})
                    continue
                result.update(
                    profile_source_url=profile_url, profile_observed_at_unix=profile_stamp,
                    profile_sha256=hashlib.sha256(profile).hexdigest(),
                    history_source_url=url, history_observed_at_unix=stamp,
                    history_sha256=hashlib.sha256(response.body).hexdigest())
                results.append(result)
    result = {"results": results, "errors": errors, "network_requests": metrics.get("network_requests", 0)}
    freeze_json(root / "verification.json", result)
    freeze_json(root / "artifacts.json", {str(path.relative_to(root)): file_sha256(path)
                                        for path in sorted(root.rglob("*")) if path.is_file()})
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, required=True)
    parser.add_argument("--stage", type=Path, required=True)
    args = parser.parse_args()
    print(json.dumps(asyncio.run(run(args.root, args.stage)), ensure_ascii=False))


if __name__ == "__main__":
    main()
