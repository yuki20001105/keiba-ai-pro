"""Immutable, one-shot entry-card / actual-win-odds research evidence.

This is intentionally NOT an application publisher or a forward collector.
The existing current-card parser and shared request pacer are reused without
editing their sources. Live capture must run in a dedicated CLI process: the
fetch observers are process-local, restored before returning, and never attach
to an existing API or H04 process. Replays cannot earn live-capture authority.
"""
from __future__ import annotations

import asyncio
from dataclasses import asdict, dataclass
from datetime import datetime, timedelta, timezone
from email.utils import parsedate_to_datetime
import hashlib
import importlib.util
import io
import json
import math
import os
from pathlib import Path
import re
import threading
from typing import Callable
from urllib.parse import parse_qs, urlsplit
import uuid

JST = timezone(timedelta(hours=9))
SCHEMA = "db-shared-win-prestart-capture-v1"
PREPARED_SCHEMA = "db-shared-win-prestart-prepared-v1"
ROOT = Path(__file__).resolve().parents[2]
DEFAULT_STORE = ROOT / "reports/generated/db-shared-win-prestart"
_CAPTURE_LOCK = threading.Lock()
_IMPORTED_CAPTURE_SHA256 = hashlib.sha256(Path(__file__).read_bytes()).hexdigest()
_RACE_ID = re.compile(r"\d{12}\Z")
_OUTCOME_KEYS = ("finish", "finish_position", "finish_time", "time", "time_seconds",
                 "last_3f_time", "margin", "speed_deviation")
_CARD_KEYS = ("race_id", "horse_number", "horse_id", "horse_name", "bracket_number",
              "sex", "age", "sex_age", "jockey_weight", "burden_weight", "weight_kg",
              "horse_weight", "weight_diff", "horse_weight_change", "jockey_name",
              "jockey_id", "trainer_name", "trainer_id", "sire", "damsire", "date",
              "distance", "track_type", "venue", "_shutuba", "odds", "popularity",
              "odds_status")


class CaptureError(ValueError):
    """Invalid evidence, never a reason to waive a capture gate."""


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _iso(value: datetime) -> str:
    if value.tzinfo is None:
        raise CaptureError("timezone_required")
    return value.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def _instant(value: str | datetime) -> datetime:
    try:
        parsed = value if isinstance(value, datetime) else datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        if parsed.tzinfo is None:
            raise ValueError("naive")
        return parsed.astimezone(timezone.utc)
    except (ValueError, TypeError) as error:
        raise CaptureError("timezone_aware_instant_required") from error


def _body(value) -> bytes:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"),
                      allow_nan=False).encode("utf-8")


def sha256(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


@dataclass(frozen=True)
class CapturePolicy:
    """Predeclared T−5 window, not a rule adjusted after seeing race outcomes."""
    lead_seconds: int = 300
    tolerance_seconds: int = 60
    max_source_age_seconds: int = 300
    max_card_quote_skew_seconds: int = 60

    def __post_init__(self):
        values = asdict(self)
        if any(type(item) is not int or item <= 0 for item in values.values()):
            raise CaptureError("positive_integer_capture_policy_required")
        if self.tolerance_seconds >= self.lead_seconds:
            raise CaptureError("capture_window_must_end_before_post")

    def window(self, post: datetime) -> tuple[datetime, datetime]:
        post = _instant(post)
        target = post - timedelta(seconds=self.lead_seconds)
        delta = timedelta(seconds=self.tolerance_seconds)
        return target - delta, target + delta


def _expected_urls(race_id: str) -> dict[str, str]:
    return {
        "card": f"https://race.netkeiba.com/race/shutuba.html?race_id={race_id}",
        "odds": f"https://race.netkeiba.com/api/api_get_jra_odds.html?race_id={race_id}&type=1&action=update",
    }


def _source_kind(url: str, race_id: str) -> str | None:
    parsed = urlsplit(str(url))
    if parsed.scheme != "https" or parsed.hostname != "race.netkeiba.com" or parsed.username or parsed.password:
        return None
    query = parse_qs(parsed.query)
    if query.get("race_id") != [race_id]:
        return None
    if parsed.path == "/race/shutuba.html":
        return "card"
    if parsed.path == "/api/api_get_jra_odds.html" and query.get("type") == ["1"] and query.get("action") == ["update"]:
        return "odds"
    return None


def _post(snapshot: dict, race_id: str, post_time: datetime) -> datetime:
    from services.race_snapshot import is_trustworthy_race_date
    info = snapshot.get("race_info") or {}
    post = _instant(post_time)
    date = str(info.get("date") or "")
    if not is_trustworthy_race_date(race_id, date):
        raise CaptureError("entry_card_date_unverified")
    try:
        card_post = datetime.strptime(date + " " + str(info.get("post_time")), "%Y%m%d %H:%M").replace(tzinfo=JST)
    except ValueError as error:
        raise CaptureError("entry_card_post_time_unverified") from error
    if card_post.astimezone(timezone.utc) != post:
        raise CaptureError("entry_card_post_time_mismatch")
    return post


def _numbers(horses: list[dict]) -> list[int]:
    result = []
    for row in horses:
        number = row.get("horse_number")
        if isinstance(number, bool) or not str(number).isdigit() or not 1 <= int(number) <= 18:
            raise CaptureError("horse_number_invalid")
        result.append(int(number))
    if len(result) != len(set(result)):
        raise CaptureError("duplicate_horse_number")
    return result


def normalize_entry(snapshot: dict, race_id: str) -> dict:
    """Do not persist leaked current outcomes or profile-derived pseudo-history."""
    from services.race_snapshot import snapshot_validation_errors
    errors = snapshot_validation_errors(snapshot, race_id)
    if errors:
        raise CaptureError("entry_card_invalid: " + "; ".join(errors))
    info, horses = snapshot["race_info"], snapshot["horses"]
    if not _RACE_ID.fullmatch(race_id) or not "01" <= race_id[4:6] <= "10":
        raise CaptureError("jra_race_id_required")
    if len(horses) < 2 or len(horses) > 18 or int(info.get("num_horses", 0)) != len(horses):
        raise CaptureError("complete_entry_card_required")
    _numbers(horses)
    ids = [str(row.get("horse_id") or "") for row in horses]
    if not all(ids) or len(ids) != len(set(ids)):
        raise CaptureError("unique_horse_ids_required")
    if snapshot.get("return_tables") or info.get("lap_cumulative") or info.get("lap_sectional"):
        raise CaptureError("result_payload_not_allowed")
    for row in [info, *horses]:
        if (row.get("odds_status") in {"result", "settled"}
                or any(row.get(key) not in (None, "", 0, "0") for key in _OUTCOME_KEYS)):
            raise CaptureError("current_result_fields_not_allowed")
    if any(row.get("_shutuba") is not True for row in horses):
        raise CaptureError("entry_lifecycle_not_verified")
    # The existing parser does not expose an explicit cancellation category.
    # Refuse status-bearing rows until an active-entry interpretation is proven.
    if any(row.get(key) not in (None, "", "active", "出走")
           for row in horses for key in ("entry_status", "status", "scratch_status")):
        raise CaptureError("entry_status_unverified")
    cleaned_info = {key: value for key, value in info.items()
                    if key not in _OUTCOME_KEYS and key not in {"lap_cumulative", "lap_sectional"}}
    return {"race_info": cleaned_info,
            "horses": [{key: row[key] for key in _CARD_KEYS if key in row} for row in horses]}


def _records(frame):
    import pandas as pd
    return [{key: None if value is None or pd.isna(value) else
             value.item() if hasattr(value, "item") else value for key, value in row.items()}
            for row in frame.to_dict("records")]


def _matrix_values(frame):
    import pandas as pd
    return [[None if item is None or pd.isna(item) else
             item.item() if hasattr(item, "item") else item for item in row]
            for row in frame.to_numpy(dtype=object)]


def raw46_features(snapshot: dict, db_path: Path) -> tuple[dict, dict]:
    """The same serving formula / ordered46 as training, DB read-only."""
    import pandas as pd
    from training import db_shared_features as shared
    from training import db_shared_optimized as optimized
    # One read-only transaction binds exactly the rows used for all features;
    # a mutable DB file mtime / later snapshot hash is not input provenance.
    current = shared._current_card(snapshot["race_info"], snapshot["horses"])
    day = str(current.race_date.iloc[0])
    previous_day = (datetime.strptime(day, "%Y%m%d") - timedelta(days=1)).strftime("%Y%m%d")
    # Extended entity statistics need ALL eligible prior JRA flat races, not
    # only races in the current horses' careers. This includes jockey/trainer
    # support counts from competitors absent from this target entry card.
    history, _unused_related, coverage = shared.load_db_frames(db_path, "20130101", previous_day)
    frame = shared.build_training_features(current, history)
    selected = optimized.select_features(frame)
    values = _matrix_values(selected)
    history_bytes = io.BytesIO()
    history.to_parquet(history_bytes, index=False)
    history_raw = history_bytes.getvalue()
    source_payload = {"schema": "canonical-db-history-read-snapshot-v1",
                      "target_race_id": str(snapshot["race_info"]["race_id"]),
                      "target_date": day, "canonical_columns": list(current),
                      "current": _records(current), "history_object": "sources/history.parquet",
                      "history_object_sha256": sha256(history_raw), "history_rows": len(history),
                      "raw47_columns": list(frame), "raw47_values": _matrix_values(frame),
                      "history_scope": "ALL complete eligible JRA flat rows since 2013; strictly prior target day"}
    source_sha = sha256(_body(source_payload))
    audit = {"source_inventory": coverage,
             "current_outcomes_consumed": False, "same_day_or_future_history_used": False,
             "history_max_source_date": str(history.race_date.max()) if not history.empty else None,
             "history_rows": len(history), "history_races": int(history.race_id.nunique()) if not history.empty else 0,
             "read_transaction": "SQLite mode=ro; query_only=ON; one explicit BEGIN",
             "snapshot_sha256": source_sha, "source_row_scope": "canonical current plus observed eligible historical rows",
             "history_capture_time_verified": False,
             "complete_career_coverage_verified": False}
    return {
        "profile": optimized.PROFILE_ID,
        "pipeline": optimized.FEATURE_PIPELINE,
        "columns": list(optimized.FEATURE_COLUMNS), "values": values,
        "feature_source_hashes": optimized.feature_source_hashes(),
        "input_sha256": sha256(_body({"columns": list(selected), "values": values})),
        "missing_policy": "JSON null retained; never impute missing history as zero",
        "canonical_source_snapshot": source_payload,
        "canonical_source_snapshot_sha256": source_sha,
        "_canonical_history_parquet": history_raw,
    }, audit


def _direct_module():
    path = ROOT / "scripts/db_shared_win_direct_features.py"
    spec = importlib.util.spec_from_file_location("db_shared_win_direct_features", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module, sha256(path.read_bytes())


def prepare_history_state(db_path: Path, target_date: str, store: Path) -> dict:
    """Prepare one immutable whole-world prior-day state before quote cutoff.

    No provider request, current entry card, model fit or collector mutation.
    The source is exactly the complete observed eligible JRA flat DB universe.
    This is an explicit research cache, not a backdated historical snapshot.
    """
    from training import db_shared_features as shared
    from forward_adapters import feature_state
    day = datetime.strptime(target_date, "%Y%m%d").date()
    previous_day = (day - timedelta(days=1)).strftime("%Y%m%d")
    history, _related, coverage = shared.load_db_frames(db_path, "20130101", previous_day)
    envelope = feature_state.build_history_state(history, target_date=target_date)
    module, module_sha = _direct_module()
    buffer = io.BytesIO()
    history.to_parquet(buffer, index=False)
    objects = {"state.json": _body(envelope), "history.parquet": buffer.getvalue()}
    metadata = {
        "schema": PREPARED_SCHEMA, "target_date": target_date,
        "prepared_at": _iso(utcnow()), "history_scope": "ALL complete eligible JRA flat rows since 2013; strictly prior target day",
        "state_sha256": envelope["state_sha256"],
        "history_source_rows_sha256": envelope["state"]["source_rows_sha256"],
        "history_rows": len(history), "history_races": int(history.race_id.nunique()),
        "history_min_source_date": str(history.race_date.min()) if not history.empty else None,
        "history_max_source_date": str(history.race_date.max()) if not history.empty else None,
        "history_parquet_sha256": sha256(objects["history.parquet"]),
        "feature_source_hashes": module.source_hashes(), "feature_module_sha256": module_sha,
        "raw_feature_source_hashes": shared.source_hashes(), "source_inventory": coverage,
        "history_capture_time_verified": False, "complete_career_coverage_verified": False,
        "research_only": True, "model_activated": False, "forward_modified": False,
    }
    objects["metadata.json"] = _body(metadata)
    store = _safe_store(store) / "prepared"
    store.mkdir(parents=True, exist_ok=True)
    name = utcnow().strftime("%Y%m%dT%H%M%S%fZ") + "-" + uuid.uuid4().hex[:12]
    staged, final = store / (name + ".partial"), store / name
    staged.mkdir()
    entries = {}
    for relative, raw in objects.items():
        _write_new(staged / relative, raw)
        entries[relative] = {"sha256": sha256(raw), "bytes": len(raw)}
    _write_new(staged / "artifact_manifest.json", _body({"schema": PREPARED_SCHEMA + "-manifest", "files": entries}))
    staged.rename(final)
    checked, _state, _history = read_prepared_state(final, target_date)
    return {"status": "prepared", "directory": str(final), "target_date": target_date,
            "state_sha256": checked["state_sha256"], "history_rows": checked["history_rows"],
            "history_races": checked["history_races"], "history_max_source_date": checked["history_max_source_date"],
            "feature_module_sha256": module_sha, "network_requests": 0, "research_only": True}


def read_prepared_state(directory: Path, target_date: str) -> tuple[dict, dict, bytes]:
    from forward_adapters import feature_state
    from training import db_shared_features as shared
    directory = Path(directory)
    if directory.name.endswith(".partial") or directory.is_symlink():
        raise CaptureError("prepared_state_incomplete_or_symlink")
    manifest = json.loads((directory / "artifact_manifest.json").read_bytes())
    if manifest.get("schema") != PREPARED_SCHEMA + "-manifest" or set(manifest.get("files", {})) != {"state.json", "history.parquet", "metadata.json"}:
        raise CaptureError("prepared_state_manifest_invalid")
    objects = {}
    for name, receipt in manifest["files"].items():
        path = directory / name
        if path.is_symlink():
            raise CaptureError("prepared_state_symlink_object")
        raw = path.read_bytes()
        if sha256(raw) != receipt["sha256"] or len(raw) != receipt["bytes"]:
            raise CaptureError("prepared_state_object_hash_mismatch")
        objects[name] = raw
    meta, envelope = json.loads(objects["metadata.json"]), json.loads(objects["state.json"])
    state = envelope["state"]
    if (meta.get("schema") != PREPARED_SCHEMA or meta.get("target_date") != target_date
            or state.get("target_date") != target_date):
        raise CaptureError("prepared_state_target_day_mismatch")
    if (envelope.get("state_sha256") != feature_state._hash(state)
            or meta.get("state_sha256") != envelope["state_sha256"]
            or meta.get("history_source_rows_sha256") != state.get("source_rows_sha256")
            or meta.get("history_rows") != state.get("history_row_count")
            or meta.get("history_races") != state.get("history_race_count")
            or meta.get("history_parquet_sha256") != sha256(objects["history.parquet"])):
        raise CaptureError("prepared_state_history_binding_invalid")
    maximum, minimum = state.get("history_max_source_date"), state.get("history_min_source_date")
    if (maximum is not None and str(maximum) >= target_date) or (minimum is not None and str(minimum) < "20130101"):
        raise CaptureError("prepared_state_history_date_invalid")
    module, module_sha = _direct_module()
    if (meta.get("feature_module_sha256") != module_sha
            or meta.get("feature_source_hashes") != module.source_hashes()
            or meta.get("raw_feature_source_hashes") != shared.source_hashes()
            or state.get("source_modules") != feature_state._modules()):
        raise CaptureError("prepared_state_feature_source_mismatch")
    return meta, envelope, objects["history.parquet"]


def prepared_raw46_features(snapshot: dict, prepared: Path) -> tuple[dict, dict]:
    """Fast current transformation: no SQLite scan or history rebuild at cutoff."""
    from training import db_shared_features as shared
    from training import db_shared_optimized as optimized
    from forward_adapters import feature_state
    current = shared._current_card(snapshot["race_info"], snapshot["horses"])
    day = str(current.race_date.iloc[0])
    meta, envelope, history_raw = read_prepared_state(prepared, day)
    generated = feature_state.build_current_features(envelope, current)
    raw47 = generated.loc[:, shared.FEATURE_COLUMNS].copy()
    selected = optimized.select_features(raw47)
    module, module_sha = _direct_module()
    extended = module._finish_result(generated)
    # Schema/order and exact numeric/categories/NaN parity are independently
    # tested against the cold historical builder. This concrete check binds
    # the sealed base46/current identity to that same prepared transformation.
    parity = module._raw47_parity(extended, raw47, extended.loc[:, list(module.IDENTITY_COLUMNS)], input_identities=current)
    state = envelope["state"]
    receipt = {
        "schema": module.SCHEMA, "mode": "current_prepared", "source_hashes": module.source_hashes(),
        "feature_columns": list(module.FEATURE_COLUMNS), "feature_count": 88,
        "identity_columns": list(module.IDENTITY_COLUMNS), "history_contract": dict(module.HISTORY_CONTRACT),
        "target_date": day, "state_sha256": envelope["state_sha256"],
        "history_source_rows_sha256": state["source_rows_sha256"],
        "history_rows": state["history_row_count"], "history_races": state["history_race_count"],
        "history_date_from": state["history_min_source_date"], "history_date_to": state["history_max_source_date"],
        "rows": len(extended), "races": int(extended.race_id.nunique()),
        "frame_sha256": module.worker.frame_hash(extended), "base46_parity": parity,
        "formula_sha256": module.worker.canonical_hash({"sources": module.source_hashes(), "columns": list(module.FEATURE_COLUMNS)}),
        "input_outcomes_consumed": False, "same_day_or_future_history_used": False,
        "dummy_current_results_used": False, "prestart_capture_provenance_verified": False,
        "complete_career_coverage_verified": False, "eligible_for_ev": False,
        "preparation_directory": str(Path(prepared).resolve()), "history_rebuilt_at_cutoff": False,
        "SQLite_read_at_cutoff": False, "independent_cold_warm_parity_scope": "frozen-source tests; current capture base46 identity/value binding",
    }
    source_payload = {
        "schema": "canonical-db-history-read-snapshot-v1", "target_race_id": str(snapshot["race_info"]["race_id"]),
        "target_date": day, "canonical_columns": list(current), "current": _records(current),
        "history_object": "sources/history.parquet", "history_object_sha256": sha256(history_raw),
        "history_rows": meta["history_rows"], "history_scope": meta["history_scope"],
        "raw47_columns": list(raw47), "raw47_values": _matrix_values(raw47),
        "prepared_state_sha256": meta["state_sha256"], "prepared_at": meta["prepared_at"],
    }
    return {
        "profile": optimized.PROFILE_ID, "pipeline": optimized.FEATURE_PIPELINE,
        "columns": list(selected), "values": _matrix_values(selected),
        "feature_source_hashes": optimized.feature_source_hashes(),
        "input_sha256": sha256(_body({"columns": list(selected), "values": _matrix_values(selected)})),
        "canonical_source_snapshot": source_payload,
        "canonical_source_snapshot_sha256": sha256(_body(source_payload)),
        "_canonical_history_parquet": history_raw,
        "_prepared_extension": {"columns": list(module.FEATURE_COLUMNS), "values": _matrix_values(extended.loc[:, list(module.FEATURE_COLUMNS)]),
                                "identities": _records(extended.loc[:, list(module.IDENTITY_COLUMNS)]),
                                "formula_receipt": receipt, "transform_file_sha256": module_sha},
    }, {
        "current_outcomes_consumed": False, "same_day_or_future_history_used": False,
        "history_max_source_date": meta["history_max_source_date"], "history_rows": meta["history_rows"],
        "history_races": meta["history_races"], "read_transaction": "immutable prepared state; no SQLite read at cutoff",
        "snapshot_sha256": sha256(_body(source_payload)), "history_capture_time_verified": False,
        "complete_career_coverage_verified": False, "prepared_at": meta["prepared_at"],
        "prepared_state_sha256": meta["state_sha256"],
    }


def _source_receipts(sources: list[dict], race_id: str, post: datetime, now: datetime,
                     policy: CapturePolicy) -> tuple[list[dict], list[str]]:
    receipts, reasons, kinds = [], [], set()
    lower, upper = policy.window(post)
    for source in sources:
        kind = _source_kind(source.get("url", ""), race_id)
        if kind is None or kind in kinds:
            reasons.append("source_identity_or_count_invalid")
            continue
        kinds.add(kind)
        raw = source.get("body")
        if not isinstance(raw, bytes) or not raw:
            reasons.append(f"{kind}_source_body_missing")
            continue
        receipt = {key: source.get(key) for key in
                   ("url", "request_url", "status", "source", "request_started_at", "observed_at", "server_date")}
        receipt.update(kind=kind, body_sha256=sha256(raw), body_size=len(raw),
                       body_file=f"sources/{kind}.{'html' if kind == 'card' else 'json'}")
        receipts.append(receipt)
        if _source_kind(source.get("request_url", source.get("url", "")), race_id) != kind:
            reasons.append(f"{kind}_request_identity_invalid")
        if source.get("status") != 200 or source.get("source") != "network":
            reasons.append(f"{kind}_fresh_network_not_verified")
        try:
            started, observed = _instant(source["request_started_at"]), _instant(source["observed_at"])
            if started > observed or observed > now or started >= post or observed >= post:
                reasons.append(f"{kind}_source_clock_invalid")
            if now - observed > timedelta(seconds=policy.max_source_age_seconds):
                reasons.append(f"{kind}_source_stale")
            if not lower <= observed <= upper:
                reasons.append(f"{kind}_source_outside_fixed_window")
            if source.get("server_date"):
                try:
                    server_date = _instant(parsedate_to_datetime(source["server_date"]))
                    if abs((observed - server_date).total_seconds()) > policy.max_source_age_seconds:
                        reasons.append(f"{kind}_provider_local_clock_skew_exceeded")
                except (CaptureError, ValueError, TypeError, OverflowError):
                    reasons.append(f"{kind}_provider_clock_invalid")
        except (CaptureError, KeyError):
            reasons.append(f"{kind}_source_clock_unverified")
    if kinds != {"card", "odds"}:
        reasons.append("card_and_odds_sources_required")
    if len(receipts) == 2:
        try:
            skew = abs((_instant(receipts[0]["observed_at"]) - _instant(receipts[1]["observed_at"])).total_seconds())
            if skew > policy.max_card_quote_skew_seconds:
                reasons.append("card_quote_skew_exceeded")
        except CaptureError:
            pass
    return receipts, reasons


def make_bundle(*, snapshot: dict, sources: list[dict], race_id: str, post_time: datetime,
                db_path: Path, mode: str, now: datetime | None = None,
                policy: CapturePolicy | None = None,
                feature_builder: Callable | None = None) -> tuple[dict, dict[str, bytes]]:
    """Build evidence without writes; only a fresh CLI acquisition is live mode."""
    from scraping.odds import parse_tansho_odds_payload
    if sha256(Path(__file__).read_bytes()) != _IMPORTED_CAPTURE_SHA256:
        raise CaptureError("capture_source_changed_since_import")
    if mode not in {"live", "sealed_replay"}:
        raise CaptureError("capture_mode_invalid")
    now, policy = _instant(now or utcnow()), policy or CapturePolicy()
    safe = normalize_entry(snapshot, race_id)
    post = _post(safe, race_id, post_time)
    receipts, reasons = _source_receipts(sources, race_id, post, now, policy)
    lower, upper = policy.window(post)
    if now >= post or now.astimezone(JST).date() != post.astimezone(JST).date():
        reasons.append("not_current_prestart_capture")
    if not lower <= now <= upper:
        reasons.append("bundle_completed_outside_fixed_window")
    if mode == "sealed_replay":
        reasons.append("replay_is_not_live_capture")
    odds_source = next((row for row in sources if _source_kind(row.get("url", ""), race_id) == "odds"), None)
    quotes, quote_status, quote_source_times = {}, "unavailable", {}
    if odds_source:
        try:
            payload = json.loads(odds_source["body"])
            quotes, popularity, quote_status = parse_tansho_odds_payload(payload)
            root = payload.get("data") if isinstance(payload, dict) else None
            quoted = root.get("odds", {}).get("1", {}) if isinstance(root, dict) else {}
            if isinstance(root, dict):
                # URL/runner-count alone must not conceal an API body for a
                # different meeting/race. These are actual provider identity
                # fields; no date is fabricated from the race-id middle bits.
                expected_identity = {"yy": int(race_id[:4]) % 100, "jyo": int(race_id[4:6]),
                                     "kai": int(race_id[6:8]), "nichi": int(race_id[8:10]),
                                     "rno": int(race_id[10:12])}
                for key, expected_value in expected_identity.items():
                    if key in root:
                        try:
                            actual_value = int(root[key])
                            if key == "yy" and actual_value >= 2000:
                                actual_value %= 100
                            if actual_value != expected_value:
                                reasons.append("provider_odds_race_identity_mismatch")
                        except (TypeError, ValueError):
                            reasons.append("provider_odds_race_identity_invalid")
                quote_source_times = {key: root.get(key) for key in
                                      ("send_date", "send_time", "official_datetime", "update_datetime")}
                quote_time = root.get("update_datetime") or root.get("official_datetime")
                if quote_time:
                    try:
                        instant = datetime.strptime(str(quote_time), "%Y-%m-%d %H:%M:%S").replace(tzinfo=JST).astimezone(timezone.utc)
                        observed = _instant(odds_source["observed_at"])
                        if instant > observed or observed - instant > timedelta(seconds=policy.max_source_age_seconds):
                            reasons.append("provider_quote_clock_out_of_bounds")
                    except (ValueError, CaptureError, KeyError):
                        reasons.append("provider_quote_clock_invalid")
            # Do not silently throw away a malformed active runner's quote.
            if len(quotes) != len(quoted) or any(not math.isfinite(price) or price < 1 for price in quotes.values()):
                reasons.append("invalid_win_odds")
            quotes = {number: price for number, price in quotes.items()
                      if math.isfinite(price) and price >= 1}
            if quote_status != "middle":
                reasons.append("actual_prestart_odds_status_required")
            numbers = _numbers(safe["horses"])
            if set(quotes) != set(numbers):
                reasons.append("card_odds_runner_keys_mismatch")
            for row in safe["horses"]:
                number = int(row["horse_number"])
                # Explicitly overwrite display odds with this exact API body.
                row.update(odds=quotes.get(number), popularity=popularity.get(number), odds_status=quote_status)
        except (ValueError, TypeError, KeyError, AttributeError):
            reasons.append("win_odds_source_invalid")
    features, history_audit = (feature_builder or raw46_features)(safe, db_path)
    if len(features.get("columns", [])) != 46 or len(features.get("values", [])) != len(safe["horses"]):
        raise CaptureError("raw46_runner_aligned_features_required")
    if (history_audit.get("current_outcomes_consumed") is not False
            or history_audit.get("same_day_or_future_history_used") is not False):
        reasons.append("feature_history_safety_unverified")
    max_history = history_audit.get("history_max_source_date")
    if max_history is not None and str(max_history) >= str(safe["race_info"]["date"]):
        reasons.append("history_not_strictly_before_target")
    # Actual acquisition completes BEFORE formula generation. Re-check the
    # server clock after generation, so slow history work cannot conceal a
    # crossed post/deadline. The CLI never supplies a historical `now` value.
    completion = _instant(utcnow()) if mode == "live" else now
    if completion < now:
        reasons.append("server_clock_moved_backwards")
    if completion >= post or completion > upper:
        reasons.append("feature_generation_finished_after_deadline")
    for receipt in receipts:
        try:
            if completion - _instant(receipt["observed_at"]) > timedelta(seconds=policy.max_source_age_seconds):
                reasons.append("source_stale_after_feature_generation")
        except CaptureError:
            pass
    reasons = list(dict.fromkeys(reasons))
    # The snapshot is also written as its own hashed object for future extension
    # replay. A current DB history snapshot is not a claim of historic capture.
    source_payload = features.pop("canonical_source_snapshot", None)
    history_raw = features.pop("_canonical_history_parquet", None)
    prepared_extension = features.pop("_prepared_extension", None)
    bundle = {
        "schema": SCHEMA, "mode": mode, "race_id": race_id,
        "capture_source_sha256": _IMPORTED_CAPTURE_SHA256,
        "captured_at": _iso(completion), "post_time": _iso(post),
        "fixed_window": {"policy": asdict(policy), "opens_at": _iso(lower), "deadline": _iso(upper)},
        "policy_sha256": sha256(_body(asdict(policy))),
        "card": safe, "quotes": {str(key): value for key, value in sorted(quotes.items())},
        "odds_kind": "actual_market_single_win", "odds_status": quote_status,
        "quote_source_times": quote_source_times,
        "quote_source_clock_semantics": "raw provider fields preserved; local receive instant is independent",
        "source_receipts": receipts, "features46": features, "history_audit": history_audit,
        "capture_validation": {"point_in_time_verified": not reasons and mode == "live",
                               "reason_codes": reasons,
                               "clock_authority": "server_local_UTC_not_client_or_DB_created_at",
                               "simultaneous": False,
                               "coherence": "bounded independently timestamped card/quote acquisition"},
        "research_only": True, "eligible_for_ev": False, "purchase_approved": False,
        "artifact_published": False, "model_activated": False, "forward_modified": False,
        "scheduler_started": False, "feature_generated_at": _iso(completion),
        "scope": "single current entry card plus exact fresh quote; replay never retrofits live provenance",
        "extension_contract": {
            "status": "canonical_inputs_preserved_pending_frozen_extension_transform",
            "allowed_input": "strict-prior canonical history; current outcome-free entry card; whole peer quote vector",
            "canonical_source_file": "sources/canonical_inputs.json" if source_payload is not None else None,
            "canonical_source_snapshot_sha256": features.get("canonical_source_snapshot_sha256"),
            "feature_count_base": 46, "extended_model_published": False,
        },
    }
    raw_files = {receipt["body_file"]: next(source["body"] for source in sources
                 if _source_kind(source.get("url", ""), race_id) == receipt["kind"])
                 for receipt in receipts}
    if source_payload is not None:
        raw_files["sources/canonical_inputs.json"] = _body(source_payload)
    if history_raw is not None:
        raw_files["sources/history.parquet"] = history_raw
    if prepared_extension is not None:
        bundle["prepared_features88"] = prepared_extension
    if sha256(Path(__file__).read_bytes()) != _IMPORTED_CAPTURE_SHA256:
        raise CaptureError("capture_source_changed_during_feature_generation")
    return bundle, raw_files


def attach_direct_features(bundle: dict, raw_files: dict[str, bytes]) -> dict:
    """Research-only88+market4 from the exact sealed full-history inputs.

    No original DB or provider request is used in this step. Source identities
    and the base46 values must match the newly frozen train/current transform.
    A replay remains a replay: recomputation cannot promote source timestamps.
    """
    import pandas as pd
    required = {"sources/canonical_inputs.json", "sources/history.parquet"}
    if not required <= set(raw_files):
        raise CaptureError("sealed_full_history_objects_required_for_extension")
    source = json.loads(raw_files["sources/canonical_inputs.json"])
    if (source.get("history_scope") != "ALL complete eligible JRA flat rows since 2013; strictly prior target day"
            or source.get("history_object_sha256") != sha256(raw_files["sources/history.parquet"])
            or source.get("target_race_id") != bundle.get("race_id")):
        raise CaptureError("extension_history_scope_or_hash_invalid")
    current = pd.DataFrame(source["current"], columns=source["canonical_columns"])
    raw47 = pd.DataFrame(source["raw47_values"], columns=source["raw47_columns"])
    module, module_sha = _direct_module()
    prepared = bundle.get("prepared_features88")
    if prepared is not None:
        if (prepared.get("columns") != list(module.FEATURE_COLUMNS)
                or prepared.get("transform_file_sha256") != module_sha
                or prepared.get("formula_receipt", {}).get("source_hashes") != module.source_hashes()):
            raise CaptureError("prepared_current_extension_contract_mismatch")
        full = pd.concat([pd.DataFrame(prepared["identities"]),
                          pd.DataFrame(prepared["values"], columns=prepared["columns"])], axis=1)
        receipt = prepared["formula_receipt"]
        module._raw47_parity(full, raw47, full.loc[:, list(module.IDENTITY_COLUMNS)], input_identities=current)
    else:
        history = pd.read_parquet(io.BytesIO(raw_files["sources/history.parquet"]))
        full, receipt = module.build_current_features(current, history, raw47=raw47)
    if (receipt.get("input_outcomes_consumed") is not False
            or receipt.get("same_day_or_future_history_used") is not False
            or receipt.get("dummy_current_results_used") is not False
            or receipt.get("base46_parity", {}).get("verified") is not True):
        raise CaptureError("extension_feature_parity_or_safety_failed")
    # Return order must be keyed, not assumed from a client table. The frozen
    # transform sorts canonical rows; remap the sealed base/quotes to that order.
    numbers = [int(value) for value in full.horse_number]
    card_by_number = {int(row["horse_number"]): row for row in bundle["card"]["horses"]}
    quoted = full.copy()
    quoted["odds"] = [bundle["quotes"].get(str(number)) for number in numbers]
    quoted["expected_runner_count"] = len(numbers)
    quoted["num_horses"] = len(numbers)
    quoted = module.add_market_features(quoted)
    if any(str(full.horse_id.iloc[index]) != str(card_by_number[number]["horse_id"])
           for index, number in enumerate(numbers)):
        raise CaptureError("extension_output_horse_identity_mismatch")
    columns = list(module.MARKET_COLUMNS) + list(module.FEATURE_COLUMNS)
    rows = _matrix_values(quoted.loc[:, columns])
    result = dict(bundle)
    result.pop("prepared_features88", None)
    result["features_extended"] = {
        "schema": "db-shared-win-direct-captured-input-v1",
        "feature_count": 92, "base46_count": 46, "aptitude_count": 13, "search_count": 29,
        "market_count": 4, "columns": columns, "values": rows,
        "identities": _records(full.loc[:, list(module.IDENTITY_COLUMNS)]),
        "input_sha256": sha256(_body({"columns": columns, "values": rows})),
        "market_source_body_sha256": next(row["body_sha256"] for row in bundle["source_receipts"] if row["kind"] == "odds"),
        "formula_receipt": receipt, "transform_file_sha256": module_sha,
        "source_snapshot_sha256": sha256(raw_files["sources/canonical_inputs.json"]),
        "eligible_for_ev": False,
    }
    result["extension_contract"] = {**bundle["extension_contract"],
                                    "status": "train_current_formula_and_exact_base46_parity_verified",
                                    "extended_feature_count": 92, "feature_source_hashes": receipt["source_hashes"],
                                    "formula_sha256": receipt["formula_sha256"]}
    completion = utcnow()
    result["feature_generated_at"] = _iso(completion)
    result["capture_validation"] = deepcopy_validation = dict(bundle["capture_validation"])
    deepcopy_validation["reason_codes"] = list(bundle["capture_validation"]["reason_codes"])
    if bundle.get("mode") == "live":
        if completion >= _instant(bundle["post_time"]) or completion > _instant(bundle["fixed_window"]["deadline"]):
            deepcopy_validation["reason_codes"].append("extension_finished_after_deadline")
            deepcopy_validation["point_in_time_verified"] = False
        max_age = bundle["fixed_window"]["policy"]["max_source_age_seconds"]
        if any(completion - _instant(row["observed_at"]) > timedelta(seconds=max_age)
               for row in bundle["source_receipts"]):
            deepcopy_validation["reason_codes"].append("source_stale_after_extension")
            deepcopy_validation["point_in_time_verified"] = False
        result["captured_at"] = _iso(completion)
    if module_sha != sha256((ROOT / "scripts/db_shared_win_direct_features.py").read_bytes()):
        raise CaptureError("extension_source_changed_during_capture")
    return result


def _safe_store(store: Path) -> Path:
    """Never write into a collector store, model directory or broad root."""
    store = Path(store)
    target = store.resolve()
    protected = (ROOT / "reports/generated/forward-runtime", ROOT / "python-api/models",
                 ROOT / "keiba/data", ROOT / "python-api/data")
    if target in (Path(target.anchor), ROOT, ROOT / "reports", ROOT / "reports/generated"):
        raise CaptureError("dedicated_research_store_required")
    if any(target == parent.resolve() or parent.resolve() in target.parents for parent in protected):
        raise CaptureError("protected_store_write_forbidden")
    if any(item.is_symlink() for item in (store, *store.parents)):
        raise CaptureError("symlink_store_forbidden")
    return target


def _write_new(path: Path, raw: bytes):
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("xb") as stream:
        stream.write(raw)
        stream.flush()
        os.fsync(stream.fileno())


def persist_bundle(store: Path, bundle: dict, raw_files: dict[str, bytes]) -> dict:
    """Stage an entire bundle then atomically publish one unique directory.

    Failures are records too. Existing records are never updated or deleted.
    Disk interruption leaves only a distinguishable .partial directory, not a
    completed capture. Hashes are revalidated immediately after publication.
    """
    store = _safe_store(store)
    if bundle.get("schema") != SCHEMA:
        raise CaptureError("bundle_schema_invalid")
    stamp = utcnow().strftime("%Y%m%dT%H%M%S%fZ") + "-" + uuid.uuid4().hex[:12]
    parent = store / "captures"
    parent.mkdir(parents=True, exist_ok=True)
    staged, final = parent / (stamp + ".partial"), parent / stamp
    staged.mkdir()
    entries = {}
    files = {"bundle.json": _body(bundle), **raw_files}
    for relative, raw in files.items():
        if not re.fullmatch(r"bundle\.json|sources/(card\.html|odds\.json|canonical_inputs\.json|history\.parquet)", relative):
            raise CaptureError("capture_object_path_invalid")
        _write_new(staged / relative, raw)
        entries[relative] = {"sha256": sha256(raw), "bytes": len(raw)}
    manifest = {"schema": SCHEMA + "-manifest", "created_at": _iso(utcnow()),
                "files": entries, "capture_sha256": entries["bundle.json"]["sha256"]}
    _write_new(staged / "artifact_manifest.json", _body(manifest))
    # rename is atomic within this filesystem; a random unique target cannot
    # overwrite an earlier capture (Windows rename also refuses existing target).
    staged.rename(final)
    verified = read_bundle(final)
    return {"status": "captured" if verified["capture_validation"]["point_in_time_verified"] else "recorded_unverified",
            "path": str(final), "capture_sha256": manifest["capture_sha256"],
            "reason_codes": verified["capture_validation"]["reason_codes"],
            "research_only": True, "eligible_for_ev": False, "purchase_approved": False}


def read_bundle(directory: Path) -> dict:
    directory = Path(directory)
    if directory.name.endswith(".partial") or directory.is_symlink():
        raise CaptureError("incomplete_or_symlink_capture")
    manifest_path = directory / "artifact_manifest.json"
    if manifest_path.is_symlink():
        raise CaptureError("symlink_capture_object")
    manifest = json.loads(manifest_path.read_bytes())
    if manifest.get("schema") != SCHEMA + "-manifest" or "bundle.json" not in manifest.get("files", {}):
        raise CaptureError("capture_manifest_invalid")
    for relative, item in manifest["files"].items():
        if not re.fullmatch(r"bundle\.json|sources/(card\.html|odds\.json|canonical_inputs\.json|history\.parquet)", relative):
            raise CaptureError("capture_manifest_path_invalid")
        path = directory / relative
        if path.is_symlink() or (directory / "sources").is_symlink():
            raise CaptureError("symlink_capture_object")
        raw = path.read_bytes()
        if sha256(raw) != item["sha256"] or len(raw) != item["bytes"]:
            raise CaptureError("capture_object_hash_mismatch")
    if manifest["files"]["bundle.json"]["sha256"] != manifest["capture_sha256"]:
        raise CaptureError("capture_manifest_binding_invalid")
    bundle = json.loads((directory / "bundle.json").read_bytes())
    if bundle.get("schema") != SCHEMA:
        raise CaptureError("bundle_schema_invalid")
    for receipt in bundle.get("source_receipts", []):
        entry = manifest["files"].get(receipt["body_file"])
        if not entry or entry["sha256"] != receipt["body_sha256"] or entry["bytes"] != receipt["body_size"]:
            raise CaptureError("capture_source_binding_invalid")
    extension = bundle.get("extension_contract", {})
    if extension.get("canonical_source_file"):
        entry = manifest["files"].get(extension["canonical_source_file"])
        if not entry or entry["sha256"] != extension["canonical_source_snapshot_sha256"]:
            raise CaptureError("canonical_source_binding_invalid")
        source = json.loads((directory / extension["canonical_source_file"]).read_bytes())
        if source.get("history_object"):
            history_entry = manifest["files"].get(source["history_object"])
            if not history_entry or history_entry["sha256"] != source["history_object_sha256"]:
                raise CaptureError("history_source_binding_invalid")
    return bundle


def record_failure(store: Path, race_id: str, reason: str, *, post_time: datetime,
                   policy: CapturePolicy | None = None) -> dict:
    """A failed attempt remains in the denominator; never fabricated success."""
    bundle = {
        "schema": SCHEMA, "mode": "failed_attempt", "race_id": race_id,
        "captured_at": _iso(utcnow()), "post_time": _iso(_instant(post_time)) if post_time is not None else None,
        "policy": asdict(policy or CapturePolicy()), "source_receipts": [],
        "capture_validation": {"point_in_time_verified": False, "reason_codes": [reason]},
        "research_only": True, "eligible_for_ev": False, "purchase_approved": False,
        "model_activated": False, "forward_modified": False, "scheduler_started": False,
    }
    return persist_bundle(store, bundle, {})


async def acquire_current_card(session, race_id: str, race_date: str, *, allow_network: bool,
                               clock: Callable[[], datetime] = utcnow) -> tuple[dict, list[dict]]:
    """Dedicated-process acquisition, preserving original pacing and parsing.

    Only returned exact card/quote bodies are retained. Headers, credentials,
    cookies, horse profiles and unrelated sources are never persisted here.
    Cache/dedup responses cannot gain fresh-network evidence from this wrapper.
    """
    if allow_network is not True:
        raise CaptureError("explicit_allow_network_required")
    if not _RACE_ID.fullmatch(race_id):
        raise CaptureError("race_id_invalid")
    day = datetime.strptime(race_date, "%Y%m%d").date()
    if day != _instant(clock()).astimezone(JST).date() or race_id[:4] != race_date[:4]:
        raise CaptureError("current_day_only_no_historical_refresh")
    from unittest.mock import patch
    from scraping import race, odds
    original_card, original_odds = race.fetch_text, odds.fetch_text
    sources = []
    if not _CAPTURE_LOCK.acquire(blocking=False):
        raise CaptureError("isolated_capture_already_running")

    async def observe(original, current_session, url, **kwargs):
        started = clock()
        response, text = await original(current_session, url, **kwargs)
        completed = clock()
        kind = _source_kind(url, race_id)
        if kind:
            sources.append({"request_url": url, "url": response.url,
                            "status": response.status, "source": response.source,
                            "body": bytes(response.body), "request_started_at": _iso(started),
                            "observed_at": _iso(completed),
                            "server_date": next((value for key, value in response.response_headers.items()
                                                 if key.lower() == "date"), None)})
        return response, text

    async def card_fetch(*args, **kwargs):
        return await observe(original_card, *args, **kwargs)

    async def odds_fetch(*args, **kwargs):
        return await observe(original_odds, *args, **kwargs)

    async def no_profile(*_args, **_kwargs):
        # Research uses strict-prior saved DB history, not a fresh/latest horse
        # profile. Keep the exact current-card parser; avoid extra requests.
        return {}

    try:
        with patch.object(race, "fetch_text", card_fetch), patch.object(odds, "fetch_text", odds_fetch), patch.object(race, "scrape_horse_detail", no_profile):
            snapshot = await race._scrape_shutuba_fallback(session, race_id, race_date, force_refresh=True)
        if not snapshot:
            raise CaptureError("current_entry_card_unavailable")
        return snapshot, sources
    finally:
        _CAPTURE_LOCK.release()


async def replay_sealed_sources(card_receipt: Path, odds_receipt: Path, objects: Path,
                                race_id: str, race_date: str) -> tuple[dict, list[dict]]:
    """Exercise the actual existing parser from hashed, previously saved bodies.

    The previous collector's files are read-only. No current network, profile
    enrichment, receipt mutation, clock backdating, or collector import occurs.
    The caller must label the resulting bundle sealed_replay, never live.
    """
    from unittest.mock import patch
    from scraping import race, odds
    from scraping.fetch_pipeline import FetchResult, decode_html_body
    sources = []
    for path in (card_receipt, odds_receipt):
        receipt = json.loads(Path(path).read_bytes())
        digest = str(receipt.get("body_sha256", ""))
        if not re.fullmatch(r"[0-9a-f]{64}", digest):
            raise CaptureError("sealed_source_hash_invalid")
        body = (Path(objects) / digest).read_bytes()
        if sha256(body) != digest or len(body) != receipt.get("body_size"):
            raise CaptureError("sealed_source_hash_mismatch")
        source = {"url": receipt.get("url"), "request_url": receipt.get("request_url"),
                  "body": body, "status": receipt.get("status"), "source": receipt.get("source"),
                  "request_started_at": receipt.get("request_started_at"),
                  "observed_at": receipt.get("received_at")}
        if _source_kind(source["url"], race_id) is None:
            raise CaptureError("sealed_source_identity_invalid")
        sources.append(source)

    async def fetch(_session, url, **_kwargs):
        source = next((item for item in sources if _source_kind(item["url"], race_id) == _source_kind(url, race_id)), None)
        if source is None:
            raise CaptureError("sealed_replay_unexpected_fetch")
        return FetchResult(source["url"], source["url"], source["status"], source["body"],
                           "sealed-replay", 1), decode_html_body(source["body"])

    async def no_profile(*_args, **_kwargs):
        # Do not refresh a horse profile after the historical target race.
        return {}

    if not _CAPTURE_LOCK.acquire(blocking=False):
        raise CaptureError("isolated_capture_already_running")
    try:
        with patch.object(race, "fetch_text", fetch), patch.object(odds, "fetch_text", fetch), patch.object(race, "scrape_horse_detail", no_profile):
            snapshot = await race._scrape_shutuba_fallback(None, race_id, race_date, force_refresh=True)
        if not snapshot:
            raise CaptureError("sealed_entry_card_parse_failed")
        return snapshot, sources
    finally:
        _CAPTURE_LOCK.release()
