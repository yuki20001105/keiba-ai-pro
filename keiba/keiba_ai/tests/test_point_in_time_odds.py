from __future__ import annotations

import pandas as pd
import pytest

from keiba_ai.point_in_time_odds import (
    PointInTimeOddsPolicy,
    normalized_market_probability,
    select_point_in_time_win_odds,
)


def _races(expected: int = 2) -> pd.DataFrame:
    return pd.DataFrame(
        [
            {
                "race_id": "202001010101",
                "post_time": "2020-01-01T06:00:00Z",
                "expected_runner_count": expected,
            }
        ]
    )


def test_selects_latest_fresh_quote_before_decision_cutoff() -> None:
    snapshots = pd.DataFrame(
        [
            {
                "race_id": "202001010101",
                "horse_id": "H1",
                "odds": 4.0,
                "observed_at": "2020-01-01T05:50:00Z",
                "source": "licensed",
                "snapshot_kind": "pre_race",
            },
            {
                "race_id": "202001010101",
                "horse_id": "H1",
                "odds": 3.8,
                "observed_at": "2020-01-01T05:54:00Z",
                "source": "licensed",
                "snapshot_kind": "pre_race",
            },
            {
                "race_id": "202001010101",
                "horse_id": "H1",
                "odds": 3.2,
                "observed_at": "2020-01-01T05:56:00Z",
                "source": "licensed",
                "snapshot_kind": "pre_race",
            },
            {
                "race_id": "202001010101",
                "horse_id": "H2",
                "odds": 8.0,
                "observed_at": "2020-01-01T05:53:00Z",
                "source": "licensed",
                "snapshot_kind": "pre_race",
            },
        ]
    )
    selected = select_point_in_time_win_odds(snapshots, _races())
    assert selected.set_index("horse_id").loc["H1", "odds"] == 3.8
    assert selected["odds_observed_at"].max() <= selected["odds_cutoff_at"].max()
    assert len(selected) == 2


def test_final_and_stale_quotes_fail_closed_for_whole_race() -> None:
    snapshots = pd.DataFrame(
        [
            {
                "race_id": "202001010101",
                "horse_id": "H1",
                "odds": 4.0,
                "observed_at": "2020-01-01T05:00:00Z",
                "source": "licensed",
                "snapshot_kind": "pre_race",
            },
            {
                "race_id": "202001010101",
                "horse_id": "H2",
                "odds": 8.0,
                "observed_at": "2020-01-01T05:50:00Z",
                "source": "licensed",
                "snapshot_kind": "final",
            },
        ]
    )
    selected = select_point_in_time_win_odds(
        snapshots,
        _races(),
        policy=PointInTimeOddsPolicy(max_age_minutes=30),
    )
    assert selected.empty


def test_market_probability_is_normalized_within_race() -> None:
    frame = pd.DataFrame(
        {"race_id": ["R1", "R1", "R2", "R2"], "odds": [2.0, 4.0, 3.0, 6.0]}
    )
    probabilities = normalized_market_probability(frame)
    totals = probabilities.groupby(frame["race_id"]).sum()
    assert totals.to_dict() == pytest.approx({"R1": 1.0, "R2": 1.0})


def test_rejects_missing_snapshot_timestamp() -> None:
    snapshots = pd.DataFrame(
        [
            {
                "race_id": "202001010101",
                "horse_id": "H1",
                "odds": 4.0,
                "observed_at": None,
                "source": "licensed",
                "snapshot_kind": "pre_race",
            }
        ]
    )
    with pytest.raises(ValueError, match="observed_at"):
        select_point_in_time_win_odds(snapshots, _races(expected=1))


def test_unknown_snapshot_kind_is_not_treated_as_pre_race() -> None:
    snapshots = pd.DataFrame(
        [
            {
                "race_id": "202001010101",
                "horse_id": "H1",
                "odds": 4.0,
                "observed_at": "2020-01-01T05:50:00Z",
                "source": "licensed",
                "snapshot_kind": "unknown",
            }
        ]
    )
    assert select_point_in_time_win_odds(snapshots, _races(expected=1)).empty


def test_quote_at_race_start_is_rejected_even_with_zero_offset() -> None:
    snapshots = pd.DataFrame(
        [
            {
                "race_id": "202001010101",
                "horse_id": "H1",
                "odds": 4.0,
                "observed_at": "2020-01-01T06:00:00Z",
                "source": "licensed",
                "snapshot_kind": "decision_time",
            }
        ]
    )
    selected = select_point_in_time_win_odds(
        snapshots,
        _races(expected=1),
        policy=PointInTimeOddsPolicy(decision_offset_minutes=0),
    )
    assert selected.empty
