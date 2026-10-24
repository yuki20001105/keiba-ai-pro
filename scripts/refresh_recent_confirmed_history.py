"""Once-only result refresh of today's already registered JRA flat schedule.

Default creates an isolated sealed plan only. --execute --allow-network is an
explicit bounded authorization: one sequential result request per incomplete
race, no retries/entry fallback/horse profiles, no changes to the forward ledger.
"""
from __future__ import annotations

import argparse
import asyncio
from collections import Counter
from datetime import datetime, timedelta, timezone
import hashlib
import json
from pathlib import Path
import re
import sqlite3
import sys
from urllib.parse import urlsplit

ROOT = Path(__file__).resolve().parents[1]
for package in (ROOT / "scripts", ROOT / "python-api", ROOT / "keiba"):
    if str(package) not in sys.path:
        sys.path.insert(0, str(package))
import prepare_h04_history_revision as preparation
from repair_source_verification import source_date, validate_result_source
from scraping.acquisition_scope import is_jra_race_id, validate_scoped_payload
from scraping.quality import classify_race_quality, record_race_quality
from scraping.safe_repair import load_saved, save_result_repair, RepairConflict

SCHEMA = "recent-confirmed-jra-history-refresh-v1"
OUTPUT_ROOT = ROOT / "reports/generated/recent-confirmed-history"
UTC = timezone.utc


def read_schedule(runtime_root, *, now, require_finished=False):
    day = now.astimezone(preparation.JST).strftime("%Y%m%d")
    path = (Path(runtime_root) / "ledger.sqlite3").resolve(strict=True)
    db = sqlite3.connect(path.as_uri() + "?mode=ro", uri=True, timeout=5)
    try:
        db.execute("PRAGMA query_only=ON"); db.execute("BEGIN")
        recorded = db.execute("SELECT status,discovery_json,discovery_sha256 FROM schedule_days WHERE date=?", (day,)).fetchone()
        rows = list(db.execute("SELECT race_id,race_date,post_time,venue,race_json,status FROM races WHERE race_date=? ORDER BY race_id", (day,)))
    finally:
        db.rollback(); db.close()
    if recorded is None or recorded[0] != "complete":
        raise ValueError("complete-today-forward-schedule-required")
    discovery = json.loads(recorded[1])
    if preparation.object_hash(discovery) != recorded[2] or discovery.get("status") != "complete":
        raise ValueError("forward-discovery-proof-integrity-failed")
    expected = discovery.get("expected_race_ids", [])
    if not expected or len(set(expected)) != len(expected) or any(not is_jra_race_id(rid, day) for rid in expected):
        raise ValueError("registered-jra-expected-identity-required")
    excluded = discovery.get("excluded_races", [])
    excluded_ids = {row["race_id"] for row in excluded if row.get("reason") == "jump"}
    if len(excluded_ids) != len(excluded) or not excluded_ids.issubset(expected):
        raise ValueError("only-registered-jump-exclusions-allowed")
    targets = []
    for rid, date, post, venue, raw, status in rows:
        race = json.loads(raw)
        if (date != day or not is_jra_race_id(rid, day) or race.get("race_type") != "flat"
                or race.get("race_id") != rid or race.get("race_date") != day
                or race.get("post_time") != post or race.get("venue") != venue or venue != rid[4:6]):
            raise ValueError("registered-flat-race-identity-mismatch")
        parsed = datetime.fromisoformat(post.replace("Z", "+00:00"))
        if parsed.tzinfo is None or parsed.astimezone(preparation.JST).strftime("%Y%m%d") != day:
            raise ValueError("registered-post-time-date-mismatch")
        targets.append({"race_id": rid, "race_date": day, "post_time": post, "venue": venue})
        if require_finished and status in ("queued", "capturing"):
            raise ValueError("natural-captures-not-finished")
    if {row["race_id"] for row in targets} != set(expected) - excluded_ids:
        raise ValueError("registered-flat-targets-do-not-cover-fixed-schedule")
    latest = max(datetime.fromisoformat(row["post_time"].replace("Z", "+00:00")) for row in targets)
    if require_finished and now.astimezone(UTC) < latest.astimezone(UTC) + timedelta(minutes=5):
        raise ValueError("wait-until-final-registered-post-plus-five-minutes")
    projection = {"race_date": day, "expected_race_ids": sorted(expected),
        "excluded_races": sorted(excluded, key=lambda item: item["race_id"]), "targets": targets}
    return {**projection, "projection_sha256": preparation.object_hash(projection),
        "latest_post_time": latest.isoformat(), "forward_ledger_read_only": True}


def new_output(run_dir, *, output_root=OUTPUT_ROOT):
    path, base = Path(run_dir), Path(output_root).resolve()
    resolved = path.resolve()
    if path.is_symlink() or resolved == base or not resolved.is_relative_to(base):
        raise ValueError("isolated-new-recent-history-run-directory-required")
    if path.exists():
        raise ValueError("new-plan-directory-must-not-exist")
    path.mkdir(parents=True, exist_ok=False)
    return resolved


def make_plan(run_dir, *, now=None, runtime_root=preparation.RUNTIME_ROOT, source_db=preparation.SOURCE_DB):
    current = now or datetime.now(UTC)
    schedule = read_schedule(runtime_root, now=current)
    protected = preparation.protection_receipt(runtime_root=runtime_root)
    source = preparation.source_epoch_receipt(source_db)
    output = new_output(run_dir)
    value = {"schema": SCHEMA, "batch_id": output.name, "created_at_utc": current.astimezone(UTC).isoformat(),
        "runtime_root": str(Path(runtime_root).resolve()), "source_database": str(Path(source_db).resolve()),
        "schedule": schedule, "protected_before": protected, "source_revision_at_plan": source,
        "worker_count": 1, "maximum_result_requests_per_race": 1, "network_retries": 0,
        "skip_normal_complete_saved_data": True, "normal_data_overwrite": False,
        "forward_ledger_or_bindings_modified": False, "network_performed": False}
    preparation.write_new_json(output / "plan.json", value)
    preparation.write_new_json(output / "plan_receipt.json", {"schema": SCHEMA,
        "plan_sha256": preparation.file_hash(output / "plan.json")})
    return value


def read_plan(run_dir, *, now, runtime_root, source_db):
    directory = Path(run_dir).resolve(strict=True)
    data = preparation.read_json(directory / "plan.json")
    receipt = preparation.read_json(directory / "plan_receipt.json")
    if (data.get("schema") != SCHEMA or data.get("batch_id") != directory.name
            or receipt.get("plan_sha256") != preparation.file_hash(directory / "plan.json")
            or Path(data["source_database"]).resolve() != Path(source_db).resolve()
            or Path(data["runtime_root"]).resolve() != Path(runtime_root).resolve()):
        raise ValueError("recent-result-plan-seal-or-scope-invalid")
    latest = read_schedule(runtime_root, now=now, require_finished=True)
    if latest["projection_sha256"] != data["schedule"]["projection_sha256"]:
        raise ValueError("registered-schedule-changed-since-plan")
    if preparation.protection_receipt(runtime_root=runtime_root) != data["protected_before"]:
        raise ValueError("protected-h04-model-or-binding-changed-since-plan")
    return data, receipt["plan_sha256"]


class OneResultObserver:
    def __init__(self, original_fetch, output):
        self.original_fetch, self.output = original_fetch, Path(output)
        self.receipts, self.calls, self.active_race_id = {}, Counter(), None
        self.restriction_observed = False

    async def fetch(self, session, url, **kwargs):
        rid = self.active_race_id
        parsed = urlsplit(url)
        if (not rid or parsed.scheme != "https" or parsed.hostname not in ("db.netkeiba.com", "db.sp.netkeiba.com")
                or parsed.path != f"/race/{rid}/" or parsed.query or parsed.username or parsed.port not in (None, 443)):
            raise RepairConflict("result-only-endpoint-required-no-entry-or-profile-fallback")
        if self.calls[rid] >= 1:
            raise RepairConflict("one-result-request-per-race-budget-exhausted")
        self.calls[rid] += 1
        kwargs.update(max_retries=0, use_cache=False, resume_key=None, force_refresh=True,
            allow_redirects=False, min_interval_sec=max(1.0, float(kwargs.get("min_interval_sec", 1.0))))
        fetched, html = await self.original_fetch(session, url, **kwargs)
        if fetched.status in (403, 429) or fetched.source in ("access-blocked", "network-rejected"):
            self.restriction_observed = True
        if fetched.status == 200 and html:
            if fetched.source != "network" or fetched.normalized_url != url or fetched.url != url:
                raise RepairConflict("fresh-exact-result-network-response-required")
            raw_sha = hashlib.sha256(fetched.body).hexdigest()
            decoded = html.encode("utf-8"); decoded_sha = hashlib.sha256(decoded).hexdigest()
            raw_path = self.output / f"{rid}-{raw_sha}.html"
            decoded_path = self.output / f"{rid}-{decoded_sha}.decoded.html"
            raw_path.write_bytes(fetched.body); decoded_path.write_bytes(decoded)
            self.receipts.setdefault(rid, []).append({"url": url, "status": 200,
                "body_sha256": raw_sha, "html_path": str(raw_path), "parsed_source_date": source_date(html),
                "decoded_html_sha256": decoded_sha, "decoded_html_path": str(decoded_path),
                "fetch_source": fetched.source, "cache_snapshot": fetched.cache_snapshot,
                "observed_at_utc": datetime.now(UTC).isoformat()})
        return fetched, html


async def execute(run_dir, *, allow_network=False, now=None, runtime_root=preparation.RUNTIME_ROOT, source_db=preparation.SOURCE_DB):
    if not allow_network:
        raise ValueError("explicit-allow-network-required")
    clock = lambda: now or datetime.now(UTC)
    directory = Path(run_dir).resolve(strict=True)
    plan, plan_sha = read_plan(directory, now=clock(), runtime_root=runtime_root, source_db=source_db)
    # An interrupted/failed invocation is never silently retried. The saved
    # claim remains evidence of an unknown attempt and requires a new decision.
    preparation.write_new_json(directory / "execution.claim.json", {"plan_sha256": plan_sha,
        "started_at_utc": clock().astimezone(UTC).isoformat(), "one_execution_only": True})
    from scraping import race, fetch_pipeline
    from scraping.constants import get_random_headers
    import aiohttp
    sources = directory / "sources"; sources.mkdir(exist_ok=False)
    observed = OneResultObserver(race.fetch_text, sources)
    previous_fetch = race.fetch_text
    race.fetch_text = observed.fetch
    source_before = preparation.source_epoch_receipt(source_db)
    outcomes, metrics, halt = [], {}, None
    try:
        with fetch_pipeline.fetch_context(job_id=plan["batch_id"]) as context:
            async with aiohttp.ClientSession(headers=get_random_headers(), connector=aiohttp.TCPConnector(limit=1, limit_per_host=1),
                timeout=aiohttp.ClientTimeout(total=70)) as session:
                for target in plan["schedule"]["targets"]:
                    # The forward schedule and post-five-minute safety gate
                    # are checked before every possible external request.
                    live_schedule = read_schedule(runtime_root, now=clock(), require_finished=True)
                    if live_schedule["projection_sha256"] != plan["schedule"]["projection_sha256"]:
                        raise ValueError("schedule-changed-during-result-refresh")
                    rid, day = target["race_id"], target["race_date"]
                    state = fetch_pipeline.get_fetch_control_status()
                    if state.get("blocked_reason") or observed.restriction_observed:
                        halt = "provider-restriction-do-not-start-more-work"
                    if halt:
                        outcomes.append({"race_id": rid, "status": "deferred", "reason": halt}); continue
                    db = sqlite3.connect(Path(source_db).resolve().as_uri() + "?mode=ro", uri=True, timeout=5)
                    try:
                        db.execute("PRAGMA query_only=ON")
                        old = load_saved(db, rid)
                        excluded = db.execute("SELECT excluded_from_standard FROM scrape_race_acquisition_state WHERE race_id=?", (rid,)).fetchone() if db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='scrape_race_acquisition_state'").fetchone() else None
                    finally:
                        db.close()
                    if excluded and excluded[0]:
                        outcomes.append({"race_id": rid, "status": "quarantined_not_reset"}); continue
                    if old and classify_race_quality(old).valid_for_date_completion:
                        outcomes.append({"race_id": rid, "status": "normal_complete_preserved", "network_requests": 0}); continue
                    observed.active_race_id = rid
                    try:
                        candidate = await asyncio.wait_for(race.scrape_race_full(session, rid, date_hint=day,
                            quick_mode=True, force_refresh=True, enrich_horse_details=False, results_required=True), timeout=90)
                        if not candidate:
                            raise RepairConflict("confirmed-result-unavailable-not-an-entry-card")
                        validate_scoped_payload(candidate, rid, day, "jra")
                        receipt = validate_result_source(candidate, observed.receipts.get(rid, []), rid, day)
                        report = classify_race_quality(candidate)
                        if not report.valid_for_date_completion:
                            raise RepairConflict("confirmed-result-incomplete:" + ";".join(report.required_errors))
                        evidence = {"kind": "today-confirmed-history-refresh", "receipts": [receipt],
                            "plan_sha256": plan_sha, "not_prestart_capture": True,
                            "schedule_projection_sha256": plan["schedule"]["projection_sha256"]}
                        changes, merged = await asyncio.to_thread(save_result_repair, source_db, candidate,
                            batch_id=plan["batch_id"], evidence=evidence)
                        await asyncio.to_thread(record_race_quality, source_db, race_date=day, race_id=rid,
                            report=classify_race_quality(merged), source_status="verified_today_result_refresh",
                            race_data=merged, record_observation=False)
                        outcomes.append({"race_id": rid, "status": "saved", "changes": changes,
                            "source_body_sha256": receipt["body_sha256"], "network_requests": observed.calls[rid]})
                    except Exception as exc:
                        outcomes.append({"race_id": rid, "status": "pending", "reason": str(exc)[:500],
                            "network_requests": observed.calls[rid]})
                    with (directory / "events.jsonl").open("a", encoding="utf-8") as stream:
                        stream.write(json.dumps({**outcomes[-1], "plan_sha256": plan_sha,
                            "recorded_at_utc": clock().astimezone(UTC).isoformat()}, ensure_ascii=False) + "\n")
                    observed.active_race_id = None
            metrics = dict(context)
    finally:
        race.fetch_text = previous_fetch
    protected_after = preparation.protection_receipt(runtime_root=runtime_root)
    protected_ok = protected_after == plan["protected_before"]
    result = {"schema": SCHEMA, "operation": "completed_single_pass", "plan_sha256": plan_sha,
        "finished_at_utc": clock().astimezone(UTC).isoformat(), "batch_id": plan["batch_id"],
        "fixed_expected_schedule_races": len(plan["schedule"]["expected_race_ids"]),
        "flat_targets": len(plan["schedule"]["targets"]), "excluded_races": plan["schedule"]["excluded_races"],
        "outcome_counts": dict(Counter(item["status"] for item in outcomes)), "outcomes": outcomes,
        "request_counts_by_race": dict(observed.calls), "fetch_metrics": metrics,
        "source_revision_before": source_before, "source_revision_after": preparation.source_epoch_receipt(source_db),
        "protected_state_unchanged": protected_ok, "protected_after": protected_after,
        "future_h04_history_rebuild_required_after_this_refresh": True,
        "forward_ledger_writes": False, "training_or_activation": False,
        "pending_is_not_zero_or_synthetic_result": True}
    preparation.write_new_json(directory / "execution_receipt.json", result)
    manifest = {str(p.relative_to(directory)).replace("\\", "/"): preparation.file_hash(p)
                for p in sorted(directory.rglob("*")) if p.is_file()}
    preparation.write_new_json(directory / "artifact_manifest.json", {"schema": SCHEMA, "sha256": manifest})
    if not protected_ok:
        raise ValueError("protected-model-h04-or-existing-binding-changed")
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run-dir", required=True, type=Path)
    parser.add_argument("--execute", action="store_true")
    parser.add_argument("--allow-network", action="store_true")
    args = parser.parse_args()
    directory = args.run_dir.resolve()
    if directory == OUTPUT_ROOT.resolve() or not directory.is_relative_to(OUTPUT_ROOT.resolve()):
        parser.error("--run-dir must be a new child of reports/generated/recent-confirmed-history")
    if not directory.exists():
        result = make_plan(directory)
    else:
        result = preparation.read_json(directory / "plan.json")
    if args.execute:
        result = asyncio.run(execute(directory, allow_network=args.allow_network))
        print(json.dumps({key: result.get(key) for key in ("operation", "flat_targets", "outcome_counts", "protected_state_unchanged")}, ensure_ascii=False, indent=2))
    else:
        print(json.dumps({"operation": "planned_no_network", "plan": str(directory / "plan.json"),
            "expected_schedule_races": len(result["schedule"]["expected_race_ids"]),
            "flat_targets": len(result["schedule"]["targets"]), "excluded_races": result["schedule"]["excluded_races"]}, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
