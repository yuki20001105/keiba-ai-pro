import sqlite3
from pathlib import Path

from scraping.quality import (
    classify_race_quality,
    evaluate_date_completeness,
    excluded_standard_dates,
    excluded_standard_race_ids,
    record_date_expectation,
    record_date_failure,
    record_verified_no_race_dates,
    record_race_failure,
    record_race_quality,
    run_date_repair_audit,
    summarize_acquisition_quality,
)


def _race(race_id: str, *, settled: bool = False, distance: int = 1600) -> dict:
    horses = []
    for number in (1, 2):
        horses.append(
            {
                "race_id": race_id,
                "horse_id": f"horse-{race_id}-{number}",
                "horse_name": f"Horse {number}",
                "horse_number": number,
                "finish_position": number if settled else None,
                "finish_time": f"1:3{number}.0" if settled else None,
                "odds": 2.0 + number if settled else None,
                "popularity": number if settled else None,
                "weight_kg": 470 + number if settled else None,
                "sire": "Sire",
                "dam": "Dam",
                "damsire": "Damsire",
            }
        )
    return {
        "race_info": {
            "race_id": race_id,
            "date": "20200104",
            "venue": "Tokyo",
            "distance": distance,
        },
        "horses": horses,
        "return_tables": [{"bet_type": "win", "payout": 300}] if settled else [],
    }


def test_publication_timed_fields_are_not_structural_errors() -> None:
    report = classify_race_quality(_race("202001010101"))

    assert report.lifecycle == "entry"
    assert report.valid_for_storage is True
    assert report.valid_for_date_completion is False
    assert report.field_states["odds"] == "not_published"
    assert report.field_states["horse_weight"] == "not_published"
    assert report.field_states["finish_position"] == "not_published"


def test_settled_race_is_complete_and_invalid_distance_is_rejected() -> None:
    settled = classify_race_quality(_race("202001010101", settled=True))
    invalid = classify_race_quality(_race("202001010102", settled=True, distance=0))

    assert settled.lifecycle == "settled"
    assert settled.valid_for_date_completion is True
    assert invalid.valid_for_storage is False
    assert "distance_invalid" in invalid.required_errors


def test_withdrawn_horse_missing_market_fields_is_domain_exception() -> None:
    data = _race("202001010104", settled=True)
    withdrawn = data["horses"][1]
    withdrawn["finish_position"] = "\u53d6"
    withdrawn["finish_time"] = None
    withdrawn["odds"] = None
    withdrawn["popularity"] = None
    withdrawn["weight_kg"] = None

    report = classify_race_quality(data)

    assert report.lifecycle == "settled"
    assert report.valid_for_date_completion is True
    assert report.field_states["finish_time"] == "available_with_domain_exceptions"
    assert report.field_states["odds"] == "available_with_domain_exceptions"
    assert report.field_states["popularity"] == "available_with_domain_exceptions"
    assert report.field_states["horse_weight"] == "available_with_domain_exceptions"


def test_active_finisher_without_time_is_repair_required() -> None:
    data = _race("202001010105", settled=True)
    data["horses"][0]["finish_time"] = None

    report = classify_race_quality(data)

    assert report.lifecycle == "settled"
    assert report.valid_for_date_completion is False
    assert report.field_states["finish_time"] == "source_missing"
    assert "finish_time_missing" in report.required_errors


def test_lifecycle_and_market_observations_are_kept_separate(tmp_path: Path) -> None:
    db_path = tmp_path / "ultimate.db"
    race_id = "202001010101"
    entry_data = _race(race_id)
    middle_data = _race(race_id)
    for number, horse in enumerate(middle_data["horses"], start=1):
        horse["odds"] = 2.0 + number
        horse["popularity"] = number
        horse["weight_kg"] = 470 + number
    settled_data = _race(race_id, settled=True)

    for data in (entry_data, middle_data, settled_data):
        report = classify_race_quality(data)
        record_race_quality(
            db_path,
            race_date="20200104",
            race_id=race_id,
            report=report,
            source_status="http_200",
            race_data=data,
        )

    with sqlite3.connect(str(db_path)) as conn:
        lifecycle = conn.execute(
            "SELECT lifecycle FROM scrape_race_acquisition_state WHERE race_id=?",
            (race_id,),
        ).fetchone()[0]
        observations = conn.execute(
            "SELECT lifecycle, market_payload_json FROM scrape_race_observations "
            "WHERE race_id=? ORDER BY id",
            (race_id,),
        ).fetchall()

    assert lifecycle == "settled"
    assert [row[0] for row in observations] == ["entry", "middle", "settled"]
    assert observations[0][1] is not None
    assert observations[1][1] is not None
    assert observations[2][1] is None


def test_date_completion_requires_every_expected_race(tmp_path: Path) -> None:
    db_path = tmp_path / "ultimate.db"
    expected = ["202001010101", "202001010102"]
    record_date_expectation(db_path, "20200104", expected)

    first = classify_race_quality(_race(expected[0], settled=True))
    record_race_quality(
        db_path,
        race_date="20200104",
        race_id=expected[0],
        report=first,
    )
    partial = evaluate_date_completeness(db_path, "20200104", expected)
    assert partial["status"] == "partial"
    assert partial["complete_race_count"] == 1
    assert partial["missing_race_ids"] == [expected[1]]

    second = classify_race_quality(_race(expected[1], settled=True))
    record_race_quality(
        db_path,
        race_date="20200104",
        race_id=expected[1],
        report=second,
    )
    complete = evaluate_date_completeness(db_path, "20200104", expected)
    assert complete["status"] == "complete"
    assert complete["expected_race_count"] == complete["complete_race_count"] == 2


def test_repeated_failure_is_quarantined_and_removed_from_standard_target(tmp_path: Path) -> None:
    db_path = tmp_path / "ultimate.db"
    race_id = "202001010103"

    first = record_race_failure(
        db_path,
        race_date="20200104",
        race_id=race_id,
        reason="html_parse_failed",
        source_status="http_200",
        max_attempts=2,
    )
    second = record_race_failure(
        db_path,
        race_date="20200104",
        race_id=race_id,
        reason="html_parse_failed",
        source_status="http_200",
        max_attempts=2,
    )

    assert first["status"] == "pending"
    assert second["status"] == "quarantined"
    assert second["excluded_from_standard"] is True
    assert excluded_standard_race_ids(db_path) == {race_id}


def test_repeated_race_list_failure_excludes_date_but_keeps_audit_row(tmp_path: Path) -> None:
    db_path = tmp_path / "ultimate.db"
    race_date = "20200104"

    record_date_failure(
        db_path,
        race_date=race_date,
        reason="html_parse_failed",
        source_status="http_200",
        max_attempts=2,
    )
    final = record_date_failure(
        db_path,
        race_date=race_date,
        reason="html_parse_failed",
        source_status="http_200",
        max_attempts=2,
    )

    assert final["status"] == "quarantined"
    assert final["excluded_from_standard"] is True
    assert excluded_standard_dates(db_path) == {race_date}
    with sqlite3.connect(str(db_path)) as conn:
        status = conn.execute(
            "SELECT status FROM scrape_date_completeness WHERE race_date=?",
            (race_date,),
        ).fetchone()[0]
    assert status == "excluded"


def test_successful_date_expectation_closes_prior_race_list_repair(tmp_path: Path) -> None:
    db_path = tmp_path / "ultimate.db"
    race_date = "20250105"
    record_date_failure(
        db_path,
        race_date=race_date,
        reason="race_list_http_400",
        source_status="http_400",
    )

    record_date_expectation(db_path, race_date, ["202506010101"])

    with sqlite3.connect(str(db_path)) as conn:
        repair = conn.execute(
            "SELECT status, last_error FROM scrape_repair_queue "
            "WHERE entity_type='date' AND entity_id=? AND repair_kind='race_list'",
            (race_date,),
        ).fetchone()
    assert repair == ("completed", None)


def test_verified_no_race_date_is_a_successful_checkpoint(tmp_path: Path) -> None:
    db_path = tmp_path / "ultimate.db"
    race_date = "20250106"
    record_date_failure(
        db_path,
        race_date=race_date,
        reason="race_list_unavailable",
        source_status="exception",
    )

    assert record_verified_no_race_dates(db_path, [race_date, race_date]) == 1

    with sqlite3.connect(str(db_path)) as conn:
        completeness = conn.execute(
            "SELECT expected_race_count, status FROM scrape_date_completeness "
            "WHERE race_date=?",
            (race_date,),
        ).fetchone()
        repair = conn.execute(
            "SELECT status, last_error FROM scrape_repair_queue "
            "WHERE entity_type='date' AND entity_id=? AND repair_kind='race_list'",
            (race_date,),
        ).fetchone()
    assert completeness == (0, "complete")
    assert repair == ("completed", None)


def test_post_job_audit_queues_only_missing_races(tmp_path: Path) -> None:
    db_path = tmp_path / "ultimate.db"
    expected = ["202001010101", "202001010102"]
    report = classify_race_quality(_race(expected[0], settled=True))
    record_race_quality(
        db_path,
        race_date="20200104",
        race_id=expected[0],
        report=report,
    )

    result = run_date_repair_audit(db_path, "20200104", expected)
    assert result["missing_race_ids"] == [expected[1]]
    with sqlite3.connect(str(db_path)) as conn:
        queued = conn.execute(
            "SELECT entity_id, repair_kind, status FROM scrape_repair_queue "
            "WHERE repair_kind='full_race'"
        ).fetchall()
    assert queued == [(expected[1], "full_race", "pending")]
    summary = summarize_acquisition_quality(db_path, ["20200104"])
    assert summary["quality_complete"] is False
    assert summary["incomplete_date_count"] == 1
    assert summary["missing_race_count"] == 1
