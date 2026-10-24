"""Bounded browser admission only; performs no browser or HTTP operations.

The caller must navigate once promptly after admission, then release its own
lease. Reservations count toward the hard ceiling even if navigation fails.
"""
from __future__ import annotations

import argparse
import asyncio
from contextlib import contextmanager
import json
import msvcrt
from pathlib import Path
import re
import sys
import time
from urllib.parse import parse_qsl, urlsplit

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / "scripts"), str(ROOT / "python-api")]
import psutil
from market_edge.compute import CollectorMonitor
from scraping.request_pacing import RequestPacer
from training_reconstruction_common import TRACK, now, read, sha, write

OUT = ROOT / "reports/generated/model-experiments/netkeiba-authenticated-phase2-v1"
NAV = OUT / "navigation"
PLAN = OUT / "phase2_pilot_plan.json"
PACER = ROOT / "keiba/data/request_pacing.db"
FAMILY = "netkeiba.com"


@contextmanager
def ledger_lock():
    NAV.mkdir(parents=True, exist_ok=True)
    with (NAV / "admission.lock").open("a+b") as stream:
        stream.seek(0, 2)
        if stream.tell() == 0:
            stream.write(b"0")
            stream.flush()
        stream.seek(0)
        try:
            msvcrt.locking(stream.fileno(), msvcrt.LK_NBLCK, 1)
        except OSError as exc:
            raise ValueError("another_phase2_gate_operation_is_active") from exc
        try:
            yield
        finally:
            stream.seek(0)
            msvcrt.locking(stream.fileno(), msvcrt.LK_UNLCK, 1)


def validate_url(url):
    parsed = urlsplit(url)
    if (parsed.scheme != "https" or parsed.hostname != "race.netkeiba.com"
            or parsed.username or parsed.password or parsed.port or parsed.fragment
            or not re.fullmatch(r"/race/[a-z_]+\.html", parsed.path)):
        raise ValueError("only_observed_https_race_netkeiba_race_page_urls_allowed")
    query = parse_qsl(parsed.query, keep_blank_values=True)
    if len(dict(query)) != len(query) or any(key not in {"race_id", "type", "master"} for key, _ in query):
        raise ValueError("unapproved_or_duplicate_url_query")
    fields = dict(query)
    if not re.fullmatch(r"\d{12}", fields.get("race_id", "")):
        raise ValueError("race_id_required")
    if "type" in fields and not re.fullmatch(r"[a-zA-Z0-9_-]{1,30}", fields["type"]):
        raise ValueError("invalid_page_type")
    if "master" in fields and (parsed.path != "/race/speed.html" or fields["master"] not in {"0", "1"}):
        raise ValueError("master_must_be_0_or_1_on_observed_speed_page")
    return url


def ledger_state():
    entries = [(p, read(p)) for p in sorted(NAV.glob("*-admission.json"))]
    manual_path = OUT / "manual-navigation-reservation.json"
    manual = read(manual_path) if manual_path.exists() else {"count": 0, "lease_released": True}
    manual_count = manual["count"]
    if isinstance(manual_count, bool) or not isinstance(manual_count, int) or manual_count < 0:
        raise ValueError("invalid_manual_navigation_count")
    if not manual.get("lease_released"):
        raise ValueError("manual_navigation_lease_not_released")
    passed = [row for _, row in entries if row["admitted"] is True]
    pending = [row["seq"] for row in passed if not (NAV / f"{row['seq']:03d}-released.json").exists()]
    return entries, manual_count + len(passed), pending


def admission(monitor):
    state = monitor.snapshot()
    instant = time.time()
    # Preserve the existing [capture start - 120, capture end + 60] guard.
    # Also preempt admission if that protected interval begins within 90s.
    conflicts = [w["race_id"] for w in state.get("capture_windows", [])
                 if w["start"] - 120 - 90 <= instant <= w["end"] + 60]
    available = psutil.virtual_memory().available
    reasons = []
    if not state.get("healthy"):
        reasons.append("collector_unhealthy")
    if state.get("capturing"):
        reasons.append("collector_capturing")
    if conflicts:
        reasons.append("protected_window_start_minus_120_or_boundary_within_90_seconds")
    if available < 4 * 1024 ** 3:
        reasons.append("available_ram_below_4GiB")
    return {"passed": not reasons, "reasons": reasons, "healthy": bool(state.get("healthy")),
            "observed_at": state["observed_at"], "pid": state.get("pid"),
            "protocol_sha256": state.get("protocol_sha256"),
            "capturing_count": len(state.get("capturing", [])),
            "conflicting_race_ids": conflicts, "available_ram_gib": round(available / 1024 ** 3, 3),
            "capture_margin_before_seconds": 120, "capture_margin_after_seconds": 60,
            "protected_boundary_preemption_seconds": 90}


async def acquire(args):
    validate_url(args.url)
    if not re.fullmatch(r"[A-Za-z0-9_.:-]{1,100}", args.purpose):
        raise ValueError("purpose_must_be_short_nonsensitive_label")
    plan = read(PLAN)
    limit = plan["page_budget"]["hard_max_browser_navigations"]
    if limit != 48 or plan["acquisition_controls"]["minimum_navigation_gap_seconds"] < 3:
        raise ValueError("unexpected_predeclared_budget_or_pacing")
    with ledger_lock():
        entries, count, pending = ledger_state()
        seq = max([row["seq"] for _, row in entries], default=0) + 1
        receipt = {"schema": "phase2-browser-admission-v1", "seq": seq,
                   "recorded_at": now(), "url": args.url, "purpose": args.purpose,
                   "plan_sha256": sha(PLAN), "admitted": False, "owner": None,
                   "reserved_navigations_before": count, "hard_limit": limit,
                   "http_request_count": None, "browser_operation_performed_by_gate": False}
        pacer = RequestPacer(PACER)
        owner = None
        try:
            if count >= limit:
                raise ValueError("hard_navigation_budget_exhausted")
            if pending:
                raise ValueError("previous_phase2_admission_not_released")
            monitor = CollectorMonitor(TRACK)
            receipt["before"] = admission(monitor)
            if not receipt["before"]["passed"]:
                raise ValueError("pre_admission_protection_failed")
            owner, waited = await asyncio.wait_for(pacer.acquire(FAMILY, 3), timeout=5)
            receipt["after"] = admission(monitor)
            if not receipt["after"]["passed"]:
                raise ValueError("post_admission_protection_failed")
            receipt.update(admitted=True, owner=owner, waited_seconds=waited,
                           admitted_at=now(), navigation_budget_ordinal=count + 1,
                           navigate_within_seconds=30,
                           required_next_action="Exactly one browser navigation, then release this owner promptly even on failure")
        except Exception as exc:
            if owner:
                pacer.release(FAMILY, owner)
                owner = None
            receipt["reason"] = str(exc) or type(exc).__name__
        finally:
            pacer.close()
        try:
            write(NAV / f"{seq:03d}-admission.json", receipt)
        except BaseException:
            # A durable receipt is required before handing a lease to a caller.
            if owner:
                try:
                    pacer.release(FAMILY, owner)
                finally:
                    pacer.close()
            raise
        print(json.dumps({"admitted": receipt["admitted"], "seq": seq, "owner": owner,
                          "reason": receipt.get("reason"), "admission": receipt.get("after", receipt.get("before")),
                          "reserved_navigations": count + int(receipt["admitted"]),
                          "navigate_within_seconds": 30 if receipt["admitted"] else None}, ensure_ascii=False))
        return 0 if receipt["admitted"] else 2


def release(args):
    if not re.fullmatch(r"[a-f0-9]{32}", args.owner):
        raise ValueError("invalid_own_lease_format")
    with ledger_lock():
        entries, _, _ = ledger_state()
        matches = [row for _, row in entries if row["admitted"] and row["owner"] == args.owner]
        if len(matches) != 1:
            raise ValueError("owner_not_a_unique_phase2_admission")
        row = matches[0]
        path = NAV / f"{row['seq']:03d}-released.json"
        if path.exists():
            raise ValueError("owner_already_released")
        pacer = RequestPacer(PACER)
        try:
            status = pacer.status(FAMILY)
            current = status.get("owner")
            if current not in (None, args.owner):
                raise ValueError("pacer_owned_by_someone_else_no_release_performed")
            if current == args.owner:
                pacer.release(FAMILY, args.owner)
            result = {"schema": "phase2-browser-release-v1", "seq": row["seq"],
                      "released_at": now(), "owner": args.owner,
                      "outcome": "navigation_issued_or_attempted", "http_success_claimed": False,
                      "http_request_count": None, "own_lease_release_performed": current == args.owner,
                      "other_owners_released": False, "block_or_cooldown_reset": False}
            write(path, result)
        finally:
            pacer.close()
        print(json.dumps(result, ensure_ascii=False))
        return 0


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    acq = commands.add_parser("acquire")
    acq.add_argument("--url", required=True)
    acq.add_argument("--purpose", required=True)
    rel = commands.add_parser("release")
    rel.add_argument("--owner", required=True)
    args = parser.parse_args()
    try:
        return asyncio.run(acquire(args)) if args.command == "acquire" else release(args)
    except Exception as exc:
        print(json.dumps({"admitted": False, "reason": str(exc) or type(exc).__name__}, ensure_ascii=False))
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
