from __future__ import annotations

import importlib
import json
import sqlite3
import sys
from pathlib import Path

import pandas as pd
import pytest


PYTHON_API = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PYTHON_API))
eligibility = importlib.import_module("training.eligibility")


def _race(
    race_id: str,
    *,
    month: str = "202501",
    num_horses: int = 3,
    finish: tuple[object, ...] = (1, 2, 3),
    times: tuple[object, ...] = (90.0, 90.5, 91.0),
    odds: tuple[object, ...] = (2.0, 3.0, 4.0),
    popularity: tuple[object, ...] = (1, 2, 3),
) -> list[dict[str, object]]:
    rows: list[dict[str, object]] = []
    for index in range(num_horses):
        rows.append(
            {
                "race_id": race_id,
                "race_date": f"{month}15",
                "horse_id": f"{race_id}-h{index + 1}",
                "horse_number": index + 1,
                "num_horses": num_horses,
                "distance": 1600,
                "venue": "東京",
                "finish": finish[index],
                "time_seconds": times[index],
                "odds": odds[index],
                "popularity": popularity[index],
            }
        )
    return rows


def test_selects_complete_target_rows_and_keeps_input_unchanged() -> None:
    rows = _race(
        "202501010101",
        finish=(1, 2, None),
        times=(90.0, 90.5, None),
        odds=(2.0, 3.0, None),
        popularity=(1, 2, None),
    )
    frame = pd.DataFrame(rows)
    original = frame.copy(deep=True)

    result = eligibility.select_training_eligible_rows(
        frame,
        target="speed_deviation",
        training_date_from="2025-01",
        training_date_to="2025-01",
        recorded_quality_states={
            "202501010101": ("settled", "complete", False)
        },
    )

    assert result.frame["horse_number"].tolist() == [1, 2]
    assert result.manifest.eligible_race_count == 1
    assert result.manifest.eligible_row_count == 2
    assert result.manifest.excluded_race_count == 0
    assert result.manifest.excluded_row_count == 1
    assert result.manifest.as_dict()["exclusion_reason_counts"]["target_missing"] == {
        "races": 1,
        "rows": 1,
    }
    pd.testing.assert_frame_equal(frame, original)


@pytest.mark.parametrize(
    ("mutation", "reason"),
    [
        (
            lambda rows: [row.update(race_id="invalid") for row in rows],
            "invalid_race_id",
        ),
        (
            lambda rows: [row.update(race_date=None) for row in rows],
            "race_date_invalid",
        ),
        (
            lambda rows: [row.update(race_date="2025-1-15") for row in rows],
            "race_date_invalid",
        ),
        (
            lambda rows: rows.__setitem__(1, {**rows[1], "race_date": "20250116"}),
            "race_date_inconsistent",
        ),
        (
            lambda rows: rows.__setitem__(
                1,
                {**rows[1], "horse_id": None, "horse_number": None},
            ),
            "runner_identity_missing",
        ),
        (lambda rows: rows.__setitem__(1, {**rows[1], "horse_id": rows[0]["horse_id"]}), "duplicate_runner"),
        (lambda rows: rows.__setitem__(0, {**rows[0], "num_horses": 5}), "horse_count_inconsistent"),
        (lambda rows: rows.__setitem__(1, {**rows[1], "distance": None}), "distance_invalid"),
        (lambda rows: rows.__setitem__(1, {**rows[1], "distance": 1800}), "distance_inconsistent"),
        (lambda rows: rows.__setitem__(1, {**rows[1], "venue": None}), "venue_missing"),
        (lambda rows: rows.__setitem__(1, {**rows[1], "venue": "中山"}), "venue_inconsistent"),
        (lambda rows: [row.update(finish=None) for row in rows], "result_not_settled"),
        (
            lambda rows: [row.update(odds=None) for row in rows],
            "odds_coverage_insufficient",
        ),
        (
            lambda rows: [row.update(popularity=None) for row in rows],
            "popularity_coverage_insufficient",
        ),
    ],
)
def test_race_level_quality_failures_reject_the_whole_race(
    mutation: object,
    reason: str,
) -> None:
    rows = _race(
        "202501010101",
        num_horses=4,
        finish=(1, 2, 3, 4),
        times=(90.0, 90.5, 91.0, 91.5),
        odds=(2.0, 3.0, 4.0, 5.0),
        popularity=(1, 2, 3, 4),
    )
    mutation(rows)

    result = eligibility.select_training_eligible_rows(
        pd.DataFrame(rows),
        target="speed_deviation",
        recorded_quality_states={
            "202501010101": ("settled", "complete", False)
        },
    )

    assert result.frame.empty
    assert result.manifest.excluded_race_count == 1
    assert result.manifest.as_dict()["exclusion_reason_counts"][reason] == {
        "races": 1,
        "rows": 4,
    }


def test_market_gate_rejects_at_exactly_eighty_percent_missing() -> None:
    rows = _race(
        "202501010101",
        num_horses=5,
        finish=(1, 2, 3, 4, 5),
        times=(90.0, 90.5, 91.0, 91.5, 92.0),
        odds=(2.0, None, None, None, None),
        popularity=(1, 2, 3, 4, 5),
    )

    result = eligibility.select_training_eligible_rows(
        pd.DataFrame(rows),
        target="speed_deviation",
    )

    assert result.frame.empty
    assert result.manifest.as_dict()["exclusion_reason_counts"][
        "odds_coverage_insufficient"
    ]["rows"] == 5


@pytest.mark.parametrize(
    ("column", "reason"),
    [
        ("race_date", "race_date_invalid"),
        ("venue", "venue_missing"),
        ("odds", "odds_coverage_insufficient"),
        ("popularity", "popularity_coverage_insufficient"),
    ],
)
def test_missing_required_race_column_rejects_all_rows(
    column: str,
    reason: str,
) -> None:
    frame = pd.DataFrame(_race("202501010101")).drop(columns=[column])

    result = eligibility.select_training_eligible_rows(
        frame,
        target="speed_deviation",
    )

    assert result.frame.empty
    assert result.manifest.as_dict()["exclusion_reason_counts"][reason] == {
        "races": 1,
        "rows": 3,
    }


def test_speed_target_requires_both_time_and_finish() -> None:
    rows = _race(
        "202501010101",
        finish=(1, 2, None),
        times=(90.0, 90.5, 91.0),
    )

    result = eligibility.select_training_eligible_rows(
        pd.DataFrame(rows),
        target="speed_deviation",
        recorded_quality_states={
            "202501010101": ("settled", "complete", False)
        },
    )

    assert result.frame["horse_number"].tolist() == [1, 2]
    assert result.manifest.as_dict()["exclusion_reason_counts"]["target_missing"] == {
        "races": 1,
        "rows": 1,
    }


def test_speed_target_does_not_accept_noncanonical_time_alias() -> None:
    frame = pd.DataFrame(_race("202501010101")).drop(columns=["time_seconds"])
    frame["finish_time_seconds"] = [90.0, 90.5, 91.0]

    result = eligibility.select_training_eligible_rows(
        frame,
        target="speed_deviation",
    )

    assert result.frame.empty
    assert result.manifest.as_dict()["exclusion_reason_counts"]["target_missing"] == {
        "races": 1,
        "rows": 3,
    }


def test_unledgered_partial_result_rejects_the_whole_race() -> None:
    rows = _race(
        "202501010101",
        finish=(1, None, None),
        times=(90.0, None, None),
    )

    result = eligibility.select_training_eligible_rows(
        pd.DataFrame(rows),
        target="win",
    )

    assert result.frame.empty
    assert result.manifest.as_dict()["exclusion_reason_counts"][
        "unverified_partial_result"
    ] == {"races": 1, "rows": 3}


def test_recorded_incomplete_quality_rejects_only_covered_race() -> None:
    complete_id = "202501010101"
    partial_id = "202501010102"
    frame = pd.DataFrame(_race(complete_id) + _race(partial_id))
    states = {
        complete_id: ("settled", "complete", False),
        partial_id: ("entry", "stored_partial", False),
    }

    result = eligibility.select_training_eligible_rows(
        frame,
        target="speed_deviation",
        recorded_quality_states=states,
    )
    manifest = result.manifest.as_dict()

    assert result.frame["race_id"].unique().tolist() == [complete_id]
    assert manifest["quality_ledger"] == {"covered_races": 2, "uncovered_races": 0}
    assert manifest["exclusion_reason_counts"]["recorded_quality_incomplete"] == {
        "races": 1,
        "rows": 3,
    }


def test_malformed_recorded_quality_state_fails_closed() -> None:
    race_id = "202501010101"

    result = eligibility.select_training_eligible_rows(
        pd.DataFrame(_race(race_id)),
        target="win",
        recorded_quality_states={race_id: "complete"},  # type: ignore[dict-item]
    )

    assert result.frame.empty
    assert result.manifest.as_dict()["exclusion_reason_counts"][
        "recorded_quality_incomplete"
    ] == {"races": 1, "rows": 3}


def test_finish_above_declared_field_size_rejects_the_race() -> None:
    rows = _race("202501010101", finish=(1, 2, 99))

    result = eligibility.select_training_eligible_rows(
        pd.DataFrame(rows),
        target="win",
        recorded_quality_states={
            "202501010101": ("settled", "complete", False)
        },
    )

    assert result.frame.empty
    assert result.manifest.as_dict()["exclusion_reason_counts"]["finish_invalid"] == {
        "races": 1,
        "rows": 3,
    }


def test_quality_ledger_loader_is_read_only_and_optional(tmp_path: Path) -> None:
    database = tmp_path / "quality.db"
    with sqlite3.connect(database) as connection:
        connection.execute(
            "CREATE TABLE scrape_race_acquisition_state ("
            "race_id TEXT PRIMARY KEY, lifecycle TEXT, quality_status TEXT, "
            "excluded_from_standard INTEGER)"
        )
        connection.execute(
            "INSERT INTO scrape_race_acquisition_state VALUES (?, ?, ?, ?)",
            ("202501010101", "settled", "complete", 0),
        )

    assert eligibility.load_recorded_quality_states(database) == {
        "202501010101": ("settled", "complete", False)
    }
    with sqlite3.connect(database) as connection:
        assert connection.execute(
            "SELECT COUNT(*) FROM scrape_race_acquisition_state"
        ).fetchone()[0] == 1


def test_manifest_is_json_serializable_order_invariant_and_reports_missing_months() -> None:
    rows = _race("202501010101", month="202501") + _race(
        "202503010101",
        month="202503",
    )
    frame = pd.DataFrame(rows)

    first = eligibility.select_training_eligible_rows(
        frame,
        target="win",
        training_date_from="202501",
        training_date_to="2025-03",
    ).manifest.as_dict()
    second = eligibility.select_training_eligible_rows(
        frame.iloc[::-1],
        target="win",
        training_date_from="2025-01",
        training_date_to="202503",
    ).manifest.as_dict()

    assert first == second
    assert first["months_with_data"] == ["2025-01", "2025-03"]
    assert first["missing_months"] == ["2025-02"]
    assert first["observed_eligible_date_range"] == {
        "from": "2025-01-15",
        "to": "2025-03-15",
    }
    assert len(first["eligibility_sha256"]) == 64
    assert json.loads(json.dumps(first, ensure_ascii=False)) == first


def test_rejects_unknown_target_and_invalid_period() -> None:
    frame = pd.DataFrame(_race("202501010101"))

    with pytest.raises(eligibility.TrainingEligibilityError, match="target-invalid"):
        eligibility.select_training_eligible_rows(frame, target="unknown")
    with pytest.raises(eligibility.TrainingEligibilityError, match="requested-period-invalid"):
        eligibility.select_training_eligible_rows(
            frame,
            target="win",
            training_date_from="2025-03",
            training_date_to="2025-01",
        )
