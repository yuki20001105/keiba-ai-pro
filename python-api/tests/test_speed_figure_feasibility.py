from pathlib import Path
import sys

import pandas as pd
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "scripts"))
from audit_speed_figure_feasibility import BASE, DERIVED, INDEX, dates, profile


def fixture():
    races = pd.DataFrame({"race_id": ["old", "new", "invalid"], "race_date": ["20210101", "20260101", "20210230"]})
    runners = pd.DataFrame({"race_id": ["old", "new", "invalid", "orphan"], "horse_number": [1, 1, 1, 1]})
    speed = pd.DataFrame({"race_id": ["old", "new"], "horse_number": [1, 1], "created_at": ["2026-09-20", "2026-09-20"],
                          **{k: [80.0, 90.0] for k in INDEX}})
    return races, runners, speed


def test_fixed_requested_feature_names():
    assert len(BASE) == len(DERIVED) == 7
    assert len(set(BASE + DERIVED)) == 14


def test_formula_missing_is_not_zero_or_coverage_success():
    annual, comparison, result = profile(*fixture())
    old = annual.set_index("year").loc[2021]
    assert old.rows == 1 and old.stored_external_records == 1
    assert old.reconstructed_rows == 0
    assert pd.isna(old.available) and pd.isna(old.missing) and pd.isna(old.invalid) and pd.isna(old.coverage_percent)
    assert not old.historical_external_values_used
    assert result["runner_race_join_missing"] == 1
    assert result["runner_date_unknown"] == 2
    assert result["stored_2026_rows"] == 1
    assert comparison.comparison_pairs.eq(0).all()
    assert comparison.MAE.isna().all() and comparison.exact_match_count.isna().all()


def test_created_date_does_not_change_historical_year():
    annual, _, _ = profile(*fixture())
    assert annual.set_index("year").loc[2021, "stored_external_records"] == 1


def test_duplicate_race_fail_closed():
    race, runner, speed = fixture()
    with pytest.raises(ValueError, match="ambiguous"):
        profile(pd.concat([race, race.iloc[:1]]), runner, speed)


def test_stored_index_invalid_and_missing_not_reconstructed():
    race, runner, speed = fixture()
    speed.loc[1, "index_last"] = float("inf")
    speed.loc[1, "index_2ago"] = None
    _, comparison, _ = profile(race, runner, speed)
    assert comparison.set_index("field").loc["index_last", "stored_invalid"] == 1
    assert comparison.set_index("field").loc["index_2ago", "stored_missing"] == 1
    assert comparison.comparison_pairs.eq(0).all()


def test_invalid_calendar_day_is_not_coerced_to_future():
    result = dates(pd.Series(["20210230", "bad", "20210228", "202611"]))
    assert result.isna().tolist() == [True, True, False, True]
