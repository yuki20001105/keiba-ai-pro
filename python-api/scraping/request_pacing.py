"""Cross-process request admission and provider-wide adaptive backpressure.

SQLite transactions cover admission, not network I/O. A renewable lease keeps
one request in flight across workers; cooldown and access blocks survive restarts.
The clock and sleeper are injectable so rate-limit tests never wait in real time.
"""
from __future__ import annotations

import asyncio
import json
import sqlite3
import time
import uuid
import weakref
from contextlib import closing, contextmanager
from pathlib import Path
from typing import Callable
from threading import RLock
from urllib.parse import urlsplit


class AccessPaused(RuntimeError):
    pass


def site_family(url: str) -> str:
    host = (urlsplit(url).hostname or "").lower()
    return "netkeiba.com" if host == "netkeiba.com" or host.endswith(".netkeiba.com") else host


def page_kind(url: str) -> str:
    path = urlsplit(url).path
    if "/ped/" in path:
        return "pedigree"
    if "/horse/" in path:
        return "horse"
    if "odds" in path:
        return "odds"
    if "list" in path or "calendar" in path or path == "/":
        return "list"
    return "race"


class RequestPacer:
    def __init__(self, path: Path, *, clock: Callable[[], float] = time.time, sleep=None):
        self.path = Path(path)
        self.clock = clock
        self.sleep = sleep or asyncio.sleep
        self._connection = None
        self._db_lock = RLock()

    @contextmanager
    def _connect(self):
        # Keep the WAL/statement cache open. Reopening the last SQLite handle on
        # every admission/observation/release repeatedly checkpoints the WAL on
        # Windows. The lock serializes use within this process; SQLite still
        # serializes admission across independent processes.
        with self._db_lock:
            if self._connection is None:
                self.path.parent.mkdir(parents=True, exist_ok=True)
                connection = sqlite3.connect(str(self.path), timeout=5, check_same_thread=False)
                connection.row_factory = sqlite3.Row
                connection.execute("PRAGMA journal_mode=WAL")
                connection.execute("""CREATE TABLE IF NOT EXISTS request_pacing (
                    family TEXT PRIMARY KEY, interval REAL NOT NULL DEFAULT 2,
                    next_start REAL NOT NULL DEFAULT 0, cooldown_until REAL NOT NULL DEFAULT 0,
                    blocked_reason TEXT NOT NULL DEFAULT '', failures INTEGER NOT NULL DEFAULT 0,
                    rate_limits INTEGER NOT NULL DEFAULT 0, owner TEXT, lease_until REAL NOT NULL DEFAULT 0
                )""")
                connection.execute("""CREATE TABLE IF NOT EXISTS request_pacing_samples (
                    family TEXT NOT NULL, kind TEXT NOT NULL, successes INTEGER NOT NULL DEFAULT 0,
                    stable_since REAL NOT NULL, baseline REAL NOT NULL DEFAULT 0,
                    durations TEXT NOT NULL DEFAULT '[]', PRIMARY KEY (family, kind)
                )""")
                connection.commit()
                self._connection = connection
                self._finalizer = weakref.finalize(self, connection.close)
            connection = self._connection
            with connection:
                yield connection

    def close(self) -> None:
        with self._db_lock:
            if self._connection is not None:
                self._finalizer()
                self._connection = None

    def _claim(self, family: str, minimum: float, owner: str) -> float:
        now = self.clock()
        with self._connect() as connection:
            # Waiting is read-only. Avoid a write transaction/WAL checkpoint every
            # tick during a long Retry-After, while still rechecking atomically below.
            state = connection.execute("SELECT * FROM request_pacing WHERE family = ?", (family,)).fetchone()
            wait = self._wait_for_state(state, now)
            if wait > 0:
                return wait
            connection.execute("BEGIN IMMEDIATE")
            now = self.clock()  # A contended SQLite lock must not backdate admission.
            connection.execute("INSERT OR IGNORE INTO request_pacing (family) VALUES (?)", (family,))
            state = connection.execute("SELECT * FROM request_pacing WHERE family = ?", (family,)).fetchone()
            wait = self._wait_for_state(state, now)
            if wait > 0:
                return wait
            interval = max(1.0, minimum, state["interval"])
            connection.execute("""UPDATE request_pacing SET owner = ?, lease_until = ?, next_start = ?
                WHERE family = ?""", (owner, now + 600, now + interval, family))
        return 0.0

    @staticmethod
    def _wait_for_state(state, now: float) -> float:
        if state is None:
            return 0
        if state["blocked_reason"]:
            raise AccessPaused(str(state["blocked_reason"]))
        if state["owner"] and state["lease_until"] > now:
            return min(0.25, state["lease_until"] - now)
        return min(5.0, max(0.0, max(state["next_start"], state["cooldown_until"]) - now))

    async def acquire(self, family: str, minimum: float = 1.0, on_wait=None) -> tuple[str, float]:
        owner = uuid.uuid4().hex
        waited = 0.0
        while True:
            delay = self._claim(family, minimum, owner)
            if delay <= 0:
                return owner, waited
            if on_wait is not None:
                on_wait(self.status(family))
            await self.sleep(delay)
            waited += delay

    async def heartbeat(self, family: str, owner: str) -> None:
        while True:
            await asyncio.sleep(30)
            with self._connect() as connection:
                connection.execute("UPDATE request_pacing SET lease_until = ? WHERE family = ? AND owner = ?",
                                   (self.clock() + 600, family, owner))

    def release(self, family: str, owner: str) -> None:
        with self._connect() as connection:
            connection.execute("UPDATE request_pacing SET owner = NULL, lease_until = 0 WHERE family = ? AND owner = ?",
                               (family, owner))

    def observe(self, family: str, *, kind: str, status: int = 0, latency: float = 0,
                retry_after: float = 0, blocked: str = "", failure: bool = False,
                circuit_threshold: int = 3, circuit_cooldown: float = 90) -> None:
        now = self.clock()
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            connection.execute("INSERT OR IGNORE INTO request_pacing (family) VALUES (?)", (family,))
            state = connection.execute("SELECT * FROM request_pacing WHERE family = ?", (family,)).fetchone()
            if blocked:
                connection.execute("UPDATE request_pacing SET blocked_reason = ? WHERE family = ?", (blocked, family))
            if failure or status >= 400 or blocked:
                failures = state["failures"] + 1
                interval = min(60.0, max(2.0, state["interval"] * 1.5)) if failure or status >= 500 or status == 429 else state["interval"]
                cooldown = state["cooldown_until"]
                rate_limits = state["rate_limits"]
                if retry_after > 0:
                    cooldown = max(cooldown, now + retry_after)
                if status == 429:
                    rate_limits += 1
                    cooldown = max(cooldown, now + max(retry_after, 300.0 * (2 ** min(5, rate_limits - 1))))
                elif (failure or status >= 500) and failures >= max(1, circuit_threshold):
                    cooldown = max(cooldown, now + max(5.0, circuit_cooldown))
                connection.execute("""UPDATE request_pacing SET failures = ?, interval = ?, cooldown_until = ?,
                    rate_limits = ?, next_start = MAX(next_start, ?) WHERE family = ?""",
                    (failures, interval, cooldown, rate_limits, now + interval, family))
                connection.execute("DELETE FROM request_pacing_samples WHERE family = ?", (family,))
                return
            if not 200 <= status < 300:
                return
            connection.execute("UPDATE request_pacing SET failures = 0 WHERE family = ?", (family,))
            connection.execute("""INSERT OR IGNORE INTO request_pacing_samples (family, kind, stable_since)
                VALUES (?, ?, ?)""", (family, kind, now))
            sample = connection.execute("SELECT * FROM request_pacing_samples WHERE family = ? AND kind = ?", (family, kind)).fetchone()
            durations = (json.loads(sample["durations"]) + [max(0.0, latency)])[-100:]
            successes = sample["successes"] + 1
            baseline = sample["baseline"]
            stable_since = sample["stable_since"]
            if len(durations) >= 20:
                p95 = sorted(durations)[int((len(durations) - 1) * 0.95)]
                if baseline > 0 and p95 > baseline * 2:
                    connection.execute("UPDATE request_pacing SET interval = ? WHERE family = ?",
                                       (min(60.0, max(2.0, state["interval"] * 1.5)), family))
                    successes, stable_since, baseline, durations = 0, now, 0, []
                elif successes >= 50 and now - stable_since >= 120:
                    connection.execute("UPDATE request_pacing SET interval = ? WHERE family = ?",
                                       (max(1.0, state["interval"] * 0.9), family))
                    successes, stable_since, baseline = 0, now, p95
            connection.execute("""UPDATE request_pacing_samples SET successes = ?, stable_since = ?, baseline = ?, durations = ?
                WHERE family = ? AND kind = ?""", (successes, stable_since, baseline, json.dumps(durations), family, kind))

    def status(self, family: str = "netkeiba.com") -> dict:
        # Inspection must not create a DB, clear a block or refresh a lease.
        if not self.path.is_file():
            return {"family": family, "interval": 2.0, "cooldown_until": 0, "blocked_reason": ""}
        with self._db_lock:
            if self._connection is not None:
                row = self._connection.execute("SELECT * FROM request_pacing WHERE family = ?", (family,)).fetchone()
            else:
                with closing(sqlite3.connect(f"{self.path.resolve().as_uri()}?mode=ro", uri=True)) as connection:
                    connection.row_factory = sqlite3.Row
                    try:
                        row = connection.execute("SELECT * FROM request_pacing WHERE family = ?", (family,)).fetchone()
                    except sqlite3.OperationalError:
                        row = None
        return dict(row) if row else {"family": family, "interval": 2.0, "cooldown_until": 0, "blocked_reason": ""}

    def clear_block(self, family: str = "netkeiba.com") -> None:
        # Explicit operator action only. Retry-After cooldown always remains.
        with self._connect() as connection:
            connection.execute("UPDATE request_pacing SET blocked_reason = '' WHERE family = ?", (family,))
