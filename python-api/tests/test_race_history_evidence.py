from __future__ import annotations

import hashlib

import numpy as np
import pandas as pd
import pytest

from scraping.race_history_evidence import backfill_conditionals, parse_history_evidence
from test_last3f_evidence import RID, RUNNERS, URL, document


def target():
    row = {"race_id": "202106010101", "horse_id": RUNNERS[0]["horse_id"], "horse_number": 1,
           "race_date": "20211201", "venue": "中山", "surface": "turf", "distance": 2500,
           "safe_prior_starts": 7, "safe_prev_finish": 2}
    for group in ("horse_course", "horse_distance_band"):
        row[f"safe_{group}_starts"] = 0
        for field in ("win_rate", "top3_rate", "avg_speed_mps"):
            row[f"safe_{group}_{field}"] = np.nan
    return pd.DataFrame([row])


def observation(**changes):
    return {"race_id": RID, "race_date": "20210612", "horse_id": RUNNERS[0]["horse_id"],
            "venue": "中山", "surface": "turf", "distance": 2400, "finish": 2,
            "time_seconds": 150, **changes}


def test_source_time_and_finish_are_bound_to_exact_runner():
    html = document().replace("<th>後3F</th>", "<th>後3F</th><th>タイム</th>")
    html = html.replace("<td>34.8</td></tr>", "<td>34.8</td><td>1:23.4</td></tr>")
    html = html.replace("<td>**</td></tr>", "<td>**</td><td>1:23.9</td></tr>")
    proof = parse_history_evidence(
        html, race_id=RID, source_url=URL, observed_at_utc="2026-10-26T13:00:00Z",
        raw_body_sha256=hashlib.sha256(html.encode()).hexdigest(), raw_body=html.encode(),
        expected_date="20210612", expected_runners=RUNNERS)
    assert proof["rows"][0]["finish"] == 1
    assert proof["rows"][0]["time_seconds"] == 83.4
    assert proof["source"]["historical_capture_time_proven"] is False


def test_confirmed_prior_history_fills_only_empty_conditionals():
    before = target()
    after, changes = backfill_conditionals(before, [observation()])
    assert len(changes) == 2
    assert after.safe_horse_course_starts.iloc[0] == 1
    assert after.safe_horse_course_win_rate.iloc[0] == 0
    assert after.safe_horse_course_top3_rate.iloc[0] == 1
    assert after.safe_horse_course_avg_speed_mps.iloc[0] == 16
    pd.testing.assert_frame_equal(before[["safe_prior_starts", "safe_prev_finish"]],
                                  after[["safe_prior_starts", "safe_prev_finish"]])
    assert before.safe_horse_course_win_rate.isna().all()


def test_no_prior_matching_condition_remains_undefined():
    before = target()
    after, changes = backfill_conditionals(before, [observation(venue="東京", distance=1800)])
    pd.testing.assert_frame_equal(before, after)
    assert changes == []


@pytest.mark.parametrize("day", ["20211201", "20211202"])
def test_current_or_future_result_cannot_fill_history(day):
    with pytest.raises(ValueError, match="not-strictly-prior"):
        backfill_conditionals(target(), [observation(race_date=day)])


def test_duplicate_history_cannot_inflate_conditional_counts():
    with pytest.raises(ValueError, match="observation-duplicate"):
        backfill_conditionals(target(), [observation(), observation()])
