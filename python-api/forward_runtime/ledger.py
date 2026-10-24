"""Isolated collector ledger: append-only evidence and guarded projections.

Construction is read-only; only initialize() creates the dedicated database.
Lease expiry never retries an in-flight HTTP capture: it becomes 'unknown'.
There is deliberately no race/event deletion or financial-order capability.
"""
from __future__ import annotations

from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timedelta
import json
import math
from pathlib import Path
import sqlite3

from .schedule import (DATE_FROM, DATE_TO, JST, UTC, ForwardError, aware, canonical,
                       normalize_discovery, require_hash, schedule_state, sha256,
                       stamp, study_days, window_for)

REPO = Path(__file__).resolve().parents[2]
TERMINAL = {"captured", "skipped", "failed", "missed", "settled", "payout_pending", "unknown"}


@dataclass(frozen=True)
class OwnerLease:
    owner_id: str
    fence: int
    expires_at: str
    stop_requested: bool = False


class RuntimeLedger:
    def __init__(self, db_path, *, clock=None):
        self.path = Path(db_path).resolve()
        self.clock = clock
        for protected in (REPO / "keiba/data", REPO / "python-api/data", REPO / "python-api/models"):
            if self.path == protected or self.path.is_relative_to(protected):
                raise ForwardError("ledger-must-not-use-production-data-directory")

    def _now(self):
        return aware(self.clock() if self.clock else datetime.now(UTC))

    @contextmanager
    def _db(self, *, write=False):
        if not self.path.exists():
            raise ForwardError("ledger-not-initialized")
        uri = self.path.as_uri() + ("?mode=rw" if write else "?mode=ro")
        db = sqlite3.connect(uri, uri=True, timeout=10, isolation_level=None)
        db.row_factory = sqlite3.Row
        db.execute("PRAGMA foreign_keys=ON")
        try:
            if write:
                db.execute("BEGIN IMMEDIATE")
            else:
                db.execute("PRAGMA query_only=ON")
                # One consistent SQLite view for multi-query reports; a
                # concurrent capture must not split days/races/owner reads.
                db.execute("BEGIN")
            version = db.execute("SELECT value FROM runtime_meta WHERE key='schema'").fetchone()
            if not version or version[0] != "forward-runtime-v1":
                raise ForwardError("wrong-ledger-schema")
            yield db
            if write:
                db.commit()
            else:
                db.rollback()
        except BaseException:
            db.rollback()
            raise
        finally:
            db.close()

    def initialize(self, contract):
        c = json.loads(canonical(contract))
        require_hash(c.get("protocol_sha256"))
        artifacts = c.get("artifact_hashes")
        if not isinstance(artifacts, dict) or not {"model", "calibrator", "features", "rule"} <= set(artifacts):
            raise ForwardError("fixed-model-calibrator-feature-rule-required")
        for digest in artifacts.values():
            require_hash(digest)
        for key, value in {"date_from": DATE_FROM, "date_to": DATE_TO, "cutoff_seconds": 300,
                           "capture_window_seconds": 30, "max_source_age_seconds": 120,
                           "paper_only": True}.items():
            if key in c and c[key] != value:
                raise ForwardError("fixed-runtime-contract-mismatch")
            c[key] = value
        if self.path.exists():
            with self._db() as db:
                old = json.loads(db.execute("SELECT value FROM runtime_meta WHERE key='contract'").fetchone()[0])
                if old != c:
                    raise ForwardError("runtime-contract-already-frozen")
            return c
        self.path.parent.mkdir(parents=True, exist_ok=True)
        # Use an exclusive placeholder before SQLite to avoid initialize races.
        try:
            with self.path.open("xb"):
                pass
        except FileExistsError:
            raise ForwardError("concurrent-initialize-retry-read-only")
        db = sqlite3.connect(self.path, timeout=10, isolation_level=None)
        db.row_factory = sqlite3.Row
        try:
            db.execute("PRAGMA foreign_keys=ON")
            db.executescript("""
                BEGIN IMMEDIATE;
                CREATE TABLE runtime_meta(key TEXT PRIMARY KEY,value TEXT NOT NULL);
                CREATE TABLE owner(singleton INTEGER PRIMARY KEY CHECK(singleton=1),owner_id TEXT,
                    fence INTEGER NOT NULL DEFAULT 0,expires_at TEXT,stop_requested INTEGER NOT NULL DEFAULT 0);
                CREATE TABLE schedule_days(date TEXT PRIMARY KEY,status TEXT NOT NULL,discovery_json TEXT,
                    discovery_sha256 TEXT,updated_at TEXT NOT NULL);
                CREATE TABLE races(race_id TEXT PRIMARY KEY,race_date TEXT NOT NULL REFERENCES schedule_days(date),
                    post_time TEXT NOT NULL,venue TEXT NOT NULL,race_json TEXT NOT NULL,status TEXT NOT NULL,
                    owner_fence INTEGER,attempts INTEGER NOT NULL DEFAULT 0,reason TEXT,created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,snapshot_json TEXT,snapshot_sha256 TEXT,decision_json TEXT,
                    decision_sha256 TEXT,captured_at TEXT,eligible INTEGER NOT NULL DEFAULT 0,
                    settlement_json TEXT,settlement_sha256 TEXT,settled_at TEXT);
                CREATE INDEX due_races ON races(status,post_time);
                CREATE TABLE events(seq INTEGER PRIMARY KEY AUTOINCREMENT,recorded_at TEXT NOT NULL,
                    race_id TEXT,event_type TEXT NOT NULL,payload_json TEXT NOT NULL,previous_sha256 TEXT,
                    event_sha256 TEXT NOT NULL UNIQUE);
                CREATE TRIGGER no_update_events BEFORE UPDATE ON events BEGIN SELECT RAISE(ABORT,'immutable-events'); END;
                CREATE TRIGGER no_delete_events BEFORE DELETE ON events BEGIN SELECT RAISE(ABORT,'immutable-events'); END;
                CREATE TRIGGER no_delete_races BEFORE DELETE ON races BEGIN SELECT RAISE(ABORT,'immutable-race-denominator'); END;
                CREATE TRIGGER no_delete_days BEFORE DELETE ON schedule_days BEGIN SELECT RAISE(ABORT,'immutable-day-denominator'); END;
                CREATE TRIGGER no_update_contract BEFORE UPDATE ON runtime_meta BEGIN SELECT RAISE(ABORT,'immutable-runtime-contract'); END;
                CREATE TRIGGER no_delete_contract BEFORE DELETE ON runtime_meta BEGIN SELECT RAISE(ABORT,'immutable-runtime-contract'); END;
            """)
            db.execute("INSERT INTO runtime_meta VALUES('schema','forward-runtime-v1')")
            db.execute("INSERT INTO runtime_meta VALUES('contract',?)", (canonical(c),))
            db.execute("INSERT INTO runtime_meta VALUES('contract_sha256',?)", (sha256(c),))
            db.execute("INSERT INTO owner(singleton) VALUES(1)")
            now = stamp(self._now())
            db.executemany("INSERT INTO schedule_days(date,status,updated_at) VALUES(?,'unknown',?)", [(date, now) for date in study_days()])
            self._event(db, "protocol_initialized", {"contract": c, "contract_sha256": sha256(c), "planned_dates": 90})
            db.commit()
        except BaseException:
            db.rollback()  # An incomplete file is retained for investigation.
            raise
        finally:
            db.close()
        return c

    def _event(self, db, kind, payload, race_id=None):
        previous = db.execute("SELECT event_sha256 FROM events ORDER BY seq DESC LIMIT 1").fetchone()
        body = {"recorded_at": stamp(self._now()), "race_id": race_id, "event_type": kind,
                "payload": payload, "previous_sha256": previous[0] if previous else None}
        db.execute("INSERT INTO events(recorded_at,race_id,event_type,payload_json,previous_sha256,event_sha256) VALUES(?,?,?,?,?,?)",
                   (body["recorded_at"], race_id, kind, canonical(payload), body["previous_sha256"], sha256(body)))

    @staticmethod
    def _ttl(value):
        if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value) or not 1 <= value <= 300:
            raise ForwardError("lease-ttl-must-be-1-to-300-seconds")
        return value

    def _expire(self, db):
        owner = db.execute("SELECT * FROM owner WHERE singleton=1").fetchone()
        if owner["owner_id"] and aware(owner["expires_at"]) <= self._now():
            self._abandon(db, owner["fence"], "owner-lease-expired")
            self._event(db, "owner_expired", {"owner_id": owner["owner_id"], "fence": owner["fence"]})
            db.execute("UPDATE owner SET owner_id=NULL,expires_at=NULL WHERE singleton=1")

    def _abandon(self, db, fence, reason):
        rows = db.execute("SELECT race_id FROM races WHERE status='capturing' AND owner_fence=?", (fence,)).fetchall()
        for row in rows:
            db.execute("UPDATE races SET status='unknown',reason=?,updated_at=? WHERE race_id=?", (reason, stamp(self._now()), row["race_id"]))
            self._event(db, "capture_unknown", {"reason": reason, "fence": fence, "automatic_retry": False}, row["race_id"])

    def _owner(self, db, lease):
        if not isinstance(lease, OwnerLease):
            raise ForwardError("collector-owner-lease-required")
        owner = db.execute("SELECT * FROM owner WHERE singleton=1").fetchone()
        if owner["owner_id"] != lease.owner_id or owner["fence"] != lease.fence or not owner["expires_at"] or aware(owner["expires_at"]) <= self._now():
            raise ForwardError("collector-lease-lost")
        return owner

    def acquire_owner(self, owner_id, ttl_seconds=60, *, resume_stopped=False):
        ttl = self._ttl(ttl_seconds)
        if not isinstance(owner_id, str) or not owner_id.strip() or len(owner_id) > 200:
            raise ForwardError("owner-id-required")
        with self._db(write=True) as db:
            self._expire(db)
            owner = db.execute("SELECT * FROM owner WHERE singleton=1").fetchone()
            if owner["owner_id"]:
                raise ForwardError("collector-already-owned")
            if owner["stop_requested"] and not resume_stopped:
                raise ForwardError("collector-stopped-explicit-resume-required")
            fence = owner["fence"] + 1
            expires = stamp(self._now() + timedelta(seconds=ttl))
            db.execute("UPDATE owner SET owner_id=?,fence=?,expires_at=?,stop_requested=0 WHERE singleton=1", (owner_id, fence, expires))
            self._event(db, "owner_acquired", {"owner_id": owner_id, "fence": fence, "expires_at": expires, "resume_stopped": resume_stopped})
            return OwnerLease(owner_id, fence, expires)

    def heartbeat(self, lease, ttl_seconds=60):
        ttl = self._ttl(ttl_seconds)
        with self._db(write=True) as db:
            owner = self._owner(db, lease)
            expires = stamp(self._now() + timedelta(seconds=ttl))
            db.execute("UPDATE owner SET expires_at=? WHERE singleton=1", (expires,))
            self._event(db, "owner_heartbeat", {"owner_id": lease.owner_id, "fence": lease.fence, "expires_at": expires})
            return OwnerLease(lease.owner_id, lease.fence, expires, bool(owner["stop_requested"]))

    def request_stop(self):
        with self._db(write=True) as db:
            db.execute("UPDATE owner SET stop_requested=1 WHERE singleton=1")
            self._event(db, "stop_requested", {"cooperative": True, "process_terminated": False})
        return {"stop_requested": True}

    def release_owner(self, lease):
        with self._db(write=True) as db:
            self._owner(db, lease)
            self._abandon(db, lease.fence, "owner-released-during-capture")
            db.execute("UPDATE owner SET owner_id=NULL,expires_at=NULL WHERE singleton=1")
            self._event(db, "owner_released", {"owner_id": lease.owner_id, "fence": lease.fence})

    def register_day(self, date_str, discovery, races, lease):
        evidence, clean = normalize_discovery(date_str, discovery, races, now=self._now())
        with self._db(write=True) as db:
            self._owner(db, lease)
            previous = db.execute("SELECT * FROM schedule_days WHERE date=?", (date_str,)).fetchone()
            existing = {r["race_id"]: r for r in db.execute("SELECT * FROM races WHERE race_date=?", (date_str,))}
            incoming = {row["race_id"]: row for row in clean}
            missing = set(existing) - set(incoming)
            # Retain all previously scheduled flat races; later disappearance
            # cannot improve coverage or turn the day into 'not held'.
            if missing and evidence["status"] in ("complete", "not_held"):
                evidence.update(status="partial", reason="previously-scheduled-races-missing", retained_race_ids=sorted(missing))
            blocked = []
            for identifier, row in incoming.items():
                old = existing.get(identifier)
                if old is None:
                    initial = "missed" if schedule_state(row["post_time"], self._now()) == "missed" else "queued"
                    now = stamp(self._now())
                    db.execute("INSERT INTO races(race_id,race_date,post_time,venue,race_json,status,reason,created_at,updated_at) VALUES(?,?,?,?,?,?,?,?,?)",
                               (identifier, date_str, row["post_time"], row["venue"], canonical(row), initial,
                                "discovered-after-cutoff" if initial == "missed" else None, now, now))
                    self._event(db, "race_scheduled", {"race": row, "status": initial}, identifier)
                elif old["post_time"] != row["post_time"]:
                    if old["status"] != "queued" or old["attempts"]:
                        blocked.append(identifier)
                        self._event(db, "post_time_change_rejected", {"old_post_time": old["post_time"], "new_post_time": row["post_time"], "status": old["status"]}, identifier)
                    else:
                        initial = "missed" if schedule_state(row["post_time"], self._now()) == "missed" else "queued"
                        db.execute("UPDATE races SET post_time=?,race_json=?,status=?,reason=?,updated_at=? WHERE race_id=?",
                                   (row["post_time"], canonical(row), initial, "rescheduled-after-cutoff" if initial == "missed" else None, stamp(self._now()), identifier))
                        self._event(db, "post_time_changed", {"old_post_time": old["post_time"], "race": row}, identifier)
                elif old["venue"] != row["venue"] or old["race_date"] != date_str:
                    raise ForwardError("existing-race-identity-conflict")
            if blocked:
                evidence.update(status="partial", reason="post-time-change-after-attempt", guarded_race_ids=blocked)
            encoded, digest = canonical(evidence), sha256(evidence)
            if previous["discovery_sha256"] != digest:
                db.execute("UPDATE schedule_days SET status=?,discovery_json=?,discovery_sha256=?,updated_at=? WHERE date=?",
                           (evidence["status"], encoded, digest, stamp(self._now()), date_str))
                self._event(db, "day_discovered", {"date": date_str, "evidence": evidence, "discovery_sha256": digest})
            return {"date": date_str, "status": evidence["status"], "scheduled_races": db.execute("SELECT COUNT(*) FROM races WHERE race_date=?", (date_str,)).fetchone()[0]}

    def claim_due(self, lease):
        with self._db(write=True) as db:
            owner = self._owner(db, lease)
            # Account for missed deadlines even when stop was requested.
            queued = db.execute("SELECT * FROM races WHERE status='queued' ORDER BY post_time,race_id").fetchall()
            due = []
            for row in queued:
                state = schedule_state(row["post_time"], self._now())
                if state == "missed":
                    db.execute("UPDATE races SET status='missed',reason='capture-window-missed',updated_at=? WHERE race_id=?", (stamp(self._now()), row["race_id"]))
                    self._event(db, "capture_missed", {"post_time": row["post_time"], "reason": "capture-window-missed"}, row["race_id"])
                elif state == "due":
                    due.append(row)
            if owner["stop_requested"] or not due:
                return None
            if db.execute("SELECT 1 FROM races WHERE status='capturing' LIMIT 1").fetchone():
                raise ForwardError("capture-already-in-flight")
            row = due[0]
            db.execute("UPDATE races SET status='capturing',owner_fence=?,attempts=attempts+1,updated_at=? WHERE race_id=? AND status='queued'", (lease.fence, stamp(self._now()), row["race_id"]))
            self._event(db, "capture_claimed", {"owner_id": lease.owner_id, "fence": lease.fence, "attempt": row["attempts"] + 1}, row["race_id"])
            return {**json.loads(row["race_json"]), "status": "capturing", "owner_fence": lease.fence, "attempts": row["attempts"] + 1}

    def record_terminal(self, lease, race_id, status, reason):
        if status not in ("skipped", "failed", "missed") or not isinstance(reason, str) or not reason.strip() or len(reason) > 500:
            raise ForwardError("terminal-status-or-reason-invalid")
        with self._db(write=True) as db:
            self._owner(db, lease)
            row = db.execute("SELECT * FROM races WHERE race_id=?", (race_id,)).fetchone()
            if not row:
                raise ForwardError("race-not-scheduled")
            if row["status"] == status and row["reason"] == reason:
                return dict(row)
            if row["status"] not in ("queued", "capturing") or (row["status"] == "capturing" and row["owner_fence"] != lease.fence):
                raise ForwardError("race-terminal-or-not-owned")
            if status == "missed" and schedule_state(row["post_time"], self._now()) != "missed":
                raise ForwardError("capture-window-not-yet-missed")
            db.execute("UPDATE races SET status=?,reason=?,updated_at=? WHERE race_id=?", (status, reason, stamp(self._now()), race_id))
            self._event(db, "capture_" + status, {"reason": reason, "fence": lease.fence}, race_id)
            return {"race_id": race_id, "status": status, "reason": reason}

    def record_capture(self, lease, race_id, snapshot, decision):
        from .settlement import validate_capture_and_decision
        with self._db(write=True) as db:
            self._owner(db, lease)
            row = db.execute("SELECT * FROM races WHERE race_id=?", (race_id,)).fetchone()
            if not row:
                raise ForwardError("race-not-scheduled")
            if row["snapshot_json"] is not None:
                if json.loads(row["snapshot_json"]) == snapshot and json.loads(row["decision_json"]) == decision:
                    return {"race_id": race_id, "status": row["status"], "duplicate": True}
                raise ForwardError("immutable-capture-conflict")
            if row["status"] != "capturing" or row["owner_fence"] != lease.fence:
                raise ForwardError("capture-not-owned-or-already-terminal")
            contract = json.loads(db.execute("SELECT value FROM runtime_meta WHERE key='contract'").fetchone()[0])
            clean, eligible = validate_capture_and_decision(dict(row), snapshot, decision, contract, now=self._now())
            status = "skipped" if decision["decision"] == "skip" else "captured"
            db.execute("UPDATE races SET status=?,snapshot_json=?,snapshot_sha256=?,decision_json=?,decision_sha256=?,captured_at=?,eligible=?,reason=?,updated_at=? WHERE race_id=?",
                       (status, canonical(snapshot), snapshot["snapshot_sha256"], canonical(decision), sha256(decision), clean["stored_at"], int(eligible), decision.get("reason"), stamp(self._now()), race_id))
            self._event(db, "capture_committed", {"snapshot": snapshot, "decision": decision, "decision_sha256": sha256(decision), "eligible": eligible, "status": status}, race_id)
            return {"race_id": race_id, "status": status, "eligible": eligible, "snapshot_sha256": snapshot["snapshot_sha256"]}

    def record_settlement(self, lease, race_id, official):
        from .settlement import settle_win_decision
        with self._db(write=True) as db:
            self._owner(db, lease)
            row = db.execute("SELECT * FROM races WHERE race_id=?", (race_id,)).fetchone()
            if not row or row["snapshot_json"] is None or row["decision_json"] is None:
                raise ForwardError("frozen-capture-and-decision-required")
            record = self._decode(row)
            result = settle_win_decision(record, official, now=self._now())
            if row["settlement_json"]:
                previous = json.loads(row["settlement_json"])
                if previous == result:
                    return previous
                if previous["status"] == "settled":
                    raise ForwardError("immutable-final-settlement-conflict")
            encoded, digest = canonical(result), sha256(result)
            db.execute("UPDATE races SET status=?,settlement_json=?,settlement_sha256=?,settled_at=?,updated_at=? WHERE race_id=?",
                       (result["status"], encoded, digest, result.get("settled_at"), stamp(self._now()), race_id))
            self._event(db, "settlement_recorded", {"settlement": result, "settlement_sha256": digest}, race_id)
            return result

    @staticmethod
    def _decode(row):
        result = dict(row)
        for key in ("race_json", "snapshot_json", "decision_json", "settlement_json", "discovery_json"):
            if key in result:
                value = result.pop(key)
                result[key.removesuffix("_json")] = json.loads(value) if value else None
        return result

    def snapshot(self):
        with self._db() as db:
            contract = json.loads(db.execute("SELECT value FROM runtime_meta WHERE key='contract'").fetchone()[0])
            stored_hash = db.execute("SELECT value FROM runtime_meta WHERE key='contract_sha256'").fetchone()[0]
            if sha256(contract) != stored_hash:
                raise ForwardError("runtime-contract-hash-mismatch")
            return {"contract": contract, "contract_sha256": stored_hash,
                    "owner": dict(db.execute("SELECT * FROM owner WHERE singleton=1").fetchone()),
                    "days": [self._decode(r) for r in db.execute("SELECT * FROM schedule_days ORDER BY date")],
                    "races": [self._decode(r) for r in db.execute("SELECT * FROM races ORDER BY post_time,race_id")],
                    "event_count": db.execute("SELECT COUNT(*) FROM events").fetchone()[0]}

    def compact_status(self):
        """Small scheduler projection: never reads captured feature/evidence JSON.

        Unknown/failed/missed entries remain visible instead of disappearing
        from the scheduler's denominator after a lease recovery.
        """
        with self._db() as db:
            return {"owner": dict(db.execute("SELECT * FROM owner WHERE singleton=1").fetchone()),
                    "days": [dict(r) for r in db.execute("SELECT date,status FROM schedule_days ORDER BY date")],
                    "races": [{**dict(r), "has_snapshot": bool(r["has_snapshot"])} for r in db.execute(
                        "SELECT race_id,race_date,post_time,status,snapshot_json IS NOT NULL AS has_snapshot FROM races ORDER BY post_time,race_id")],
                    "event_count": db.execute("SELECT COUNT(*) FROM events").fetchone()[0]}

    def verify_events(self):
        with self._db() as db:
            previous = None
            count = 0
            for row in db.execute("SELECT * FROM events ORDER BY seq"):
                body = {"recorded_at": row["recorded_at"], "race_id": row["race_id"], "event_type": row["event_type"],
                        "payload": json.loads(row["payload_json"]), "previous_sha256": row["previous_sha256"]}
                if row["previous_sha256"] != previous or sha256(body) != row["event_sha256"]:
                    raise ForwardError("event-chain-invalid")
                previous = row["event_sha256"]; count += 1
            return {"verified_events": count, "head_sha256": previous}

    def aggregate(self, *, as_of=None):
        from .settlement import aggregate_forward
        state = self.snapshot()
        return aggregate_forward(state["days"], state["races"], as_of=as_of or self._now(), contract=state["contract"])


# Naming alias for integrations that prefer an explicit service noun.
ForwardLedger = RuntimeLedger
