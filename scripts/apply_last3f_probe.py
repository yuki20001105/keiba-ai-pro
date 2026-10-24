"""Replay the three saved public probes, optionally filling real clock gaps."""
from __future__ import annotations

import argparse
from contextlib import closing
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import sqlite3
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / "python-api"), str(ROOT / "scripts")]
from evaluate_db_shared_venues import preservation_receipt
from scraping.acquisition_scope import normalized_date
from scraping.fetch_pipeline import decode_html_body
from scraping.last3f_evidence import parse_last3f_evidence
from scraping.last3f_gap_repair import apply_evidence
from scraping.safe_repair import load_saved


def write_new(path, data):
    with path.open("xb") as stream:
        stream.write((json.dumps(data, ensure_ascii=False, indent=2, allow_nan=False) + "\n").encode())


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--probe", type=Path, required=True)
    parser.add_argument("--expected-probe-sha256", required=True)
    parser.add_argument("--run-dir", type=Path, required=True)
    parser.add_argument("--apply", action="store_true")
    args = parser.parse_args()
    probe = args.probe.resolve(strict=True)
    if hashlib.sha256(probe.read_bytes()).hexdigest() != args.expected_probe_sha256:
        raise ValueError("probe manifest changed")
    run = args.run_dir.resolve()
    if run.parent != (ROOT / "reports/generated/data-quality/feature-gap").resolve() or run.exists():
        raise ValueError("new isolated feature-gap output required")
    data = json.loads(probe.read_text(encoding="utf-8"))
    records = data["records"]
    if {row["race_id"] for row in records} != {"202001010101", "202101010101", "202501020212"} or len(records) != 3:
        raise ValueError("only the three explicitly verified public probes are in scope")
    before = preservation_receipt()
    db_path = ROOT / "keiba/data/keiba_ultimate.db"
    verified = []
    for record in records:
        source_path = Path(record["body_path"]).resolve(strict=True)
        if source_path.parent != probe.parent or source_path.name != record["race_id"] + ".html":
            raise ValueError("source body path not bound to probe")
        body = source_path.read_bytes()
        if hashlib.sha256(body).hexdigest() != record["body_sha256"]:
            raise ValueError("source body changed")
        observed = datetime.fromisoformat(record["observed_at_utc"].replace("Z", "+00:00"))
        if observed.tzinfo is None or observed > datetime.now(timezone.utc):
            raise ValueError("real nonfuture observation time required")
        with closing(sqlite3.connect(db_path.resolve().as_uri() + "?mode=ro", uri=True)) as db:
            db.execute("PRAGMA query_only=ON")
            payload = load_saved(db, record["race_id"])
        evidence = parse_last3f_evidence(decode_html_body(body), race_id=record["race_id"],
            source_url=record["source_url"], observed_at_utc=record["observed_at_utc"],
            raw_body_sha256=record["body_sha256"], raw_body=body, http_status=record["http_status"],
            expected_date=normalized_date(payload["race_info"]["date"]), expected_runners=payload["horses"])
        verified.append(evidence)
    run.mkdir(parents=True, exist_ok=False)
    write_new(run / "preflight.json", {"protected_before": before, "probe_sha256": args.expected_probe_sha256,
        "apply": args.apply, "network_requests": 0, "evidence": verified})
    results = [apply_evidence(db_path, evidence, batch_id=run.name) for evidence in verified] if args.apply else []
    after = preservation_receipt()
    preserved = all(after.get(key) == value for key, value in before.items() if key != "forward_processes")
    result = {"schema": "last3f-public-probe-replay-v1", "batch_id": run.name,
        "status": "applied" if args.apply else "dry_run_verified", "results": results,
        "updated_runners": sum(item["updated_runners"] for item in results), "network_requests": 0,
        "protected_state_unchanged": preserved, "protected_after": after,
        "original_prestart_observation_proven": False, "zero_fill": False}
    write_new(run / "receipt.json", result)
    if not preserved:
        raise ValueError("protected state changed")
    print(json.dumps({key: value for key, value in result.items() if key != "protected_after"}, ensure_ascii=False))


if __name__ == "__main__":
    main()
