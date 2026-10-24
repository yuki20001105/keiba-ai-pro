"""Resident, local, paper-only runtime with immutable raw evidence and deadlines.

The fixed H04/model/calibrator/rule are copied verbatim from the earlier frozen
protocol. This new protocol corrects provider adapter assumptions only. It
cannot promote models, place orders or mutate the acquisition database.
"""
from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone
import hashlib
import json
import os
from pathlib import Path
import time
import uuid

import pandas as pd

from . import provider
from .ledger import RuntimeLedger
from .schedule import canonical, sha256, stamp, window_for, study_days, JST
from forward_adapters.feature_state import build_current_features
from forward_adapters.inference import score_fixed_model
from prospective.policy import decide, RULE

REPO = Path(__file__).resolve().parents[2]
UTC = timezone.utc


def now():
    return datetime.now(UTC)


def file_hash(path):
    digest = hashlib.sha256()
    with Path(path).open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def read_json(path):
    return json.loads(Path(path).read_text(encoding="utf-8"))


def exclusive_json(path, value):
    body = canonical(value).encode()
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        with path.open("xb") as stream:
            stream.write(body)
            stream.flush()
            os.fsync(stream.fileno())
    except FileExistsError:
        if path.read_bytes() != body:
            raise ValueError("immutable-object-conflict")


def object_bytes(root, body):
    sha = hashlib.sha256(body).hexdigest()
    target = Path(root) / "objects" / sha
    target.parent.mkdir(parents=True, exist_ok=True)
    try:
        with target.open("xb") as stream:
            stream.write(body)
            stream.flush()
            os.fsync(stream.fileno())
    except FileExistsError:
        if file_hash(target) != sha:
            raise ValueError("immutable-raw-object-corrupted")
    return sha


def source_manifest():
    paths = []
    for package in ("forward_runtime", "forward_adapters", "experiments", "prospective"):
        paths.extend((REPO / "python-api" / package).glob("*.py"))
    paths += [REPO / "scripts/run_forward_collector.py"]
    paths += [REPO / "python-api" / name for name in ("training/eligibility.py", "training/local_retrain.py", "scraping/fetch_pipeline.py")]
    return {str(p.relative_to(REPO)).replace("\\", "/"): file_hash(p) for p in sorted(paths)}


def initialize(root, parent_store, clean_dir, daily_dir):
    root, parent = Path(root).resolve(), Path(parent_store).resolve()
    if (root / "protocol.json").exists():
        raise ValueError("runtime-already-initialized-use-status-or-run")
    prior = read_json(parent / "protocol.json")
    if sha256(prior["protocol"]) != prior["protocol_sha256"]:
        raise ValueError("parent-protocol-hash-mismatch")
    p = prior["protocol"]
    if now() >= provider.aware(p["start_at"]):
        raise ValueError("freeze-before-study-start-required")
    if p["fixed_model_id"] != "H04" or not p["paper_only"] or p["automatic_purchase"]:
        raise ValueError("fixed-paper-H04-parent-required")
    for key, digest in p["artifact_hashes"].items():
        raw = (parent / "objects" / digest).read_bytes()
        if hashlib.sha256(raw).hexdigest() != digest:
            raise ValueError("parent-artifact-integrity-failure")
        object_bytes(root, raw)
    if read_json(root / "objects" / p["artifact_hashes"]["rule"]) != RULE:
        raise ValueError("parent-rule-differs-from-frozen-decision-code")
    from io import BytesIO
    import joblib
    model_body = (root / "objects" / p["artifact_hashes"]["model"]).read_bytes()
    if hashlib.sha256(model_body).hexdigest() != p["artifact_hashes"]["model"]:
        raise ValueError("model-object-integrity-failure")
    feature_categories = joblib.load(BytesIO(model_body))["encoder"].categories
    repair = read_json(Path(clean_dir) / "repair_manifest.json")
    contract = {"schema": "live-paper-runtime-protocol-v2", "parent_protocol_sha256": prior["protocol_sha256"],
                "frozen_at": stamp(now()), "start_at": p["start_at"], "end_at": p["end_at"],
                "date_from": "20260920", "date_to": "20261218", "fixed_model_id": "H04",
                "artifact_hashes": p["artifact_hashes"], "feature_columns": p["feature_columns"],
                "nullable_feature_columns": p["nullable_feature_columns"], "feature_categories": feature_categories, "source_manifest": source_manifest(),
                "cutoff_seconds": 300, "capture_window_seconds": 30, "max_source_age_seconds": 120,
                "paper_only": True, "automatic_purchase": False, "paper_rule_operationally_adopted": False,
                "initial_clean_history_sha256": repair["clean_history"]["sha256"],
                "history_policy": "daily clean export; prior calendar dates only; observed history may be incomplete; no outcome-based model or rule tuning",
                "daily_refresh_policy": "read-only DB backup and same fixed quality gate before 08:00 JST; never prepare inside capture window",
                "missing_result_policy": "published complete result and payout proof or pending; never approximate payouts from odds",
                "minimum_complete_races": 1000, "minimum_race_days": 30,
                "limitations": ["Local observation evidence is not cryptographically signed provider evidence.",
                                "Indicative pari-mutuel odds are not locked execution prices.",
                                "Historical policy robustness failed; shadow collection is diagnostic only."]}
    envelope = {"protocol": contract, "protocol_sha256": sha256(contract)}
    exclusive_json(root / "protocol.json", envelope)
    exclusive_json(root / "local-config.json", {"clean_dir": str(Path(clean_dir).resolve()),
                   "initial_daily_dir": str(Path(daily_dir).resolve()),
                   "source_db": str(REPO / "keiba/data/keiba_ultimate.db")})
    RuntimeLedger(root / "ledger.sqlite3").initialize({"protocol_sha256": envelope["protocol_sha256"],
        "artifact_hashes": p["artifact_hashes"], "date_from": "20260920", "date_to": "20261218"})
    return envelope


class Runtime:
    def __init__(self, root, *, clock=now, transport=None):
        self.root = Path(root).resolve()
        self.clock, self.transport = clock, transport
        self.envelope = read_json(self.root / "protocol.json")
        self.protocol = self.envelope["protocol"]
        self.config = read_json(self.root / "local-config.json")
        self.ledger = RuntimeLedger(self.root / "ledger.sqlite3", clock=clock)
        self.daily = self.state = self.blood = None
        self.lease = None
        self.calendar = {}
        self.last_discovery = {}
        self.last_settle = {}
        self.preparation_attempts = set()
        self.verify()

    def verify(self):
        if sha256(self.protocol) != self.envelope["protocol_sha256"] or self.protocol["source_manifest"] != source_manifest():
            raise ValueError("runtime-protocol-or-source-changed")
        for digest in self.protocol["artifact_hashes"].values():
            if file_hash(self.root / "objects" / digest) != digest:
                raise ValueError("runtime-artifact-changed")
        hashes = self.protocol["artifact_hashes"]
        if (read_json(self.root / "objects" / hashes["rule"]) != RULE
                or read_json(self.root / "objects" / hashes["features"])["feature_columns"] != self.protocol["feature_columns"]):
            raise ValueError("fixed-rule-or-feature-artifact-contract-mismatch")

    def log(self, event, **fields):
        value = {"recorded_at": stamp(self.clock()), "event": event, **fields}
        print(json.dumps(value, ensure_ascii=False), flush=True)
        exclusive_json(self.root / "events" / (stamp(self.clock()).replace(":", "") + "-" + uuid.uuid4().hex + ".json"), value)

    async def fetch(self, url, *, timeout_seconds=22):
        started = self.clock()
        if self.transport is None:
            import aiohttp
            from scraping.fetch_pipeline import fetch_text
            async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=timeout_seconds)) as session:
                result, _ = await fetch_text(session, url, use_cache=False, force_refresh=True,
                    max_retries=1, max_body_bytes=2 * 1024 * 1024)
            value = {"url": result.url, "status": result.status, "source": result.source, "body": result.body}
        else:
            value = await self.transport(url)
        received = self.clock()
        if value["source"] != "network" or value["status"] != 200 or value["url"] != url or received < started:
            raise ValueError("fresh-correct-network-response-required")
        body = value["body"]
        if not isinstance(body, bytes) or not 0 < len(body) <= 2 * 1024 * 1024:
            raise ValueError("bounded-raw-response-required")
        receipt = {"request_url": url, "url": value["url"], "status": 200, "source": "network",
                   "request_started_at": stamp(started), "received_at": stamp(received),
                   "body_sha256": object_bytes(self.root, body), "body_size": len(body),
                   "protocol_sha256": self.envelope["protocol_sha256"]}
        receipt["receipt_sha256"] = sha256(receipt)
        exclusive_json(self.root / "receipts" / (receipt["receipt_sha256"] + ".json"), receipt)
        return receipt, body

    async def load_calendar(self, year, month):
        receipt, body = await self.fetch(f"https://race.netkeiba.com/top/calendar.html?year={year}&month={month}")
        days = provider.parse_calendar(body, year, month)
        for day, venues in days.items():
            self.calendar[day] = {"venues": venues, "receipt": receipt}
        return days

    async def discover(self, day):
        if day not in self.calendar or (self.clock() - provider.aware(self.calendar[day]["receipt"]["received_at"])).total_seconds() > 21600:
            await self.load_calendar(int(day[:4]), int(day[4:6]))
        cal = self.calendar[day]
        evidence = {"status": "not_held" if not cal["venues"] else "partial", "calendar_verified": True,
                    "active_venues": cal["venues"], "venue_race_counts": {}, "expected_race_ids": [], "excluded_races": [],
                    "source_url": cal["receipt"]["url"], "source_sha256": cal["receipt"]["body_sha256"],
                    "observed_at": cal["receipt"]["received_at"], "received_at": cal["receipt"]["received_at"],
                    "calendar_receipt": cal["receipt"], "reason": "race-card-not-yet-published"}
        if not cal["venues"]:
            return self.ledger.register_day(day, evidence, [], self.lease)
        receipt, body = await self.fetch(f"https://race.netkeiba.com/top/race_list_sub.html?kaisai_date={day}")
        listed = provider.parse_race_list(body, day, receipt=receipt)
        evidence.update(source_url=receipt["url"], source_sha256=receipt["body_sha256"],
                        observed_at=receipt["received_at"], received_at=receipt["received_at"], list_receipt=receipt,
                        expected_race_ids=listed["expected_race_ids"], excluded_races=listed["excluded_races"], venue_navigation=[])
        known = []
        for venue in cal["venues"]:
            candidates = [r for r in listed["races"] if r["venue"] == venue]
            if not candidates:
                continue
            first = candidates[0]
            nav_receipt, card = await self.fetch(f"https://race.netkeiba.com/race/shutuba.html?race_id={first['race_id']}")
            nav = provider.parse_navigation(card, first["race_id"], first["post_time"])
            listed_venue = {i for i in listed["expected_race_ids"] if i[4:6] == venue}
            if set(nav) != listed_venue:
                evidence["reason"] = "list-versus-dated-card-navigation-mismatch"
                continue
            evidence["venue_race_counts"][venue] = len(nav)
            evidence["venue_navigation"].append({"race_ids": nav, "receipt": nav_receipt})
            known.extend(candidates)
        if set(evidence["venue_race_counts"]) == set(cal["venues"]):
            evidence.update(status="complete", reason=None)
        return self.ledger.register_day(day, evidence, known, self.lease)

    def load_daily(self, directory, day):
        directory = Path(directory)
        manifest = read_json(directory / "preparation_manifest.json")
        if manifest.get("target_date") != day or manifest.get("status") != "completed":
            raise ValueError("daily-history-target-mismatch")
        for name, key in (("history_state.json", "state_sha256"), ("prior_bloodlines.json", "bloodline_sha256")):
            if file_hash(directory / name) != manifest[key]:
                raise ValueError("daily-history-artifact-changed")
        self.daily = manifest
        self.state = read_json(directory / "history_state.json")
        self.blood = read_json(directory / "prior_bloodlines.json")
        if self.blood["target_date"] != day or self.state["state"]["target_date"] != day or self.state["state"]["history_max_source_date"] >= day:
            raise ValueError("daily-history-boundary-violation")
        exclusive_json(self.root / "daily-bindings" / (day + ".json"), manifest)
        return manifest

    def prepare_daily(self, day):
        from .data_quality import audit_clean_history, prepare_daily_history
        initial = Path(self.config["initial_daily_dir"])
        if read_json(initial / "preparation_manifest.json")["target_date"] == day:
            return self.load_daily(initial, day)
        directory = self.root / "daily" / day
        if not (directory / "preparation_manifest.json").exists():
            local = self.clock().astimezone(JST)
            if local.strftime("%Y%m%d") >= day and local.hour >= 8:
                raise ValueError("daily-history-not-prepared-before-0800-no-heavy-daytime-rebuild")
            import shutil
            import psutil
            from training.local_retrain import create_sqlite_snapshot
            source = Path(self.config["source_db"])
            if psutil.virtual_memory().available < 4 * 1024**3 or shutil.disk_usage(self.root).free < 2 * source.stat().st_size + 6 * 1024**3:
                raise ValueError("daily-preparation-resource-reserve-required")
            # Read-only online backup; no filtering/writes against a live WAL.
            # Every generated artifact is retained; low disk space stops rather
            # than silently deleting original data or old evidence.
            backup = create_sqlite_snapshot(source.resolve(), (self.root / "history-snapshots").resolve())
            clean = self.root / "quality" / day
            if not (clean / "quality_report.json").exists():
                audit_clean_history(backup.path, clean, before_date=day)
            prepare_daily_history(clean, directory, target_date=day)
        return self.load_daily(directory, day)

    def infer(self, card, race):
        if self.daily is None or self.daily["target_date"] != race["race_date"]:
            raise ValueError("daily-history-not-prepared")
        rows = provider.parse_card(card, race["race_id"], race["post_time"])
        for row in rows:
            old = self.blood["horses"].get(row["horse_id"])
            if old:
                if old["history_max_source_date"] >= race["race_date"]:
                    raise ValueError("future-bloodline-observation")
                row.update({key: old[key] for key in ("sire", "damsire")})
        feature_start = time.perf_counter()
        frame = build_current_features(self.state, pd.DataFrame(rows))
        frame["expected_runner_count"] = frame["num_horses"].astype(int)
        generated = self.clock()
        hashes = self.protocol["artifact_hashes"]
        scored = score_fixed_model(frame, self.root / "objects" / hashes["model"], self.root / "objects" / hashes["calibrator"],
                                  hashes["model"], hashes["calibrator"])
        predicted = self.clock()
        records = []
        for raw, (_, features), (_, scores) in zip(rows, frame.iterrows(), scored.iterrows()):
            if raw["horse_number"] != int(scores.horse_number) or raw["horse_id"] != scores.horse_id:
                raise ValueError("inference-runner-order-changed")
            values = {name: None if pd.isna(features[name]) else features[name] for name in self.protocol["feature_columns"]}
            for name, value in values.items():
                if value is None and name not in self.protocol["nullable_feature_columns"]:
                    raise ValueError("required-feature-missing:" + name)
                if hasattr(value, "item"):
                    values[name] = value.item()
            records.append({"horse_number": raw["horse_number"], "horse_id": raw["horse_id"], "features": values,
                            "score": float(scores.rank_score), "win_probability": float(scores.win_probability)})
        unknown = {name: sorted({str(v) for v in frame[name].dropna() if str(v).strip() not in categories})
                   for name, categories in self.protocol.get("feature_categories", {}).items()}
        return {"runners": records, "features_generated_at": stamp(generated), "prediction_at": stamp(predicted),
                "feature_inference_seconds": time.perf_counter() - feature_start, "normalized_inputs": rows,
                "input_quality": {"missing_feature_values": {name: int(frame[name].isna().sum()) for name in self.protocol["feature_columns"] if frame[name].isna().any()},
                                  "unknown_categories": {name: v for name, v in unknown.items() if v},
                                  "zero_fill_performed": False, "imputed_from_future_profiles": False}}

    async def capture(self, race):
        self.verify()
        start, cutoff = window_for(race["post_time"])
        if not start <= self.clock() <= cutoff:
            raise ValueError("outside-fixed-capture-window")
        # No retries across a cutoff. Two fresh paced responses, then one
        # feature+model pass. Historical cached bodies cannot be stamped anew.
        card_receipt, card = await self.fetch(f"https://race.netkeiba.com/race/shutuba.html?race_id={race['race_id']}")
        odds_receipt, odds_body = await self.fetch(f"https://race.netkeiba.com/api/api_get_jra_odds.html?race_id={race['race_id']}&type=1&action=update")
        odds = provider.parse_odds(odds_body, race["race_id"], received_at=odds_receipt["received_at"])
        tick = provider.aware(odds["provider_time"])
        if not cutoff - timedelta(seconds=120) <= tick <= provider.aware(odds_receipt["received_at"]) <= self.clock() <= cutoff:
            raise ValueError("odds-stale-or-capture-late")
        inferred = self.infer(card, race)
        if {r["horse_number"] for r in inferred["runners"]} != set(odds["odds"]):
            raise ValueError("card-and-odds-cohort-mismatch")
        for row in inferred["runners"]:
            row["odds"] = odds["odds"][row["horse_number"]]
        stored = self.clock()
        if not max(provider.aware(r["received_at"]) for r in (card_receipt, odds_receipt)) <= provider.aware(inferred["features_generated_at"]) <= provider.aware(inferred["prediction_at"]) <= stored <= cutoff:
            raise ValueError("capture-completed-after-deadline")
        normalized_sha = object_bytes(self.root, canonical(inferred["normalized_inputs"]).encode())
        snapshot = {"schema": "forward-race-snapshot-v2", "race_id": race["race_id"], "post_time": race["post_time"],
                    "cutoff_at": stamp(cutoff), "features_generated_at": inferred["features_generated_at"],
                    "prediction_at": inferred["prediction_at"], "stored_at": stamp(stored),
                    "protocol_sha256": self.envelope["protocol_sha256"], "artifact_hashes": self.protocol["artifact_hashes"],
                    "card_receipt_sha256": card_receipt["receipt_sha256"], "odds_receipt_sha256": odds_receipt["receipt_sha256"],
                    "source_at": stamp(tick), "provider_time": stamp(tick), "received_at": odds_receipt["received_at"],
                    "history_max_source_date": self.daily["history_max_source_date"], "daily_state_sha256": self.daily["state_sha256"],
                    "prior_bloodlines_sha256": self.daily["bloodline_sha256"], "normalized_inputs_sha256": normalized_sha,
                    "feature_values_sha256": sha256([r["features"] for r in inferred["runners"]]),
                    "runners": inferred["runners"], "expected_runner_count": len(inferred["runners"]),
                    "capture_kind": "direct_fetch", "policy_ready": True, "inference_verified": True, "raw_features_recomputed": True,
                    "paper_only": True, "automatic_purchase": False,
                    "feature_inference_seconds": inferred["feature_inference_seconds"],
                    "input_quality": inferred.get("input_quality", {}),
                    "history_is_complete_career": False, "history_lag_days": (provider.aware(race["post_time"]).astimezone(JST).date() - datetime.strptime(self.daily["history_max_source_date"], "%Y%m%d").date()).days}
        envelope = {"snapshot": snapshot, "snapshot_sha256": sha256(snapshot)}
        decision = decide(snapshot["runners"])
        # The ledger is the commit point. If interrupted, its lease recovery
        # leaves an unknown attempt instead of repeating network/decision.
        result = self.ledger.record_capture(self.lease, race["race_id"], envelope, decision)
        try:
            exclusive_json(self.root / "captures" / (race["race_id"] + ".json"), envelope)
            self.log("capture_committed", **result)
        except OSError as exc:
            # SQLite is already the durable commit point. An optional export
            # failure must not demote/retry an already captured race.
            print(json.dumps({"event": "capture_committed_export_pending", "race_id": race["race_id"],
                              "error": type(exc).__name__, "recover_from": "ledger.snapshot_json"}), flush=True)
        return envelope

    async def settle(self, race):
        receipt, body = await self.fetch(f"https://race.netkeiba.com/race/result.html?race_id={race['race_id']}")
        try:
            official = provider.parse_result(body, race["race_id"], race["post_time"], receipt=receipt)
        except ValueError as exc:
            official = {"race_id": race["race_id"], "status": "pending", "complete": False,
                        "source_url": receipt["url"], "source_sha256": receipt["body_sha256"],
                        "observed_at": receipt["received_at"], "received_at": receipt["received_at"], "reason": str(exc)}
        result = self.ledger.record_settlement(self.lease, race["race_id"], official)
        self.log("settlement", race_id=race["race_id"], status=result["status"])
        return result

    async def heartbeat_loop(self):
        while True:
            await asyncio.sleep(15)
            self.lease = self.ledger.heartbeat(self.lease)

    async def run(self, *, once=False, resume_stopped=False):
        self.lease = self.ledger.acquire_owner(f"pid-{os.getpid()}-{uuid.uuid4().hex}", resume_stopped=resume_stopped)
        heart = asyncio.create_task(self.heartbeat_loop())
        try:
            self.log("collector_started", pid=os.getpid(), paper_only=True)
            while True:
                if heart.done():
                    heart.result()
                instant = self.clock()
                day = instant.astimezone(JST).strftime("%Y%m%d")
                if day > self.protocol["date_to"]:
                    self.log("study-period-ended")
                    break
                state = self.ledger.compact_status()
                if state["owner"]["stop_requested"]:
                    self.log("cooperative_stop_completed")
                    break
                active_day = max(day, self.protocol["date_from"])
                # Never spend the capture window scanning raw history or
                # running discovery. Warm the prepared daily state first.
                if (self.daily is None or self.daily["target_date"] != active_day) and active_day not in self.preparation_attempts:
                    self.preparation_attempts.add(active_day)
                    try:
                        await asyncio.to_thread(self.prepare_daily, active_day)
                        self.log("daily_history_ready", target_date=active_day, history_max=self.daily["history_max_source_date"])
                    except Exception as exc:
                        self.daily = None
                        self.log("daily_history_unavailable", target_date=active_day, reason=str(exc)[:300])
                due = self.ledger.claim_due(self.lease)
                if due:
                    if self.daily is None or self.daily["target_date"] != due["race_date"]:
                        self.ledger.record_terminal(self.lease, due["race_id"], "skipped", "daily-history-not-ready-no-heavy-preparation-in-capture-window")
                        continue
                    try:
                        seconds = max(.001, (window_for(due["post_time"])[1] - self.clock()).total_seconds())
                        await asyncio.wait_for(self.capture(due), timeout=seconds)
                    except Exception as exc:
                        status = "missed" if self.clock() > window_for(due["post_time"])[1] else "failed"
                        self.ledger.record_terminal(self.lease, due["race_id"], status, f"{type(exc).__name__}: {str(exc)[:400]}")
                        self.log("capture_failed", race_id=due["race_id"], reason=str(exc)[:400])
                    continue
                # One schedule refresh hourly; source already enforces global
                # host pacing. Avoid background work within 2 min of T-5:30.
                future = [provider.aware(r["post_time"]) - timedelta(seconds=330) for r in state["races"] if r["status"] == "queued"]
                slack = min(((t - instant).total_seconds() for t in future if t > instant), default=86400)
                if slack > 120:
                    if time.monotonic() - self.last_discovery.get(active_day, -1e9) > 3600:
                        try:
                            self.log("day_discovery", **await self.discover(active_day))
                        except Exception as exc:
                            self.log("discovery_unavailable", target_date=active_day, reason=str(exc)[:300])
                            self.ledger.register_day(active_day, {"status": "unknown", "reason": str(exc)[:300]}, [], self.lease)
                        self.last_discovery[active_day] = time.monotonic()
                    for race in state["races"]:
                        if race.get("has_snapshot") and race["status"] != "settled" and instant > provider.aware(race["post_time"]) + timedelta(minutes=15):
                            if time.monotonic() - self.last_settle.get(race["race_id"], -1e9) > 1800:
                                try:
                                    await self.settle(race)
                                except Exception as exc:
                                    self.log("settlement_pending", race_id=race["race_id"], reason=str(exc)[:300])
                                self.last_settle[race["race_id"]] = time.monotonic()
                                break
                if once:
                    break
                await asyncio.sleep(min(5, max(.2, slack)))
        finally:
            heart.cancel()
            try:
                await heart
            except asyncio.CancelledError:
                pass
            self.ledger.release_owner(self.lease)
