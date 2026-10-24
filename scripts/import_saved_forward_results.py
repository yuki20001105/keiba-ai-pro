"""Offline-only, explicit import of already sealed public result HTML.

Dry-run parses a new isolated plan. No network fallback exists. The original
forward receipts/objects/ledger are read-only and retain their observation times;
this replay is not historical pre-start availability evidence.
"""
from __future__ import annotations

import argparse
import asyncio
from collections import Counter
from copy import deepcopy
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import re
import sqlite3
import sys

ROOT = Path(__file__).resolve().parents[1]
for package in (ROOT / "scripts", ROOT / "python-api", ROOT / "keiba"):
    if str(package) not in sys.path:
        sys.path.insert(0, str(package))
import prepare_h04_history_revision as preparation
import refresh_recent_confirmed_history as recent
from repair_source_verification import validate_result_source, source_date
from scraping.safe_repair import save_result_repair, RepairConflict
from scraping.quality import classify_race_quality, record_race_quality
from scraping.acquisition_scope import validate_scoped_payload

SCHEMA = "saved-forward-results-offline-import-v1"
OUTPUT_ROOT = ROOT / "reports/generated/offline-confirmed-history"
UTC = timezone.utc
IMPLEMENTATION_PATHS = (
    "scripts/import_saved_forward_results.py", "scripts/refresh_recent_confirmed_history.py",
    "scripts/prepare_h04_history_revision.py", "scripts/repair_source_verification.py",
    "python-api/scraping/race.py", "python-api/scraping/mobile_race.py",
    "python-api/scraping/constants.py", "python-api/scraping/source_normalization.py",
    "python-api/scraping/last3f_evidence.py", "python-api/scraping/safe_repair.py",
    "python-api/scraping/quality.py", "python-api/scraping/acquisition_scope.py",
    "python-api/scraping/fetch_pipeline.py", "python-api/forward_runtime/provider.py",
)


def implementation_hashes():
    actual = {name: preparation.file_hash(ROOT / name) for name in IMPLEMENTATION_PATHS}
    imported = globals().get("_IMPORTED_IMPLEMENTATION_HASHES")
    if imported is not None and actual != imported:
        raise ValueError("offline-import-implementation-changed-since-process-start")
    return actual


_IMPORTED_IMPLEMENTATION_HASHES = implementation_hashes()


class NoNetworkSession:
    def __getattr__(self, name):
        raise RepairConflict("offline-only-network-session-forbidden:" + name)


async def forbidden(*args, **kwargs):
    raise RepairConflict("offline-only-entry-detail-odds-network-forbidden")


def inventory_rows(value):
    rows = value.get("rows", {})
    if isinstance(rows, dict):
        return rows
    if isinstance(rows, list):
        return {row["race_id"]: row for row in rows}
    raise ValueError("source-inventory-race-rows-required")


def make_plan(inventory_path, run_dir, *, runtime_root=preparation.RUNTIME_ROOT, source_db=preparation.SOURCE_DB, now=None):
    current = now or datetime.now(UTC)
    inventory_path = Path(inventory_path).resolve(strict=True)
    inventory = preparation.read_json(inventory_path)
    indexed = inventory_rows(inventory)
    schedule = recent.read_schedule(runtime_root, now=current, require_finished=True)
    targets = []
    for target in schedule["targets"]:
        candidates = indexed.get(target["race_id"], {}).get("candidates", [])
        eligible = [row for row in candidates if row.get("receipt_verified") is True
                    and row.get("object_hash_verified") is True]
        # The pinned public-result parser is called again independently below;
        # inventory diagnostics do not substitute for source/body validation.
        chosen = sorted(eligible, key=lambda row: str(row.get("observed_at_utc", "")))[-1] if eligible else None
        targets.append({**target, "source": chosen})
    directory = Path(run_dir).resolve()
    if directory.exists():
        raise ValueError("offline-result-plan-output-must-be-new")
    directory.mkdir(parents=True, exist_ok=False)
    result = {"schema": SCHEMA, "batch_id": directory.name, "created_at_utc": current.astimezone(UTC).isoformat(),
        "source_inventory": str(inventory_path), "source_inventory_sha256": preparation.file_hash(inventory_path),
        "runtime_root": str(Path(runtime_root).resolve()), "source_database": str(Path(source_db).resolve()),
        "schedule_projection_sha256": schedule["projection_sha256"], "targets": targets,
        "protected_before": preparation.protection_receipt(runtime_root=runtime_root),
        "implementation_sha256": implementation_hashes(),
        "network_allowed": False, "default_mode": "dry_run", "payout_policy": "Only exact independently verified win payout; all other payouts pending, no zero/odds substitution."}
    preparation.write_new_json(directory / "plan.json", result)
    preparation.write_new_json(directory / "plan_receipt.json", {"plan_sha256": preparation.file_hash(directory / "plan.json")})
    return result


def verify_saved_source(item, *, runtime_root):
    from scraping.fetch_pipeline import decode_html_body
    from forward_runtime import provider
    rid, source = item["race_id"], item["source"]
    if not source:
        raise RepairConflict("saved-forward-result-source-not-available")
    root = Path(runtime_root).resolve(strict=True)
    receipt_path = Path(source["receipt_path"]).resolve(strict=True)
    object_path = Path(source["object_path"]).resolve(strict=True)
    if receipt_path.parent != root / "receipts" or object_path.parent != root / "objects":
        raise RepairConflict("only-original-forward-receipts-and-objects-allowed")
    receipt = preparation.read_json(receipt_path)
    declared = receipt.get("receipt_sha256")
    if preparation.object_hash({key: value for key, value in receipt.items() if key != "receipt_sha256"}) != declared:
        raise RepairConflict("original-forward-receipt-seal-mismatch")
    if source.get("receipt_sha256") != declared:
        raise RepairConflict("inventory-original-receipt-hash-mismatch")
    protocol = preparation.read_json(root / "protocol.json")
    if receipt.get("protocol_sha256") != protocol["protocol_sha256"]:
        raise RepairConflict("original-forward-receipt-protocol-mismatch")
    raw = object_path.read_bytes(); body_sha = hashlib.sha256(raw).hexdigest()
    expected_url = f"https://race.netkeiba.com/race/result.html?race_id={rid}"
    if (receipt.get("url") != expected_url or receipt.get("request_url") != expected_url
            or receipt.get("source") != "network" or receipt.get("status") != 200
            or receipt.get("body_sha256") != body_sha or object_path.name != body_sha
            or source.get("body_sha256") != body_sha):
        raise RepairConflict("original-forward-result-url-body-or-network-proof-mismatch")
    if source.get("observed_at_utc") != receipt.get("received_at"):
        raise RepairConflict("original-observation-time-mismatch")
    official = provider.parse_result(raw, rid, item["post_time"], receipt=receipt)
    if official.get("complete") is not True or official.get("status") != "final":
        raise RepairConflict("original-published-result-not-final")
    html = decode_html_body(raw)
    if source_date(html) != item["race_date"]:
        raise RepairConflict("original-result-displayed-date-not-verified")
    return raw, html, receipt, official


async def replay_candidate(item, *, run_dir, runtime_root):
    from scraping import race
    from scraping.fetch_pipeline import FetchResult
    raw, html, original, official = verify_saved_source(item, runtime_root=runtime_root)
    rid = item["race_id"]
    decoded = html.encode("utf-8")
    decoded_sha = hashlib.sha256(decoded).hexdigest()
    decoded_path = Path(run_dir) / "decoded" / f"{rid}-{decoded_sha}.html"
    decoded_path.parent.mkdir(exist_ok=True)
    if decoded_path.exists():
        if decoded_path.read_bytes() != decoded:
            raise RepairConflict("offline-decoded-artifact-conflict")
    else:
        with decoded_path.open("xb") as stream:
            stream.write(decoded)
    calls = 0
    async def only_raw(session, url, **kwargs):
        nonlocal calls
        if calls or url != f"https://db.netkeiba.com/race/{rid}/":
            raise RepairConflict("offline-replay-is-one-raw-only-no-other-fetch")
        calls += 1
        return FetchResult(original["url"], original["url"], 200, raw, "saved-forward-raw-replay", 0), html
    replacements = {"fetch_text": only_raw, "prefer_mobile_endpoint": lambda key: False,
        "_scrape_shutuba_fallback": forbidden, "scrape_horse_detail": forbidden, "fetch_tansho_odds_api": forbidden}
    previous = {name: getattr(race, name) for name in replacements}
    for name, value in replacements.items():
        setattr(race, name, value)
    try:
        candidate = await race.scrape_race_full(NoNetworkSession(), rid, date_hint="",
            quick_mode=True, force_refresh=False, enrich_horse_details=False, results_required=True)
    finally:
        for name, value in previous.items():
            setattr(race, name, value)
    if not candidate:
        raise RepairConflict("saved-source-parser-could-not-build-complete-result")
    validate_scoped_payload(candidate, rid, item["race_date"], "jra")
    replay_at = datetime.now(UTC).isoformat()
    candidate["_source_evidence"].update(observed_at_utc=original["received_at"], replayed_at_utc=replay_at,
        source_observation_preserved_from_original_receipt=True, replay_is_new_source_observation=False)
    source_receipt = {"url": original["url"], "status": 200, "body_sha256": original["body_sha256"],
        "html_path": item["source"]["object_path"], "parsed_source_date": source_date(html),
        "decoded_html_sha256": decoded_sha, "decoded_html_path": str(decoded_path),
        "fetch_source": "saved-forward-raw-replay", "observed_at_utc": original["received_at"],
        "original_receipt_path": item["source"]["receipt_path"], "original_receipt_sha256": original["receipt_sha256"],
        "replayed_at_utc": replay_at, "network_requests": 0}
    strict = validate_result_source(candidate, [source_receipt], rid, item["race_date"])
    quality = classify_race_quality(candidate)
    if not quality.valid_for_date_completion:
        raise RepairConflict("offline-result-parser-quality:" + ";".join(quality.required_errors))
    parsed = candidate.get("return_tables", [])
    kept = []
    for payout in parsed:
        if payout.get("bet_type") != "単勝":
            continue
        key = str(payout.get("combinations"))
        if (key in official["win_payouts"] and payout.get("payout") == official["win_payouts"][key]
                and payout.get("race_id") == rid):
            kept.append(payout)
    # Unknown or differently shaped combinations are never approximated.
    candidate["return_tables"] = kept
    payout_status = "verified_win_only" if len(kept) == len(official["win_payouts"]) else "pending_unverified_parser_shape"
    return candidate, {"source_receipt": strict, "original_official": official,
        "payout_status": payout_status, "unverified_payouts_not_imported": len(parsed) - len(kept),
        "source_observed_at_utc": original["received_at"], "replayed_at_utc": replay_at,
        "network_requests": 0}


async def process(run_dir, *, apply=False, limit=None, now=None, runtime_root=preparation.RUNTIME_ROOT, source_db=preparation.SOURCE_DB):
    current = now or datetime.now(UTC)
    directory = Path(run_dir).resolve(strict=True)
    plan = preparation.read_json(directory / "plan.json")
    if plan.get("implementation_sha256") != implementation_hashes():
        raise ValueError("offline-import-code-freeze-changed-or-missing-new-plan-required")
    seal = preparation.read_json(directory / "plan_receipt.json")
    if (plan.get("schema") != SCHEMA or seal["plan_sha256"] != preparation.file_hash(directory / "plan.json")
            or preparation.file_hash(plan["source_inventory"]) != plan["source_inventory_sha256"]
            or Path(plan["runtime_root"]).resolve() != Path(runtime_root).resolve()
            or Path(plan["source_database"]).resolve() != Path(source_db).resolve()):
        raise ValueError("offline-import-plan-binding-invalid")
    schedule = recent.read_schedule(runtime_root, now=current, require_finished=True)
    if schedule["projection_sha256"] != plan["schedule_projection_sha256"]:
        raise ValueError("registered-schedule-changed-since-offline-plan")
    before = preparation.protection_receipt(runtime_root=runtime_root)
    if before != plan["protected_before"]:
        raise ValueError("protected-model-h04-binding-changed-since-offline-plan")
    if apply:
        if limit is not None:
            raise ValueError("partial-limit-is-dry-run-only")
        preparation.write_new_json(directory / "apply.claim.json", {"plan_sha256": seal["plan_sha256"],
            "one_apply_only": True, "started_at_utc": current.astimezone(UTC).isoformat()})
    results = []
    for item in plan["targets"][:limit]:
        try:
            candidate, proof = await replay_candidate(item, run_dir=directory, runtime_root=runtime_root)
            outcome = {"race_id": item["race_id"], "status": "validated_dry_run", "proof": proof}
            if apply:
                db = sqlite3.connect(Path(source_db).resolve().as_uri() + "?mode=ro", uri=True, timeout=5)
                try:
                    excluded = db.execute("SELECT excluded_from_standard FROM scrape_race_acquisition_state WHERE race_id=?", (item["race_id"],)).fetchone()
                finally:
                    db.close()
                if excluded and excluded[0]:
                    raise RepairConflict("existing-quarantine-must-not-be-reset-by-offline-import")
                changed, merged = await asyncio.to_thread(save_result_repair, source_db, candidate,
                    batch_id=plan["batch_id"], evidence={"kind": "saved-forward-result-offline-import",
                        "not_prestart_capture": True, "plan_sha256": seal["plan_sha256"], **proof})
                await asyncio.to_thread(record_race_quality, source_db, race_date=item["race_date"], race_id=item["race_id"],
                    report=classify_race_quality(merged), source_status="verified_saved_forward_result_replay",
                    race_data=merged, record_observation=False)
                outcome.update(status="saved", changes=changed)
        except Exception as exc:
            outcome = {"race_id": item["race_id"], "status": "pending", "reason": str(exc)[:600], "network_requests": 0}
        results.append(outcome)
    after = preparation.protection_receipt(runtime_root=runtime_root)
    if plan["implementation_sha256"] != implementation_hashes():
        raise ValueError("offline-import-code-freeze-changed-during-processing")
    report = {"schema": SCHEMA, "mode": "apply" if apply else "dry_run", "network_requests": 0,
        "outcome_counts": dict(Counter(row["status"] for row in results)), "outcomes": results,
        "protected_state_unchanged": before == after, "protected_after": after,
        "implementation_sha256": plan["implementation_sha256"],
        "original_forward_artifacts_modified": False, "new_prestart_evidence_claimed": False,
        "finished_at_utc": datetime.now(UTC).isoformat()}
    name = ("apply" if apply else "dry-run") + "-" + datetime.now(UTC).strftime("%Y%m%dT%H%M%S%fZ") + ".json"
    preparation.write_new_json(directory / name, report)
    if before != after:
        raise ValueError("protected-state-changed-during-offline-import")
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source-inventory", type=Path, required=True)
    parser.add_argument("--run-dir", type=Path, required=True)
    parser.add_argument("--limit", type=int)
    parser.add_argument("--apply", action="store_true")
    args = parser.parse_args()
    directory = args.run_dir.resolve()
    if directory == OUTPUT_ROOT.resolve() or not directory.is_relative_to(OUTPUT_ROOT.resolve()):
        parser.error("new --run-dir must be below reports/generated/offline-confirmed-history")
    if args.limit is not None and args.limit < 1:
        parser.error("--limit must be positive")
    if not directory.exists():
        make_plan(args.source_inventory, directory)
    result = asyncio.run(process(directory, apply=args.apply, limit=args.limit))
    print(json.dumps({key: result[key] for key in ("mode", "network_requests", "outcome_counts", "protected_state_unchanged")}, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
