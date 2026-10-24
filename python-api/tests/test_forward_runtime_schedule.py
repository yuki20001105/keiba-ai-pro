"""Synthetic schedule fixtures only: no provider evidence or live access."""
from copy import deepcopy

import pytest

from forward_runtime.schedule import (ForwardError, aware, normalize_discovery,
                                      schedule_state, study_days, validate_race)


def race(number=1, *, post="2026-09-20T15:00:00+09:00"):
    return {"race_id": f"2026060406{number:02d}", "venue": "06", "race_type": "flat", "post_time": post}


def evidence(rows, *, status="complete"):
    return {"status": status, "source_url": "https://race.netkeiba.com/test-fixture",
            "source_sha256": "a" * 64, "observed_at": "2026-09-19T00:00:00Z",
            "received_at": "2026-09-19T00:00:01Z", "calendar_verified": True,
            "active_venues": ["06"], "venue_race_counts": {"06": len(rows)},
            "expected_race_ids": [r["race_id"] for r in rows], "excluded_races": []}


def normalize(value, rows):
    return normalize_discovery("20260920", value, rows, now="2026-09-20T05:00:00Z")


def test_exact_fixed_90_day_window():
    assert len(study_days()) == 90
    assert study_days()[0] == "20260920" and study_days()[-1] == "20261218"


@pytest.mark.parametrize("now,expected", [
    ("2026-09-20T05:54:29Z", "queued"), ("2026-09-20T05:54:30Z", "due"),
    ("2026-09-20T05:55:00Z", "due"), ("2026-09-20T05:55:00.001Z", "missed"),
])
def test_capture_window_boundary(now, expected):
    assert schedule_state(race()["post_time"], now) == expected


@pytest.mark.parametrize("value", ["2026-09-20T15:00:00", None, 123])
def test_naive_or_invalid_time_rejected(value):
    with pytest.raises(ForwardError):
        aware(value)


@pytest.mark.parametrize("change", [
    {"post_time": "2026-09-20T15:00:59+09:00"}, {"post_time": "2026-09-21T15:00:00+09:00"},
    {"race_type": "jump"}, {"race_type": "unknown"}, {"venue": "09"},
    {"race_id": "202650040601"}, {"race_id": "202606040613"},
    {"race_date": "20260921"},
])
def test_bad_race_identity_or_scope_rejected(change):
    with pytest.raises(ForwardError):
        validate_race({**race(), **change}, "20260920")


def test_complete_program_including_explicit_jump_and_multiple_raw_evidence():
    rows = [race(n) for n in range(1, 13)]
    value = evidence(rows)
    value["excluded_races"] = [{"race_id": rows[3]["race_id"], "reason": "jump"}]
    value["additional"] = {"calendar": {"source_url": "https://race.netkeiba.com/calendar", "source_sha256": "b" * 64}}
    clean, scheduled = normalize(value, rows[:3] + rows[4:])
    assert clean["status"] == "complete" and len(scheduled) == 11
    assert clean["additional"] == value["additional"]


@pytest.mark.parametrize("change", [
    {"calendar_verified": False}, {"venue_race_counts": {"06": 12}},
    {"expected_race_ids": []}, {"active_venues": ["09"]},
    {"active_venues": [{}]}, {"expected_race_ids": [{}]},
    {"received_at": "2026-09-21T00:00:00Z"}, {"source_sha256": "bad"},
])
def test_incomplete_or_malformed_evidence_not_complete(change):
    rows = [race()]
    with pytest.raises(ForwardError):
        normalize({**evidence(rows), **change}, rows)


def test_nonconsecutive_venue_program_rejected():
    rows = [race(1), race(3)]
    with pytest.raises(ForwardError, match="program-identity"):
        normalize(evidence(rows), rows)


def test_partial_retains_known_rows_without_promoting_empty_response():
    rows = [race()]
    clean, actual = normalize({**evidence(rows, status="partial"), "calendar_verified": False}, rows)
    assert clean["status"] == "partial" and len(actual) == 1
    clean, actual = normalize({"status": "unknown", "reason": "HTTP 400"}, [])
    assert clean["status"] == "unknown" and actual == []


def test_no_race_requires_verified_empty_calendar():
    e = evidence([], status="not_held")
    e.update(active_venues=[], venue_race_counts={})
    assert normalize(e, [])[0]["status"] == "not_held"
    with pytest.raises(ForwardError, match="empty-calendar"):
        normalize({**e, "calendar_verified": False}, [])


def test_duplicate_schedule_and_ambiguous_exclusion_rejected():
    r = race()
    with pytest.raises(ForwardError, match="duplicate"):
        normalize(evidence([r, r]), [r, r])
    e = evidence([r]); e["excluded_races"] = [{"race_id": r["race_id"], "reason": "jump"}]
    with pytest.raises(ForwardError, match="ambiguous"):
        normalize(e, [r])
