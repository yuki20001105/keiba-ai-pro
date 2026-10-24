from datetime import datetime
import sqlite3

import pandas as pd
import pytest

from bet_types.inventory import (BET_TYPES, build_coverage, combination,
                                 expected_combinations, inspect_cache, readonly,
                                 summarize_coverage, timing_class)


def cohort():
    return pd.DataFrame({"race_id": ["202105010101"] * 3, "race_date": ["20210130"] * 3,
                         "horse_number": [1, 2, 3], "expected_runner_count": [3] * 3,
                         "odds": [1.0, 4.0, 8.0]})


@pytest.mark.parametrize("bet,expected", list(zip(BET_TYPES, [18, 18, 153, 153, 306, 816, 4896])))
def test_full_combination_counts(bet, expected):
    assert expected_combinations(bet, 18) == expected


def test_order_and_alias_normalization():
    assert combination("3-1", "umaren", {1, 2, 3}) == (1, 3)
    assert combination("3→1", "umatan", {1, 2, 3}) == (3, 1)
    assert combination("3→1→2", "sanrentan", {1, 2, 3}) == (3, 1, 2)


@pytest.mark.parametrize("value", ["1-1", "1-4", "refund 1-2", "1-2-3", "1.5-2"])
def test_malformed_combination_rejected(value):
    with pytest.raises(ValueError):
        combination(value, "wide", {1, 2, 3})


def test_payouts_never_become_odds_or_ev():
    f = cohort()
    rows = [("202105010101", "ワイド", "1-2", 200, "2026-01-01"),
            ("202105010101", "3連複", "1-2-3", 400, "2026-01-01")]
    c = build_coverage(f, rows, [("202105010101", n, o) for n, o in zip([1, 2, 3], [1., 4., 8.])])
    s = summarize_coverage(c).set_index("bet_type")
    assert s.loc["tansho", "all_combination_odds_races"] == 1
    assert s.loc["tansho", "odds_timing_class"] == "C"
    assert s.loc["sanrenpuku", "payout_races"] == 1
    assert s.loc["wide", "payout_races"] == 1
    assert s.loc["wide", "all_combination_odds_races"] == 0
    assert s.loc["wide", "odds_timing_class"] == "NO_ODDS"
    assert s.loc["wide", "D_observed_races"] == 0
    assert not s.loc["wide", "historical_ev_data_eligible"]
    assert not c.prerace_odds_verified.any()
    assert not c.nonwin_ev_roi_permitted.any()


def test_spaced_and_hyphen_ordered_grammar_is_not_sorted():
    assert combination("8 - 12", "umaren", {8, 12}) == (8, 12)
    assert combination("12 → 8 → 9", "sanrentan", {8, 9, 12}) == (12, 8, 9)
    assert combination("8-6-2", "sanrentan", {2, 6, 8}) == (8, 6, 2)


def test_duplicate_conflicting_payout_not_silently_cleaned():
    c = build_coverage(cohort(), [("202105010101", "馬連", "1-2", 200), ("202105010101", "馬連", "2-1", 300)], [])
    r = c[c.bet_type == "umaren"].iloc[0]
    assert r.duplicate_payout_rows == r.conflicting_payout_rows == 1
    assert not r.payout_combination_membership_valid


@pytest.mark.parametrize("rows", [[(1, 1.), (2, 4.)], [(1, 1.), (2, 4.), (3, 8.), (3, 8.)],
                                 [(1, 1.), (2, 4.), (3, 9.)], [(1, 1.), (2, 4.), (3, None)]])
def test_incomplete_duplicate_mismatched_odds_fail_closed(rows):
    c = build_coverage(cohort(), [], [("202105010101", n, o) for n, o in rows])
    assert not c.all_combination_odds_present.any()


def test_later_reacquisition_is_not_preoff():
    assert timing_class(historical=True, received_at="2026-09-20T00:00:00Z", post_time="2021-01-30T01:10:00Z", direct_observation=True) == "C"
    assert timing_class(received_at="2026-09-20T00:00:00Z", post_time="2021-01-30T01:10:00Z", direct_observation=True) == "D"
    assert timing_class(received_at="2021-01-30T01:04:00Z", post_time="2021-01-30T01:10:00Z", direct_observation=True) == "B"
    assert timing_class(received_at="2021-01-30T01:04:00Z", source_time="2021-01-30T01:03:59Z", post_time="2021-01-30T01:10:00Z", direct_observation=True) == "A"
    assert timing_class(received_at="2021-01-30T01:04:00Z", source_time="2021-01-30T01:04:01Z", post_time="2021-01-30T01:10:00Z", direct_observation=True) == "D"


def test_post2022_cohort_refused_before_values():
    f = cohort()
    f["race_date"] = "20230101"
    with pytest.raises(ValueError, match="2021/2022"):
        build_coverage(f, [], [])


def test_readonly_cannot_write_and_does_not_create(tmp_path):
    p = tmp_path / "source.db"
    with pytest.raises(sqlite3.OperationalError):
        with readonly(p):
            pass
    assert not p.exists()
    db = sqlite3.connect(p)
    db.execute("CREATE TABLE proof(x)")
    db.close()
    with readonly(p) as db:
        with pytest.raises(sqlite3.OperationalError):
            db.execute("INSERT INTO proof VALUES (1)")


def test_cache_only_metadata_and_unknown_candidate_is_not_ignored(tmp_path):
    cache, parsed = tmp_path / "cache.db", tmp_path / "parsed.db"
    db = sqlite3.connect(cache)
    db.execute("CREATE TABLE http_cache(normalized_url TEXT PRIMARY KEY,status INTEGER,fetched_at REAL,expires_at REAL,body BLOB)")
    db.execute("CREATE INDEX idx_http_cache_freshness ON http_cache(normalized_url,status,fetched_at,expires_at)")
    db.execute("CREATE TABLE fetch_resume(x)")
    db.executemany("INSERT INTO http_cache VALUES (?,200,1,2,?)", [
        ("https://x/race/202305010101/", b"unread later result"),
        ("https://x/odds?race_id=202305010101", b"unread later odds"),
        ("https://x/?pid=odds", b"unread unknown date")])
    db.commit(); db.close()
    db = sqlite3.connect(parsed)
    db.execute("CREATE TABLE parsed_snapshots(kind TEXT,version TEXT,payload TEXT)")
    db.execute("INSERT INTO parsed_snapshots VALUES ('horse-detail','v','unread')")
    db.commit(); db.close()
    result = inspect_cache(cache, parsed, ["202105010101"])
    assert result["metadata_rows"] == 3
    assert result["http_response_bodies_read"] == result["parsed_payload_bodies_read"] == 0
    assert len(result["cohort_odds_url_candidates"]) == 1
    assert result["cohort_odds_url_candidates"][0]["requires_scoped_followup"]
