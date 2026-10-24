"""Pure scheduling/discovery contracts for the fixed prospective cohort."""
from __future__ import annotations

from datetime import datetime, date, timedelta, timezone
import hashlib
import json
import re

JST = timezone(timedelta(hours=9))
UTC = timezone.utc
DATE_FROM, DATE_TO = "20260920", "20261218"
HASH = re.compile(r"^[0-9a-f]{64}$")


class ForwardError(ValueError):
    pass


def canonical(value):
    try:
        return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False)
    except (ValueError, TypeError) as exc:
        raise ForwardError("invalid-canonical-json") from exc


def sha256(value):
    return hashlib.sha256(canonical(value).encode()).hexdigest()


def aware(value):
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00")) if isinstance(value, str) else value
        if not isinstance(parsed, datetime) or parsed.tzinfo is None or parsed.utcoffset() is None:
            raise ValueError()
        return parsed.astimezone(UTC)
    except (ValueError, TypeError) as exc:
        raise ForwardError("timezone-aware-timestamp-required") from exc


def stamp(value):
    return aware(value).isoformat().replace("+00:00", "Z")


def day(value):
    if not isinstance(value, str) or not re.fullmatch(r"\d{8}", value):
        raise ForwardError("date-must-be-YYYYMMDD")
    try:
        return datetime.strptime(value, "%Y%m%d").date()
    except ValueError as exc:
        raise ForwardError("invalid-date") from exc


def study_days():
    first, last = day(DATE_FROM), day(DATE_TO)
    return [(first + timedelta(days=n)).strftime("%Y%m%d") for n in range((last - first).days + 1)]


def require_hash(value):
    if not isinstance(value, str) or not HASH.fullmatch(value):
        raise ForwardError("sha256-required")
    return value


def race_id(value, date_str=None):
    if not isinstance(value, str) or not re.fullmatch(r"\d{12}", value) or not 1 <= int(value[4:6]) <= 10 or not 1 <= int(value[-2:]) <= 12:
        raise ForwardError("jra-race-id-required")
    if date_str is not None and value[:4] != date_str[:4]:
        raise ForwardError("race-year-mismatch")
    return value


def validate_race(row, date_str):
    if not isinstance(row, dict):
        raise ForwardError("race-object-required")
    identifier = race_id(row.get("race_id"), date_str)
    if row.get("race_date", date_str) != date_str:
        raise ForwardError("declared-race-date-mismatch")
    post = aware(row.get("post_time"))
    if post.second or post.microsecond or post.astimezone(JST).strftime("%Y%m%d") != date_str:
        raise ForwardError("race-post-date-or-minute-invalid")
    if row.get("race_type") != "flat" or str(row.get("venue")) != identifier[4:6]:
        raise ForwardError("explicit-central-flat-domain-required")
    if not DATE_FROM <= date_str <= DATE_TO:
        raise ForwardError("outside-fixed-study-period")
    return {**row, "race_id": identifier, "race_date": date_str, "post_time": stamp(post),
            "venue": identifier[4:6], "race_type": "flat"}


def window_for(post_time):
    post = aware(post_time)
    if post.second or post.microsecond:
        raise ForwardError("minute-resolution-post-required")
    cutoff = post - timedelta(minutes=5)
    return cutoff - timedelta(seconds=30), cutoff


def schedule_state(post_time, now):
    start, cutoff = window_for(post_time)
    current = aware(now)
    return "queued" if current < start else "due" if current <= cutoff else "missed"


def normalize_discovery(date_str, discovery, races, *, now):
    """Validate provenance and distinguish unknown/partial from no-race days.

    The caller attests that declared venue counts/calendar came from actual raw
    evidence. A hash alone is not proof of provider completeness. This contract
    never infers 'not held' from an empty race-list response.
    """
    day(date_str)
    if not DATE_FROM <= date_str <= DATE_TO:
        raise ForwardError("outside-fixed-study-period")
    if not isinstance(discovery, dict) or not isinstance(races, list):
        raise ForwardError("discovery-object-and-races-required")
    status = discovery.get("status")
    if status not in ("complete", "partial", "unknown", "not_held"):
        raise ForwardError("discovery-status-invalid")
    clean = [validate_race(row, date_str) for row in races]
    ids = [row["race_id"] for row in clean]
    if len(set(ids)) != len(ids):
        raise ForwardError("duplicate-scheduled-race")
    value = json.loads(canonical(discovery))
    # An unavailable response may have no source evidence at all. It can only
    # be 'unknown', contain no inferred races, and retain a concrete reason.
    if status == "unknown" and not clean and not value.get("source_sha256"):
        if not isinstance(value.get("reason"), str) or not value["reason"].strip():
            raise ForwardError("unknown-discovery-reason-required")
        return {**value, "status": "unknown", "date": date_str}, clean
    require_hash(value.get("source_sha256"))
    if not isinstance(value.get("source_url"), str) or not value["source_url"].startswith("https://"):
        raise ForwardError("source-url-required")
    observed, received = aware(value.get("observed_at")), aware(value.get("received_at"))
    if not observed <= received <= aware(now):
        raise ForwardError("discovery-time-order-invalid")
    value.update(observed_at=stamp(observed), received_at=stamp(received))
    venues = value.get("active_venues", [])
    if not isinstance(venues, list) or any(not isinstance(v, str) or not re.fullmatch(r"0[1-9]|10", v) for v in venues) or len(set(venues)) != len(venues):
        raise ForwardError("active-venues-invalid")
    expected = value.get("expected_race_ids", [])
    if not isinstance(expected, list) or any(not isinstance(v, str) for v in expected) or len(set(expected)) != len(expected):
        raise ForwardError("expected-race-list-invalid")
    for identifier in expected:
        race_id(identifier, date_str)
        if identifier[4:6] not in venues:
            raise ForwardError("expected-venue-mismatch")
    exclusions = value.get("excluded_races", [])
    if not isinstance(exclusions, list):
        raise ForwardError("excluded-races-invalid")
    excluded_ids = []
    for row in exclusions:
        if not isinstance(row, dict) or row.get("reason") not in ("jump", "cancelled"):
            raise ForwardError("explicit-exclusion-reason-required")
        excluded_ids.append(race_id(row.get("race_id"), date_str))
    if len(set(excluded_ids)) != len(excluded_ids) or set(ids) & set(excluded_ids):
        raise ForwardError("ambiguous-discovery-exclusion")
    if status == "not_held":
        if value.get("calendar_verified") is not True or venues or expected or clean or exclusions:
            raise ForwardError("verified-empty-calendar-required")
    if status == "complete":
        counts = value.get("venue_race_counts")
        if value.get("calendar_verified") is not True or not venues or not isinstance(counts, dict) or set(counts) != set(venues):
            raise ForwardError("complete-calendar-and-venue-counts-required")
        if any(type(n) is not int or not 1 <= n <= 12 for n in counts.values()):
            raise ForwardError("declared-venue-count-invalid")
        if any(sum(identifier[4:6] == venue for identifier in expected) != count for venue, count in counts.items()):
            raise ForwardError("partial-race-list-not-complete")
        for venue, count in counts.items():
            venue_ids = [identifier for identifier in expected if identifier[4:6] == venue]
            if len({identifier[:10] for identifier in venue_ids}) != 1 or {int(identifier[-2:]) for identifier in venue_ids} != set(range(1, count + 1)):
                raise ForwardError("venue-program-identity-incomplete")
        if set(expected) != set(ids) | set(excluded_ids):
            raise ForwardError("missing-or-extra-discovery-races")
    if status in ("partial", "unknown") and not value.get("reason"):
        value["reason"] = "race-universe-not-completely-proven"
    value.update(date=date_str, active_venues=venues, expected_race_ids=expected, excluded_races=exclusions)
    return value, clean
