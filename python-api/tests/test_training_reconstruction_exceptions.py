from pathlib import Path
import sys

import pandas as pd
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "scripts"))
from audit_training_reconstruction_exceptions import build_artifacts
from test_training_data_provenance import fixture


def run(values=None):
    training, races, runners, cohorts = fixture() if values is None else values
    return build_artifacts(training, races, runners, cohorts["released_clean"], "a" * 64)


def test_after_date_quarantined_without_invented_cause_or_fix():
    tables, counts = run()
    rows = tables["post_race_date_anomalies.csv"]
    assert len(rows) == 1 and rows.iloc[0].training_date == "2022/01/11"
    assert rows.iloc[0].days_after_race == 1 and rows.iloc[0].quarantine
    assert rows.iloc[0].cause == "UNKNOWN_WITHOUT_ORIGINAL_SOURCE"
    assert not rows.iloc[0].correction_applied and not rows.iloc[0].parser_error_verified
    assert not rows.iloc[0].source_url_verified_for_record and not rows.iloc[0].strict_pit
    assert counts["event_valid_identity_records"] == 2 and counts["strict_pit_records"] == 0


def test_missing_runner_is_not_assumed_cancelled_or_reassigned_by_name():
    values = fixture()
    values[0].loc[1, "horse_number"] = 99
    tables, counts = run(values)
    row = tables["unmatched_runner_records.csv"].iloc[0]
    assert row.runner_join_status == "name_candidate_number_conflict"
    assert row.number_match_rows == 0 and row.name_match_rows == 1
    assert row.result_row_absent_from_projection
    assert not row.cancellation_verified and not row.exclusion_verified and not row.renumbering_verified
    assert row.horse_number == 99 and not row.model_input_permitted
    assert counts["unmatched_runner_records"] == 1


def test_identity_and_event_are_both_needed_and_exceptions_count_union():
    values = fixture()
    values[0].loc[3, "horse_name"] = "different"
    tables, counts = run(values)
    assert counts["post_race_date_records"] == counts["unmatched_runner_records"] == 1
    assert counts["exception_union_records"] == counts["exception_overlap_records"] == 1
    assert len(tables["unmatched_runner_records.csv"]) == 1


def test_coverage_separates_presence_event_valid_and_strict():
    tables, _ = run()
    coverage = tables["yearly_coverage_after_reconstruction.csv"]
    row = coverage.loc[coverage.scope.eq("market_clean_2021_2022") & coverage.year.eq(2021)].iloc[0]
    assert row.denominator_runner_count == 2
    assert row.stored_presence_runner_coverage_percent == 100
    assert row.event_valid_identity_runner_coverage_percent == 50
    assert row.event_valid_identity_ge50pct_races == 1 and row.event_valid_identity_ge80pct_races == 0
    assert row.strict_pit_runner_coverage_percent == 0 and row.model_input_gate == "NO"
    row = coverage.loc[coverage.scope.eq("market_clean_2021_2022") & coverage.year.eq(2022)].iloc[0]
    assert row.event_valid_identity_runner_count == 0  # future and invalid event dates


def test_missing_saved_runner_rows_do_not_shrink_declared_denominator():
    values = fixture()
    values = (values[0], values[1], values[2].iloc[1:].copy(), values[3])
    tables, _ = run(values)
    row = tables["yearly_coverage_after_reconstruction.csv"].query("scope == 'all_canonical' and year == 2013").iloc[0]
    assert row.denominator_runner_count == 2 and row.known_runner_key_rows == 1
    assert row.event_valid_identity_runner_count == 0
    exception = tables["unmatched_runner_records.csv"].iloc[0]
    assert exception.header_count_exceeds_stored_unique_numbers


def test_unknown_denominator_is_reported_not_counted_complete():
    values = fixture()
    values[1].loc[0, "num_horses"] = 1  # contradicts two stored runner keys
    tables, _ = run(values)
    row = tables["yearly_coverage_after_reconstruction.csv"].query("scope == 'all_canonical' and year == 2013").iloc[0]
    assert row.denominator_unknown_races == 1 and row.denominator_runner_count == 0
    assert row.event_valid_identity_runner_count == 1
    assert pd.isna(row.event_valid_identity_runner_coverage_percent)
    assert row.event_valid_identity_100pct_races == 0


def test_no_mutation_no_roster_outcomes_and_record_hash_stable():
    values = fixture()
    original = [v.copy(deep=True) for v in values[:3]]
    first, _ = run(values)
    second, _ = run(values)
    for before, after in zip(original, values):
        pd.testing.assert_frame_equal(before, after)
    row = first["post_race_date_anomalies.csv"].iloc[0]
    assert row.source_record_sha256 == second["post_race_date_anomalies.csv"].iloc[0].source_record_sha256
    assert "finish" not in row.canonical_roster_identity_only_json
    assert "odds" not in row.canonical_roster_identity_only_json


@pytest.mark.parametrize("error", ["later_year", "duplicate_cohort", "duplicate_record"])
def test_invalid_source_contract_rejected(error):
    values = fixture()
    if error == "later_year": values[3]["released_clean"].loc[0, "race_date"] = "20230101"
    elif error == "duplicate_cohort":
        frame = values[3]["released_clean"]
        values[3]["released_clean"] = pd.concat([frame, frame.iloc[:1]], ignore_index=True)
    else: values[0].loc[1, "id"] = values[0].loc[0, "id"]
    with pytest.raises((ValueError, AssertionError)):
        run(values)
