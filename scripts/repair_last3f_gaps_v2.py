"""Plan-bound, resumable public final-3F gap repair; dry run by default.

Preserves good values, shares provider admission/backpressure, and never trains,
switches models, rewrites H04 evidence, buys tickets, or copies/hashes the DB.
"""
from __future__ import annotations

import argparse
import asyncio
from collections import Counter
from contextlib import closing, contextmanager
from datetime import datetime, timezone
import hashlib
import json
import math
import os
from pathlib import Path
import re
import sqlite3
import sys
import time
import uuid

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / "python-api"), str(ROOT / "scripts")]

from evaluate_db_shared_venues import preservation_receipt
from scraping.acquisition_scope import is_jra_race_id, normalized_date
from scraping.last3f_evidence import parse_last3f_evidence
from scraping.last3f_gap_repair import apply_evidence, clock_state, identity
from scraping.quality import classify_race_quality, _finish_value, NON_FINISH_TOKENS
from scraping.safe_repair import _surface, load_saved
from services.prediction_source_revision import _validated_state

SCHEMA = "last3f-gap-repair-plan-v2"
FROM_DATE, TO_DATE = "20160101", "20261016"
DB_PATH = ROOT / "keiba/data/keiba_ultimate.db"
CACHE_PATH = ROOT / "keiba/data/fetch_cache.db"
TRACK_PATH = ROOT / "reports/generated/forward-runtime/h04-live-20260920-v2"
GAP_STATES = {"missing", "masked", "masked_shadowing_valid_alias", "missing_shadowing_valid_alias"}
TERMINAL = {"completed", "domain_missing", "quarantined"}
VENUES = {"01": "札幌", "02": "函館", "03": "福島", "04": "新潟", "05": "東京",
          "06": "中山", "07": "中京", "08": "京都", "09": "阪神", "10": "小倉"}
CODE_PATHS = ("scripts/repair_last3f_gaps_v2.py", "python-api/scraping/last3f_evidence.py",
              "python-api/scraping/last3f_gap_repair.py", "python-api/scraping/quality.py",
              "python-api/scraping/safe_repair.py", "python-api/scraping/acquisition_scope.py",
              "python-api/services/prediction_source_revision.py")
RUNNER_FIELDS = ("race_id", "race_date", "date", "horse_number", "horse_id", "horse_name",
                 "finish_position", "finish", "finish_time", "time_seconds", "time", "_shutuba",
                 "last_3f_time", "last_3f")


def now():
    return datetime.now(timezone.utc).isoformat()


def encoded(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False).encode("utf-8")


def digest_file(path):
    value = hashlib.sha256()
    with Path(path).open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            value.update(block)
    return value.hexdigest()


def read_json(path):
    return json.loads(Path(path).read_text(encoding="utf-8"))


def exclusive_json(path, value):
    with Path(path).open("xb") as stream:
        stream.write(encoded(value) + b"\n")


def independent_failure_receipt(directory, value):
    """No replacement of a locked status file; retain the original exception."""
    path = Path(directory) / ("failure-" + datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
                              + "-" + uuid.uuid4().hex[:8] + ".json")
    try:
        exclusive_json(path, value)
    except OSError:
        return None
    return str(path)


def _replace_is_transient_sharing(exc, temporary, target):
    # Windows may report a delete-share denial as ERROR_ACCESS_DENIED (5),
    # instead of ERROR_SHARING_VIOLATION (32) or ERROR_LOCK_VIOLATION (33).
    # Restrict this compatibility retry to this exact same-directory replace,
    # existing files and a non-readonly target; never retry arbitrary I/O.
    if not isinstance(exc, PermissionError) or getattr(exc, "winerror", None) not in {5, 32, 33}:
        return False
    try:
        return (temporary.parent == target.parent and temporary.is_file() and target.is_file()
                and not (getattr(target.stat(), "st_file_attributes", 0) & 1))
    except OSError:
        return False


def atomic_status(path, value):
    target = Path(path)
    temporary = target.with_name(target.name + ".pending-" + str(os.getpid()))
    temporary.write_bytes(encoded(value) + b"\n")
    delays = (.025, .05, .1, .2, .3, .4)  # At most 1.075 s of sleeps, seven replace attempts.
    for attempt in range(len(delays) + 1):
        try:
            os.replace(temporary, target)
            return
        except OSError as exc:
            retryable = _replace_is_transient_sharing(exc, temporary, target)
            if retryable and attempt < len(delays):
                time.sleep(delays[attempt])
                continue
            independent_failure_receipt(target.parent, {
                "schema": "last3f-v2-status-write-failure-v1", "recorded_at_utc": now(),
                "operation": "os.replace-status-only", "status_path": str(target),
                "pending_path": str(temporary), "replace_attempts": attempt + 1,
                "winerror": getattr(exc, "winerror", None), "retryable_sharing_class": retryable,
                "error": type(exc).__name__ + ": " + str(exc), "intended_status": value,
                "original_status_file_not_deleted": True,
            })
            raise


def read_only(path):
    db = sqlite3.connect(Path(path).resolve(strict=True).as_uri() + "?mode=ro", uri=True, timeout=3)
    db.execute("PRAGMA query_only=ON")
    db.execute("PRAGMA cache_size=-4096")
    return db


def lower_priority():
    try:
        import psutil
        process = psutil.Process()
        process.nice(psutil.BELOW_NORMAL_PRIORITY_CLASS if os.name == "nt" else 10)
        return "below_normal"
    except (ImportError, OSError, AttributeError):
        return "cooperative_yield_only"


def protected_projection(receipt):
    return {key: value for key, value in receipt.items() if key != "forward_processes"}


def source_epoch(db):
    state = _validated_state(db)
    return list(state) if state is not None else None


def metadata_projection(info):
    return {key: info.get(key) for key in ("race_id", "date", "race_date", "venue", "distance", "track_type", "surface", "num_horses")}


def valid_metadata(rid, info):
    if not isinstance(info, dict):
        return False
    day = normalized_date(info.get("date"))
    if (not day or not FROM_DATE <= day <= TO_DATE or not is_jra_race_id(rid, day)
            or info.get("race_id") != rid or info.get("venue") != VENUES[rid[4:6]]
            or (info.get("race_date") is not None and normalized_date(info["race_date"]) != day)):
        return False
    try:
        surface = _surface(info)
    except ValueError:
        return False
    if surface not in {"芝", "ダート"} or any("障" in str(info.get(key, "")) for key in ("race_name", "race_class", "class")):
        return False
    return True


def _gap_summary(runners):
    states = Counter(clock_state(row)[0] for row in runners)
    numbers = [int(row["horse_number"]) for row in runners if clock_state(row)[0] in GAP_STATES]
    return states, sorted(numbers)


def create_plan(run_dir, *, db_path=DB_PATH, cache_path=CACHE_PATH):
    run_dir, db_path, cache_path = Path(run_dir).resolve(), Path(db_path).resolve(), Path(cache_path).resolve()
    run_dir.mkdir(parents=True, exist_ok=True)
    if any((run_dir / name).exists() for name in ("plan.json", "plan_receipt.json", "targets.jsonl")):
        raise FileExistsError("last3f-plan-already-exists-use-existing-plan")
    priority = lower_priority()
    before = preservation_receipt()
    metadata, duplicate_ids, seen_metadata = {}, set(), set()
    counts, excluded = Counter(), Counter()
    target_path = run_dir / "targets.jsonl"
    with closing(read_only(db_path)) as db, target_path.open("xb") as output:
        db.execute("BEGIN")
        epoch = source_epoch(db)
        for rid, raw in db.execute("SELECT race_id,data FROM races_ultimate WHERE race_id>=? AND race_id<? ORDER BY race_id", ("2016", "2027")):
            counts["metadata_rows_scanned"] += 1
            if counts["metadata_rows_scanned"] % 1024 == 0:
                time.sleep(.003)
            if rid in seen_metadata:
                duplicate_ids.add(rid)
                continue
            seen_metadata.add(rid)
            try:
                info = json.loads(raw)
            except (ValueError, TypeError):
                excluded["invalid_header_json"] += 1
                continue
            if valid_metadata(rid, info):
                metadata[rid] = info
        for rid in duplicate_ids:
            metadata.pop(rid, None)
        excluded["duplicate_metadata_ids"] = len(duplicate_ids)
        current, horses = None, []

        def flush():
            if current is None or current not in metadata:
                return
            info = metadata[current]
            quality = classify_race_quality({"race_info": info, "horses": horses})
            if not quality.valid_for_date_completion:
                excluded["not_settled_or_complete"] += 1
                return
            try:
                keys = [identity(horse) for horse in horses]
                if len(set(keys)) != len(keys):
                    raise ValueError("duplicate")
                states, gaps = _gap_summary(horses)
            except (ValueError, TypeError, KeyError):
                excluded["invalid_runner_identity"] += 1
                return
            counts["eligible_flat_races"] += 1
            counts["eligible_flat_runners"] += len(horses)
            for state, count in states.items():
                counts["clock_" + state] += count
            if not gaps:
                return
            if states["alias_conflict"]:
                excluded["gap_races_with_conflicting_aliases"] += 1
                return
            record = {"race_id": current, "race_date": normalized_date(info["date"]),
                      "metadata": metadata_projection(info), "runner_count": len(horses),
                      "expected_runners": [{"horse_number": number, "horse_id": hid, "horse_name": name}
                                           for number, hid, name in sorted(keys)],
                      "initial_clock_states": dict(states), "initial_gap_horse_numbers": gaps}
            output.write(encoded(record) + b"\n")
            counts["target_races"] += 1
            counts["target_gap_runners"] += len(gaps)

        projection = ",".join("'" + field + "',json_extract(data,'$." + field + "')" for field in RUNNER_FIELDS)
        sql = ("SELECT race_id,json_object(" + projection + ") FROM race_results_ultimate "
               "WHERE race_id>=? AND race_id<? AND substr(race_id,5,2) BETWEEN '01' AND '10' ORDER BY race_id,id")
        for rid, raw in db.execute(sql, ("2016", "2027")):
            counts["runner_rows_scanned"] += 1
            if counts["runner_rows_scanned"] % 1024 == 0:
                time.sleep(.003)
            if rid != current:
                flush()
                current, horses = rid, []
            if rid in metadata:
                horses.append(json.loads(raw))
        flush()
        db.rollback()
    after = preservation_receipt()
    if protected_projection(before) != protected_projection(after):
        raise ValueError("last3f-protected-state-changed-during-plan")
    data = {"schema": SCHEMA, "batch_id": run_dir.name, "created_at_utc": now(),
            "database": str(db_path), "cache_database": str(cache_path),
            "scope": {"race_scope": "jra", "surface": ["芝", "ダート"], "from": FROM_DATE, "to": TO_DATE,
                      "settled_complete_only": True, "current_same_day_and_future_results_excluded": True},
            "targets_file": "targets.jsonl", "targets_sha256": digest_file(target_path),
            "counts": dict(counts), "excluded": dict(excluded), "source_epoch_at_plan": epoch,
            "protected_before": before, "priority": priority, "code_sha256": {name: digest_file(ROOT / name) for name in CODE_PATHS},
            "execution": {"dry_run_default": True, "cache_pass_first": True, "http_workers": 1,
                          "network_priority": "race_date_desc_race_id_desc",
                          "minimum_interval_seconds": 1.0, "maximum_attempts_per_race_per_run": 1,
                          "http_source": "https://race.netkeiba.com/race/result.html?race_id=RACE_ID",
                          "provider_block_stops_job": True, "no_authentication_or_block_bypass": True,
                          "consecutive_unresolved_masked_source_pause_threshold": 3,
                          "stop_request_file": "stop-request.json"},
            "normal_values_overwritten": False, "historical_capture_time_proven": False,
            "zero_fill": False, "training_or_model_activation": False}
    exclusive_json(run_dir / "plan.json", data)
    exclusive_json(run_dir / "plan_receipt.json", {"plan_sha256": digest_file(run_dir / "plan.json"), "batch_id": run_dir.name})
    atomic_status(run_dir / "status.json", {"schema": "last3f-gap-status-v2", "status": "planned_dry_run",
                  "updated_at_utc": now(), "plan_sha256": digest_file(run_dir / "plan.json"),
                  "counts": dict(counts), "database_writes": 0, "network_requests": 0})
    return data


def load_plan(run_dir, *, db_path=None):
    run_dir = Path(run_dir).resolve()
    data = read_json(run_dir / "plan.json")
    sha = digest_file(run_dir / "plan.json")
    receipt = read_json(run_dir / "plan_receipt.json")
    if (data.get("schema") != SCHEMA or data.get("batch_id") != run_dir.name or receipt.get("plan_sha256") != sha
            or receipt.get("batch_id") != run_dir.name or data.get("targets_file") != "targets.jsonl"
            or data.get("targets_sha256") != digest_file(run_dir / "targets.jsonl")
            or (db_path is not None and Path(data["database"]).resolve() != Path(db_path).resolve())
            or data.get("scope") != {"race_scope": "jra", "surface": ["芝", "ダート"], "from": FROM_DATE, "to": TO_DATE,
                                     "settled_complete_only": True, "current_same_day_and_future_results_excluded": True}):
        raise ValueError("last3f-plan-binding-invalid")
    for name, expected in data.get("code_sha256", {}).items():
        if name not in CODE_PATHS or digest_file(ROOT / name) != expected:
            raise ValueError("last3f-repair-source-code-changed")
    if set(data.get("code_sha256", {})) != set(CODE_PATHS):
        raise ValueError("last3f-repair-source-set-changed")
    targets, seen = [], set()
    with (run_dir / "targets.jsonl").open(encoding="utf-8") as stream:
        for line in stream:
            row = json.loads(line)
            rid, day = row["race_id"], row["race_date"]
            if rid in seen or not is_jra_race_id(rid, day) or normalized_date(day) != day or not FROM_DATE <= day <= TO_DATE:
                raise ValueError("last3f-plan-target-out-of-scope")
            keys = [identity(item) for item in row["expected_runners"]]
            if len(set(keys)) != len(keys) or len(keys) != row["runner_count"] or not valid_metadata(rid, row["metadata"]):
                raise ValueError("last3f-plan-target-identity-invalid")
            seen.add(rid)
            targets.append(row)
    if len(targets) != data["counts"].get("target_races", 0):
        raise ValueError("last3f-plan-target-count-invalid")
    return data, sha, targets


@contextmanager
def execution_lock(run_dir):
    path = Path(run_dir) / "execution.lock"
    with path.open("a+b") as stream:
        stream.seek(0, 2)
        if stream.tell() == 0:
            stream.write(b"0")
            stream.flush()
        stream.seek(0)
        try:
            if os.name == "nt":
                import msvcrt
                msvcrt.locking(stream.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl
                fcntl.flock(stream.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError as exc:
            raise ValueError("last3f-repair-job-already-running") from exc
        try:
            yield
        finally:
            stream.seek(0)
            if os.name == "nt":
                msvcrt.locking(stream.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                fcntl.flock(stream.fileno(), fcntl.LOCK_UN)


@contextmanager
def failure_status(run_dir):
    """Only the lock owner publishes its failure; never clobber another job."""
    try:
        yield
    except BaseException as exc:
        path = Path(run_dir) / "status.json"
        try:
            previous = read_json(path) if path.exists() else {"schema": "last3f-gap-status-v2"}
        except (OSError, ValueError):
            previous = {"schema": "last3f-gap-status-v2", "previous_status_unreadable": True}
        failed = {**previous, "status": "interrupted" if isinstance(exc, KeyboardInterrupt) else "failed",
                  "updated_at_utc": now(), "failure": type(exc).__name__ + ": " + str(exc)[:500],
                  "resume_requires_revalidation": True}
        independent_failure_receipt(run_dir, {**failed, "schema": "last3f-v2-execution-failure-v1"})
        try:
            atomic_status(path, failed)
        except OSError:
            pass  # Independent receipts retain both errors; do not mask the originating failure.
        raise


class Journal:
    def __init__(self, run_dir, plan_sha):
        self.root, self.plan_sha = Path(run_dir), plan_sha
        self.path = self.root / "events.jsonl"
        self.last_hash, self.latest, self.cache_checked = None, {}, set()
        self.status_counts = Counter()
        self.updated_total = 0
        self.count = 0
        if self.path.exists():
            with self.path.open(encoding="utf-8") as stream:
                for line in stream:
                    item = json.loads(line)
                    digest = item.pop("event_sha256", None)
                    if (item.get("plan_sha256") != plan_sha or item.get("previous_event_sha256") != self.last_hash
                            or hashlib.sha256(encoded(item)).hexdigest() != digest):
                        raise ValueError("last3f-event-chain-or-plan-invalid")
                    proofs = ([item] if item.get("proof_file") else []) + item.get("source_proofs", [])
                    for reference in proofs:
                        proof = (self.root / reference["proof_file"]).resolve()
                        if not proof.is_relative_to(self.root.resolve()) or digest_file(proof) != reference["proof_sha256"]:
                            raise ValueError("last3f-event-proof-changed")
                    self._remember(item, digest)

    def _remember(self, item, digest):
        self.last_hash, self.count = digest, self.count + 1
        self.updated_total += int(item.get("updated_runners", 0))
        if item.get("race_id"):
            old = self.latest.get(item["race_id"])
            if old:
                self.status_counts[old["status"]] -= 1
            self.status_counts[item["status"]] += 1
            self.latest[item["race_id"]] = item
            if item.get("phase") == "cache":
                self.cache_checked.add(item["race_id"])

    def emit(self, **item):
        item.update(plan_sha256=self.plan_sha, previous_event_sha256=self.last_hash, recorded_at_utc=now())
        digest = hashlib.sha256(encoded(item)).hexdigest()
        with self.path.open("ab") as stream:
            stream.write(encoded({**item, "event_sha256": digest}) + b"\n")
            stream.flush()
            os.fsync(stream.fileno())
        self._remember(item, digest)
        print(json.dumps({key: item.get(key) for key in ("phase", "race_id", "status", "reason", "updated_runners")}, ensure_ascii=False), flush=True)
        return item


def _seal_bytes(run_dir, subdir, body, suffix):
    digest = hashlib.sha256(body).hexdigest()
    path = Path(run_dir) / subdir / (digest + suffix)
    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        with path.open("xb") as stream:
            stream.write(body)
    except FileExistsError:
        if digest_file(path) != digest:
            raise ValueError("last3f-content-addressed-source-changed")
    return path


def seal_source(run_dir, evidence, body, html, transport):
    raw = _seal_bytes(run_dir, "sources/raw", body, ".bin")
    decoded = _seal_bytes(run_dir, "sources/decoded", html.encode("utf-8"), ".html")
    value = {"schema": "last3f-repair-source-proof-v1", "evidence": evidence, "transport": transport,
             "raw_body_path": raw.relative_to(run_dir).as_posix(), "decoded_html_path": decoded.relative_to(run_dir).as_posix()}
    path = _seal_bytes(run_dir, "evidence", encoded(value), ".json")
    return {"proof_file": path.relative_to(run_dir).as_posix(), "proof_sha256": digest_file(path),
            "source_body_sha256": evidence["source"]["raw_body_sha256"]}


class SourceRejected(ValueError):
    def __init__(self, reason, details):
        super().__init__(reason)
        self.details = details


def _seal_unverified(run_dir, target, body, html, *, url, observed, transport, reason):
    root = Path(run_dir)
    raw = _seal_bytes(root, "sources/raw", body, ".bin")
    decoded = _seal_bytes(root, "sources/decoded", html.encode("utf-8"), ".html")
    value = {"schema": "last3f-rejected-source-proof-v1", "race_id": target["race_id"], "source_url": url,
             "observed_at_utc": observed, "raw_body_sha256": hashlib.sha256(body).hexdigest(),
             "raw_body_path": raw.relative_to(root).as_posix(), "decoded_html_path": decoded.relative_to(root).as_posix(),
             "decoded_html_sha256": hashlib.sha256(html.encode("utf-8")).hexdigest(),
             "transport": transport, "reason": reason, "source_verified": False, "database_write_authority": False}
    path = _seal_bytes(root, "evidence", encoded(value), ".json")
    return {"proof_file": path.relative_to(root).as_posix(), "proof_sha256": digest_file(path),
            "source_body_sha256": value["raw_body_sha256"], "source_verified": False}


def _current(db_path, target):
    with closing(read_only(db_path)) as db:
        payload = load_saved(db, target["race_id"])
    if not payload or metadata_projection(payload["race_info"]) != target["metadata"]:
        raise ValueError("last3f-current-header-differs-from-fixed-plan")
    if (not classify_race_quality(payload).valid_for_date_completion or not valid_metadata(target["race_id"], payload["race_info"])
            or sorted(identity(row) for row in payload["horses"]) != sorted(identity(row) for row in target["expected_runners"])):
        raise ValueError("last3f-current-race-identity-or-settled-state-changed")
    return payload, _gap_summary(payload["horses"])


def _result_status(payload, evidence):
    states, gaps = _gap_summary(payload["horses"])
    if not gaps:
        return "completed", {"remaining_gap_runners": 0, "current_clock_states": dict(states)}
    source = {row["horse_number"]: row for row in evidence["rows"]}
    remaining_states = [source[number]["last3f_state"] for number in gaps]
    numeric = any(row["last3f_state"] == "valid" for row in evidence["rows"])
    by_number = {int(row["horse_number"]): row for row in payload["horses"]}
    legitimate_nonfinish = all(str(_finish_value(by_number[number]) or "").strip() in NON_FINISH_TOKENS for number in gaps)
    domain = numeric and legitimate_nonfinish and all(state == "missing" for state in remaining_states)
    return ("domain_missing" if domain else "partial"), {"remaining_gap_runners": len(gaps),
        "remaining_source_states": dict(Counter(remaining_states)), "current_clock_states": dict(states),
        "source_missing_is_not_numeric_recovery": domain, "remaining_are_legitimate_nonfinishers": legitimate_nonfinish}


def _apply_source(run_dir, plan, target, body, html, *, url, observed, phase, cache_receipt=None, transport=None):
    try:
        evidence = parse_last3f_evidence(html, race_id=target["race_id"], source_url=url, observed_at_utc=observed,
            raw_body_sha256=hashlib.sha256(body).hexdigest(), expected_date=target["race_date"],
            expected_runners=target["expected_runners"], raw_body=body,
            allow_cached_transport_identity=phase == "cache", cache_receipt=cache_receipt)
    except (ValueError, KeyError, TypeError) as exc:
        proof = _seal_unverified(run_dir, target, body, html, url=url, observed=observed,
                                  transport=transport, reason=str(exc)[:300])
        raise SourceRejected(str(exc), proof) from exc
    sealed = seal_source(Path(run_dir), evidence, body, html, transport or {"phase": phase})
    try:
        result = apply_evidence(plan["database"], evidence, batch_id=plan["batch_id"])
    except (ValueError, KeyError, TypeError, sqlite3.Error) as exc:
        raise SourceRejected(str(exc), sealed) from exc
    payload, _ = _current(plan["database"], target)
    status, remainder = _result_status(payload, evidence)
    return {"status": status, **result, **remainder, **sealed, "identity_grade": evidence["source"]["identity_grade"]}


def _cache_rows(cache, race_id):
    rows = []
    for url in (f"https://race.netkeiba.com/race/result.html?race_id={race_id}", f"https://db.netkeiba.com/race/{race_id}/"):
        row = cache.execute("SELECT normalized_url,status,body,fetched_at FROM http_cache WHERE normalized_url=?", (url,)).fetchone()
        if row:
            rows.append(row)
    return rows


def _check_protection(plan):
    current = preservation_receipt()
    if protected_projection(current) != protected_projection(plan["protected_before"]):
        raise ValueError("last3f-protected-model-h04-or-source-pins-changed")
    return current


def _write_status(run_dir, plan_sha, targets, journal, *, status, phase, **extra):
    counts = +journal.status_counts
    if len(targets) > len(journal.latest):
        counts["not_started"] = len(targets) - len(journal.latest)
    value = {"schema": "last3f-gap-status-v2", "updated_at_utc": now(), "status": status, "phase": phase,
             "plan_sha256": plan_sha, "target_races": len(targets), "race_status_counts": dict(counts),
             "cache_checked_races": len(journal.cache_checked), "cache_phase_complete": len(journal.cache_checked) == len(targets),
             "updated_runners_total": journal.updated_total,
             "event_count": journal.count, "last_event_sha256": journal.last_hash,
             "stop_request_file": str((Path(run_dir) / "stop-request.json").resolve()), **extra}
    atomic_status(Path(run_dir) / "status.json", value)
    return value


async def apply_run(run_dir, *, mode, limit=None, allow_network=False, db_path=None):
    run_dir = Path(run_dir).resolve()
    plan, plan_sha, targets = load_plan(run_dir, db_path=db_path)
    if mode not in {"cache", "network"} or (limit is not None and (isinstance(limit, bool) or limit < 1)):
        raise ValueError("last3f-execution-mode-or-limit-invalid")
    if mode == "network" and (not allow_network or limit is None):
        raise ValueError("last3f-network-needs-explicit-allow-and-positive-limit")
    lower_priority()
    stop = run_dir / "stop-request.json"
    with execution_lock(run_dir), failure_status(run_dir):
        journal = Journal(run_dir, plan_sha)
        if set(journal.latest) - {row["race_id"] for row in targets}:
            raise ValueError("last3f-events-contain-race-outside-fixed-plan")
        if mode == "network" and len(journal.cache_checked) != len(targets):
            raise ValueError("last3f-cache-pass-must-complete-before-network")
        protected_before = _check_protection(plan)
        selected = [target for target in targets if journal.latest.get(target["race_id"], {}).get("status") not in TERMINAL
                    and (mode != "cache" or target["race_id"] not in journal.cache_checked)]
        if mode == "network":
            selected.sort(key=lambda row: (row["race_date"], row["race_id"]), reverse=True)
        if limit is not None:
            selected = selected[:limit]
        processed = updated = 0
        job_state, stop_reason = "completed_selected_batch", None
        last_protection = time.monotonic()
        _write_status(run_dir, plan_sha, targets, journal, status="running", phase=mode)

        def checkpoint():
            nonlocal last_protection
            if stop.exists():
                return False
            if time.monotonic() - last_protection > 30:
                _check_protection(plan)
                last_protection = time.monotonic()
            return True

        def record(target, result):
            nonlocal processed, updated
            journal.emit(**{**result, "phase": mode, "race_id": target["race_id"]})
            processed += 1
            updated += int(result.get("updated_runners", 0))
            _write_status(run_dir, plan_sha, targets, journal, status="running", phase=mode,
                          processed_this_run=processed, updated_runners_this_run=updated)

        if mode == "cache":
            cache = read_only(plan["cache_database"]) if Path(plan["cache_database"]).exists() else None
            try:
                from scraping.fetch_pipeline import decode_html_body
                for target in selected:
                    if not checkpoint():
                        job_state, stop_reason = "stopped", "operator-stop-request"
                        break
                    try:
                        candidates = _cache_rows(cache, target["race_id"]) if cache is not None else []
                        if not candidates:
                            # No source can be applied here. Avoid loading every
                            # historical runner JSON just to report a cache miss;
                            # network/apply still performs the full live check.
                            record(target, {"status": "pending", "reason": "no-verified-cache-source",
                                "cache_sources_found": 0, "network_retry_candidate": True,
                                "current_revalidated": False, "updated_runners": 0})
                            await asyncio.sleep(.003)
                            continue
                        _, (_, gaps) = _current(plan["database"], target)
                        if not gaps:
                            result = {"status": "completed", "reason": "already-no-repairable-gaps", "updated_runners": 0}
                        else:
                            failures, successes, failed_proofs = [], [], []
                            for url, status, raw, fetched in candidates:
                                if status != 200 or raw is None or not isinstance(fetched, (int, float)) or not math.isfinite(fetched):
                                    failures.append("cache-status-body-or-timestamp-unverified")
                                    continue
                                try:
                                    body = bytes(raw)
                                    observed = datetime.fromtimestamp(fetched, timezone.utc).isoformat()
                                    receipt = {"normalized_url": url, "status": status,
                                               "body_sha256": hashlib.sha256(body).hexdigest(), "fetched_at": fetched}
                                    result = _apply_source(run_dir, plan, target, body, decode_html_body(body), url=url,
                                        observed=observed, phase=mode, cache_receipt=receipt, transport={"cache_receipt": receipt})
                                    successes.append(result)
                                    if result["status"] in TERMINAL:
                                        break
                                except (ValueError, KeyError, TypeError) as exc:
                                    failures.append(str(exc)[:300])
                                    if getattr(exc, "details", None):
                                        failed_proofs.append(exc.details)
                            if successes:
                                result = {**successes[-1], "updated_runners": sum(row["updated_runners"] for row in successes),
                                          "source_attempts": len(successes) + len(failures), "other_cache_failures": failures,
                                          "source_proofs": failed_proofs + [{key: row[key] for key in ("proof_file", "proof_sha256")} for row in successes]}
                            else:
                                result = {"status": "pending", "reason": "no-verified-cache-source", "cache_failures": failures,
                                          "cache_sources_found": len(candidates), "network_retry_candidate": True,
                                          "source_proofs": failed_proofs, "updated_runners": 0}
                    except (ValueError, KeyError, TypeError, sqlite3.Error) as exc:
                        result = {"status": "pending", "reason": str(exc)[:300], "updated_runners": 0, **getattr(exc, "details", {})}
                    record(target, result)
                    await asyncio.sleep(.003)
            finally:
                if cache is not None:
                    cache.close()
        else:
            import aiohttp
            from market_edge.compute import CollectorMonitor
            from scraping.constants import get_random_headers
            from scraping.fetch_pipeline import fetch_bytes, decode_html_body, fetch_context
            monitor = CollectorMonitor(TRACK_PATH)
            masked_streak = 0
            with fetch_context(job_id=plan["batch_id"]) as metrics:
                async with aiohttp.ClientSession(headers=get_random_headers(), cookie_jar=aiohttp.DummyCookieJar(),
                                                 timeout=aiohttp.ClientTimeout(total=30)) as session:
                    for target in selected:
                        if not checkpoint():
                            job_state, stop_reason = "stopped", "operator-stop-request"
                            break
                        try:
                            _, (_, gaps) = _current(plan["database"], target)
                            if not gaps:
                                record(target, {"status": "completed", "reason": "already-no-repairable-gaps", "updated_runners": 0})
                                continue
                            while True:
                                if not checkpoint():
                                    job_state, stop_reason = "stopped", "operator-stop-request"
                                    break
                                protection = monitor.snapshot()
                                if not protection.get("healthy"):
                                    job_state, stop_reason = "paused", "collector-unhealthy"
                                    break
                                instant = time.time()
                                conflicts = [row["race_id"] for row in protection.get("capture_windows", [])
                                             if row["start"] - 210 <= instant <= row["end"] + 60]
                                if not protection.get("capturing") and not conflicts:
                                    break
                                _write_status(run_dir, plan_sha, targets, journal, status="waiting_for_forward_capture", phase=mode,
                                              protected_race_ids=conflicts)
                                await asyncio.sleep(1)
                            if stop_reason:
                                break
                            url = f"https://race.netkeiba.com/race/result.html?race_id={target['race_id']}"
                            response = await fetch_bytes(session, url, use_cache=False, force_refresh=True,
                                max_retries=0, retry_statuses=set(), allow_redirects=False, min_interval_sec=1.0,
                                max_body_bytes=3_000_000, total_timeout_sec=30)
                            observed = now()
                            if response.status in {401, 403, 429} or response.source == "access-blocked":
                                record(target, {"status": "blocked", "reason": response.error or f"http-{response.status}",
                                                "http_status": response.status, "updated_runners": 0})
                                job_state, stop_reason = "blocked", "provider-access-or-rate-limit"
                                break
                            if response.status != 200 or not response.body:
                                result = {"status": "pending", "reason": response.error or f"http-{response.status}",
                                          "http_status": response.status, "updated_runners": 0}
                            else:
                                html = decode_html_body(response.body)
                                if any(token in html for token in ("ログインが必要です", "ログインしてください", "ログインが必要になります")) and not re.search(r'<table[^>]+class=[\"\'][^\"\']*(?:RaceTable01|race_table_01|ResultsByRaceDetail)', html):
                                    record(target, {"status": "blocked", "reason": "authentication-required-page", "updated_runners": 0})
                                    job_state, stop_reason = "blocked", "authentication-required-page"
                                    break
                                result = _apply_source(run_dir, plan, target, response.body, html, url=url, observed=observed, phase=mode,
                                    transport={"response_status": response.status, "received_at_utc": observed,
                                               "source": response.source, "attempts": response.attempts,
                                               "collector_protection": {"protocol_sha256": protection["protocol_sha256"], "healthy": True}})
                        except (ValueError, KeyError, TypeError, sqlite3.Error) as exc:
                            result = {"status": "pending", "reason": str(exc)[:300], "updated_runners": 0, **getattr(exc, "details", {})}
                        unresolved_masked = (result.get("status") == "partial" and result.get("updated_runners", 0) == 0
                                             and result.get("remaining_source_states", {}).get("masked", 0) > 0)
                        masked_streak = masked_streak + 1 if unresolved_masked else 0
                        result["consecutive_unresolved_masked_sources"] = masked_streak
                        member_marker = unresolved_masked and any(token in html for token in
                            ("有料会員限定", "プレミアム会員限定", "会員登録が必要です"))
                        record(target, result)
                        if masked_streak >= 3 or member_marker:
                            job_state, stop_reason = "paused", "source-still-masked-member-gate" if member_marker else "source-still-masked"
                            break
                network_metrics = dict(metrics)
        protected_after = _check_protection(plan)
        remaining = [row for row in targets if journal.latest.get(row["race_id"], {}).get("status") not in TERMINAL]
        if not stop_reason:
            if mode == "cache" and len(journal.cache_checked) < len(targets):
                job_state = "partial_cache_pass"
            elif remaining:
                job_state = "partial_pending_sources"
            else:
                job_state = "completed_with_domain_missing" if any(row.get("status") == "domain_missing" for row in journal.latest.values()) else "completed"
        summary = _write_status(run_dir, plan_sha, targets, journal, status=job_state, phase=mode,
                    processed_this_run=processed, updated_runners_this_run=updated, remaining_nonterminal_races=len(remaining),
                    stop_reason=stop_reason, protected_state_unchanged=protected_projection(protected_before) == protected_projection(protected_after),
                    network_metrics=network_metrics if mode == "network" else {"network_requests": 0})
        receipt_path = run_dir / ("execution-" + datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ") + ".json")
        exclusive_json(receipt_path, summary)
        return summary


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run-dir", type=Path, required=True)
    parser.add_argument("--db", type=Path, default=DB_PATH)
    parser.add_argument("--cache-db", type=Path, default=CACHE_PATH)
    group = parser.add_mutually_exclusive_group()
    group.add_argument("--apply-cache", action="store_true")
    group.add_argument("--apply-network", action="store_true")
    group.add_argument("--request-stop", action="store_true")
    parser.add_argument("--allow-network", action="store_true")
    parser.add_argument("--limit", type=int)
    args = parser.parse_args()
    if args.allow_network and not args.apply_network:
        parser.error("--allow-network is accepted only with --apply-network")
    if args.limit is not None and args.limit < 1:
        parser.error("--limit must be positive")
    run_dir = args.run_dir.resolve()
    if args.request_stop:
        # Stopping must remain possible even after unrelated source/model changes.
        plan = read_json(run_dir / "plan.json")
        receipt = read_json(run_dir / "plan_receipt.json")
        if plan.get("schema") != SCHEMA or receipt.get("plan_sha256") != digest_file(run_dir / "plan.json"):
            raise ValueError("last3f-stop-plan-binding-invalid")
        if not (run_dir / "stop-request.json").exists():
            exclusive_json(run_dir / "stop-request.json", {"requested_at_utc": now(), "operation": "finish current transaction and stop"})
        print(json.dumps({"status": "stop_requested", "run_dir": str(run_dir)}))
        return
    if args.apply_cache or args.apply_network:
        result = asyncio.run(apply_run(run_dir, mode="network" if args.apply_network else "cache", limit=args.limit,
                                      allow_network=args.allow_network, db_path=args.db))
    elif (run_dir / "plan.json").exists():
        result, _, _ = load_plan(run_dir, db_path=args.db)
    else:
        result = create_plan(run_dir, db_path=args.db, cache_path=args.cache_db)
    print(json.dumps({"run_dir": str(run_dir), "status": result.get("status", "planned_dry_run"),
                      "counts": result.get("counts", result.get("race_status_counts")),
                      "updated_runners_this_run": result.get("updated_runners_this_run", 0)}, ensure_ascii=False), flush=True)


if __name__ == "__main__":
    main()

