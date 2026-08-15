"""Fresh, validated race snapshots for live analyze requests."""
from __future__ import annotations

import json
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


_YYYYMMDD_RE = re.compile(r"^\d{8}$")


def bind_source_observed_at(snapshot: dict) -> dict:
    """Bind a coherent fresh snapshot to one UTC retrieval-completion instant."""

    observed_at = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
    race_info = snapshot.get("race_info")
    if isinstance(race_info, dict):
        race_info["data_observed_at"] = observed_at
    horses = snapshot.get("horses")
    if isinstance(horses, list):
        for horse in horses:
            if isinstance(horse, dict):
                horse["data_observed_at"] = observed_at
    return snapshot


def is_trustworthy_race_date(race_id: str, value: Any) -> bool:
    """Return whether *value* is a real calendar date, not a race-id slice.

    Legacy on-demand analysis stored ``race_id[:8]`` as YYYYMMDD even though the
    middle digits are venue/meeting codes.  Treat that exact value as unknown.
    """

    date_text = str(value or "").strip()
    if not _YYYYMMDD_RE.fullmatch(date_text) or date_text == str(race_id)[:8]:
        return False
    try:
        datetime.strptime(date_text, "%Y%m%d")
    except ValueError:
        return False
    return True


def snapshot_validation_errors(snapshot: Any, expected_race_id: str) -> list[str]:
    """Validate fields that must be coherent before replacing a race snapshot."""

    if not isinstance(snapshot, dict):
        return ["snapshot is not an object"]
    race_info = snapshot.get("race_info")
    horses = snapshot.get("horses")
    if not isinstance(race_info, dict):
        return ["race_info is missing"]
    errors: list[str] = []
    if str(race_info.get("race_id") or "") != str(expected_race_id):
        errors.append("race_id mismatch")
    if not is_trustworthy_race_date(expected_race_id, race_info.get("date")):
        errors.append("race date is missing or derived from race_id")
    try:
        distance = int(race_info.get("distance") or 0)
    except (TypeError, ValueError):
        distance = 0
    if distance <= 0:
        errors.append("distance is missing")
    if not isinstance(horses, list) or not horses:
        errors.append("horses are missing")
        return errors

    horse_numbers: list[int] = []
    for horse in horses:
        if not isinstance(horse, dict):
            errors.append("horse row is not an object")
            continue
        try:
            horse_number = int(horse.get("horse_number"))
        except (TypeError, ValueError):
            errors.append("horse_number is missing")
            continue
        horse_numbers.append(horse_number)
        horse_race_id = str(horse.get("race_id") or expected_race_id)
        if horse_race_id != str(expected_race_id):
            errors.append(f"horse {horse_number} race_id mismatch")
        try:
            horse_distance = int(horse.get("distance") or distance)
        except (TypeError, ValueError):
            horse_distance = 0
        if horse_distance != distance:
            errors.append(f"horse {horse_number} distance mismatch")
    if len(horse_numbers) != len(set(horse_numbers)):
        errors.append("duplicate horse_number")
    return list(dict.fromkeys(errors))


def stored_rows_need_refresh(race_id: str, race_info: dict, horse_rows: list[Any]) -> bool:
    try:
        distance_invalid = int(race_info.get("distance") or 0) <= 0
    except (TypeError, ValueError):
        distance_invalid = True
    if distance_invalid or not is_trustworthy_race_date(race_id, race_info.get("date")):
        return True
    if not horse_rows:
        return True

    odds_seen = False
    for row in horse_rows:
        try:
            horse = json.loads(row[0]) if isinstance(row, (tuple, list)) else row
        except (TypeError, ValueError, json.JSONDecodeError):
            return True
        if not isinstance(horse, dict):
            return True
        if not str(horse.get("data_observed_at") or "").strip():
            return True
        try:
            odds_seen = odds_seen or float(horse.get("odds")) > 0
        except (TypeError, ValueError):
            pass
    return not odds_seen


async def fetch_fresh_race_snapshot(
    session: Any,
    race_id: str,
    stored_date: Any = "",
) -> dict | None:
    """Fetch a current coherent snapshot, bypassing resume and HTTP caches."""

    from scraping.race import _scrape_shutuba_fallback, scrape_race_full  # type: ignore

    date_text = str(stored_date or "")
    trustworthy = is_trustworthy_race_date(race_id, date_text)
    today = datetime.now().strftime("%Y%m%d")
    use_result_first = trustworthy and date_text < today

    async def _result(date_hint: str = date_text if trustworthy else "") -> dict | None:
        return await scrape_race_full(
            session,
            race_id,
            date_hint=date_hint,
            force_refresh=True,
        )

    async def _shutuba() -> dict | None:
        return await _scrape_shutuba_fallback(
            session,
            race_id,
            date_hint=date_text if trustworthy else "",
            force_refresh=True,
        )

    if use_result_first:
        result_snapshot = await _result()
        if result_snapshot and not snapshot_validation_errors(result_snapshot, race_id):
            return bind_source_observed_at(result_snapshot)

    shutuba_snapshot = await _shutuba()
    if shutuba_snapshot and not snapshot_validation_errors(shutuba_snapshot, race_id):
        # When a legacy row had a fake date, the current shutuba page can reveal
        # the real date.  Prefer the result page for a genuinely past race so a
        # refresh never replaces settled rows with pre-race-only fields.
        discovered_date = str(shutuba_snapshot.get("race_info", {}).get("date") or "")
        if (
            not use_result_first
            and is_trustworthy_race_date(race_id, discovered_date)
            and discovered_date < today
        ):
            result_snapshot = await _result(discovered_date)
            if result_snapshot and not snapshot_validation_errors(result_snapshot, race_id):
                return bind_source_observed_at(result_snapshot)
        return bind_source_observed_at(shutuba_snapshot)

    result_snapshot = await _result()
    if result_snapshot and not snapshot_validation_errors(result_snapshot, race_id):
        return bind_source_observed_at(result_snapshot)
    fallback = shutuba_snapshot or result_snapshot
    return bind_source_observed_at(fallback) if fallback else None


def save_valid_race_snapshot(snapshot: dict, db_path: Path, expected_race_id: str) -> bool:
    """Validate then atomically replace race metadata and every horse row."""

    errors = snapshot_validation_errors(snapshot, expected_race_id)
    if errors:
        raise ValueError("invalid race snapshot: " + "; ".join(errors))
    from scraping.storage import _save_race_to_ultimate_db  # type: ignore

    if not _save_race_to_ultimate_db(snapshot, db_path, overwrite=True):
        raise RuntimeError("atomic race snapshot write failed")
    return True
