import importlib.util
from pathlib import Path
import zipfile

import pytest

ROOT = Path(__file__).resolve().parents[2]
SPEC = importlib.util.spec_from_file_location("training_source_audit", ROOT / "scripts/audit_training_reconstruction_sources.py")
audit = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(audit)


@pytest.fixture(scope="module")
def parser():
    return audit.legacy_parser(audit.source_objects(ROOT)["python-api/scraping/oikiri.py"])


def test_pinned_source_only():
    with pytest.raises(ValueError, match="untrusted"):
        audit.legacy_parser(b"import os; os.remove('file')")


def test_header_changes_do_not_change_storage(parser):
    first = parser(audit.fixture(), "202101010101")[0]
    second = parser(audit.fixture(headers=("5F", "4F", "3F", "2F", "1F")), "202101010101")[0]
    assert first == second
    assert [first[x] for x in audit.TIME_FIELDS] == [101.1, 82.2, 66.3, 51.4, 12.5]


@pytest.mark.parametrize("course", ["栗坂", "美坂", "栗W", "CW", "ＣＷ", "美W", "美Ｗ", "南Ｗ", "函Ｗ", "美Ｐ", "ＤＰ", "南Ｄ"])
def test_course_has_no_mapping_branch(parser, course):
    row = parser(audit.fixture(course), "202101010101")[0]
    assert row["course"] == course
    assert [row[x] for x in audit.TIME_FIELDS] == [101.1, 82.2, 66.3, 51.4, 12.5]


def test_sixth_slot_is_ignored(parser):
    row = parser(audit.fixture(slots=("101", "82", "66", "51", "26", "12")), "202101010101")[0]
    assert row["time_1f"] == 26
    assert "time_2f" not in row


def test_placeholder_is_not_zero_and_wrapped_value_is_lost(parser):
    row = parser(audit.fixture(slots=("-", "<b>52.0</b>", "38.0", "25.0", "0")), "202101010101")[0]
    assert row["time_6f"] is None
    assert row["time_5f"] is None
    assert row["time_1f"] == 0.0
    assert row["lap_6f_5f"] == 12.3


def test_calendar_validation_and_target_cutoff_absent(parser):
    row = parser(audit.fixture(date="2026/02/31"), "202101010101")[0]
    assert row["training_date"] == "2026/02/31"


@pytest.mark.parametrize("grade", ["A", "B", "C", "D"])
def test_categories_and_forced_latest_flag(parser, grade):
    row = parser(audit.fixture(grade=grade), "202101010101")[0]
    assert row["is_last_training"] is True
    assert row["grade"] == grade
    assert row["pace"] == "馬也"
    assert row["comment"] == "synthetic comment"
    assert "synthetic critic" not in str(row)


def test_synthetic_tag_is_not_raw_proof(parser):
    cases = audit.reproduce(parser)
    assert len(cases) == 17
    assert all(x["evidence_type"] == "SYNTHETIC_NOT_PROVIDER_EVIDENCE" for x in cases)


def test_exclusive_write_preserves_existing(tmp_path):
    p = tmp_path / "old.json"
    p.write_bytes(b"old")
    with pytest.raises(FileExistsError):
        audit.exclusive(p, b"new")
    assert p.read_bytes() == b"old"


def test_zip_inventory_never_extracts_unsafe_members(tmp_path):
    p = tmp_path / "input.zip"
    with zipfile.ZipFile(p, "w") as z:
        z.writestr("../outside.html", "<table class='OikiriTable'>")
    result = audit.zip_inventory(p)
    assert result["members"][0]["safe_relative_name"] is False
    assert not (tmp_path.parent / "outside.html").exists()
    assert result["members_read"] == []
