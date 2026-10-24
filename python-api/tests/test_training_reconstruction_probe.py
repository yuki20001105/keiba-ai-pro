"""No HTTP in tests. Source accessibility is not historical publication proof."""
from pathlib import Path
import sqlite3
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "scripts"))
from probe_training_reconstruction import inspect_body, select_probes, validate_samples, retry_after, YEARS
import pytest


def test_numeric_unlabelled_slots_do_not_gain_distance_or_pit():
    raw = b'<table class="OikiriTable"><tr id="tr_1" class="HorseList"></tr><tr class="HorseList"><td class="Training_Day">2021/01/01</td><td>CW</td><td class="TrainingTimeData"><ul class="TrainingTimeDataList"><li>52.0</li></ul></td></tr></table>'
    result = inspect_body(raw, "202101010101")
    assert result["content_status"] == "DATA_PRESENT"
    assert result["distance_mapping_verified"] is False
    assert result["historical_backfill"] is True
    assert result["pit_class"] == "INVALID_OR_UNKNOWN"


def test_missing_table_does_not_mean_historical_data_absent():
    result = inspect_body('<html>ログイン プレミアム会員</html>'.encode("euc-jp"), "x")
    assert result["membership_link_or_text_present"]
    assert result["content_status"] == "NO_OBSERVABLE_WORKOUT_VALUES"


def test_generic_login_link_does_not_override_observable_data():
    raw = '<a>ログイン</a><table class="OikiriTable"><tr><td class="Training_Day">2020/01/01</td><td><ul class="TrainingTimeDataList"><li>13.2</li></ul></td></tr></table>'
    assert inspect_body(raw.encode("euc-jp"), "x")["content_status"] == "DATA_PRESENT"


def test_challenge_is_explicit():
    assert inspect_body(b'<title>Just a moment...</title>', "x")["content_status"] == "ACCESS_CHALLENGE"


@pytest.mark.parametrize("message",["アクセス制限","アクセスが制限されています","verify you are human"])
def test_additional_access_block_markers(message):
    assert inspect_body(message.encode("euc-jp"),"x")["challenge"] is True


def test_retry_after_never_shortens_provider_cooldown():
    assert retry_after({"Retry-After":"3600"}) == 3600
    assert retry_after({"Retry-After":"invalid"}) == 0


def test_observable_strings_never_approve_source_event_time():
    result=inspect_body(b'<table class="OikiriTable"><td class="Training_Day">2027/99/99</td><ul class="TrainingTimeDataList"><li>13.2</li></ul></table>',"x")
    assert result["pit_class"] == "INVALID_OR_UNKNOWN"


def test_an_empty_table_is_not_data():
    assert inspect_body(b'<table class="OikiriTable"></table>', "x")["content_status"] == "NO_OBSERVABLE_WORKOUT_VALUES"


def test_selection_is_outcome_free_and_excludes_saved_workouts():
    db = sqlite3.connect(":memory:")
    db.execute("CREATE TABLE races_ultimate(race_id,race_date,num_horses)")
    db.execute("CREATE TABLE training_data(race_id)")
    for year in YEARS:
        for n in range(1, 13):
            db.execute("INSERT INTO races_ultimate VALUES(?,?,?)",(f"{year}050101{n:02}",f"{year}01{n:02}",10))
        db.execute("INSERT INTO training_data VALUES(?)",(f"{year}05010101",))
    rows, counts = select_probes(db)
    assert len(rows) == 18
    assert set(counts.values()) == {11}
    assert all(not r["race_id"].endswith("01") for r in rows)
    assert len({r["race_id"] for r in rows}) == 18
    plan = {"probe_races":rows,"uncollected_JRA_races_by_year":counts}
    validate_samples(plan,rows,counts)
    with pytest.raises(ValueError):
        validate_samples({**plan,"probe_races": rows*2},rows,counts)
    with pytest.raises(ValueError):
        validate_samples({"probe_races":rows[:1],"uncollected_JRA_races_by_year":counts},rows[:1],counts)
