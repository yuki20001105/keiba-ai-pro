from __future__ import annotations

from copy import deepcopy
import importlib.util
from pathlib import Path
import sqlite3

import pytest

from scraping.pedigree_identity import fill_missing_parents, parse_parent_identities


HORSE = "2020000001"
URL = f"https://db.netkeiba.com/horse/ped/{HORSE}/"


def page():
    rows = []
    for index in range(32):
        if index == 0:
            cells = '<td rowspan="16"><a href="/horse/2010000001/">父</a></td>'
        elif index == 16:
            cells = ('<td rowspan="16"><a href="/horse/2010000002/">母</a></td>'
                     '<td rowspan="8"><a href="/horse/000a010a17/">Sevres Rose\u00a0(愛)</a>'
                     '<a href="/horse/ped/000a010a17/">血統</a></td>')
        else:
            cells = "<td>ancestor</td>"
        rows.append("<tr>" + cells + "</tr>")
    return (f'<html><head><link rel="canonical" href="https://db.netkeiba.com/horse/{HORSE}/">'
            f'<meta property="og:url" content="https://db.netkeiba.com//horse/{HORSE}/"></head>'
            '<table class="blood_table">' + "".join(rows) + "</table></html>")


def proof():
    return {"horse_id": HORSE, "source_url": URL,
            "parents": parse_parent_identities(page(), HORSE, URL),
            "raw_body_sha256": "a" * 64, "proof_sha256": "b" * 64,
            "source_observed_at_utc": "2026-10-25T07:00:00+00:00",
            "reconstructed_at_utc": "2026-10-25T07:01:00+00:00"}


def repair_runner():
    script = Path(__file__).resolve().parents[2] / "scripts" / "repair_pedigree_identities.py"
    spec = importlib.util.spec_from_file_location("parent_identity_repair_fixture", script)
    runner = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(runner)
    return runner


def test_parent_links_establish_foreign_identity_without_stripping_country_label():
    parsed = parse_parent_identities(page(), HORSE, URL)
    assert parsed["damsire"] == {"horse_id": "000a010a17", "name": "Sevres Rose\u00a0(愛)"}
    assert parsed["sire"]["horse_id"] == "2010000001"


@pytest.mark.parametrize("old,new,reason", [
    (f"/horse/{HORSE}/", "/horse/2099999999/", "child-identity-conflict"),
    ('rowspan="16"', 'rowspan="15"', "parent-position-unconfirmed"),
    ("/horse/000a010a17/", "/horse/ped/000a010a17/", "parent-link-not-unique"),
])
def test_mismatched_source_identity_or_layout_is_rejected(old, new, reason):
    with pytest.raises(ValueError, match=reason):
        parse_parent_identities(page().replace(old, new), HORSE, URL)


def test_empty_source_body_is_rejected():
    with pytest.raises(ValueError, match="body-empty"):
        parse_parent_identities("", HORSE, URL)


@pytest.mark.parametrize("different_id", [False, True])
def test_archive_compares_parent_ids_and_preserves_source_labels(tmp_path, different_id):
    archive = tmp_path / "archive.db"
    mobile_url = URL.replace("db.netkeiba.com", "db.sp.netkeiba.com")
    mobile = page().replace("Sevres Rose\u00a0(愛)", "Sevres Rose")
    if different_id:
        mobile = mobile.replace("/horse/000a010a17/", "/horse/000a099999/")
    with sqlite3.connect(archive) as db:
        db.execute("CREATE TABLE sources(url TEXT, final_url TEXT, status INTEGER, body BLOB, fetched_at REAL)")
        db.executemany("INSERT INTO sources VALUES(?,?,?,?,?)", [
            (URL, URL, 200, page().encode(), 1000),
            (mobile_url, mobile_url, 200, mobile.encode(), 1001),
        ])
    proofs, rejected = repair_runner().parse_archive(archive)
    if different_id:
        assert proofs == {}
        assert rejected[0]["reason"] == "pedigree-source-parent-id-conflict-between-endpoints"
    else:
        assert rejected == []
        assert proofs[HORSE]["parents"]["damsire"]["name"] == "Sevres Rose\u00a0(愛)"


def test_fill_empty_row_preserves_results_and_attaches_source_ids():
    item = {"horse_id": HORSE, "finish_position": 2, "odds": 0.0, "other": {"keep": True}}
    original = deepcopy(item)
    updated, fields = fill_missing_parents(item, proof())
    assert item == original
    assert set(fields) == {"sire", "dam", "damsire"}
    assert updated["damsire"] == "Sevres Rose\u00a0(愛)"
    assert updated["damsire_id"] == "000a010a17"
    assert all(updated[key] == value for key, value in item.items())
    assert updated["_pedigree_identity"]["historical_capture_time_verified"] is False


@pytest.mark.parametrize("known", [
    {"sire": "別の父"}, {"sire_name": "別の父"}, {"damsire_id": "000a099999"},
])
def test_partial_row_conflicts_are_held_not_mixed(known):
    with pytest.raises(ValueError, match="conflict"):
        fill_missing_parents({"horse_id": HORSE, **known}, proof())


def test_complete_rows_are_untouched_and_legacy_guard_is_not_relaxed():
    from scraping.saved_horse_reuse import _name

    item = {"horse_id": HORSE, **{field: value["name"] for field, value in proof()["parents"].items()}}
    updated, fields = fill_missing_parents(item, proof())
    assert updated == item and fields == []
    assert _name("Sevres Rose\u00a0(愛)") is None


def test_research_writer_and_verifier_preserve_exact_nonparent_values(tmp_path):
    from test_pedigree_gap_repair import stores, add

    # Use the existing source-revision/journal fixture without opening live DBs.
    generator = stores.__wrapped__(tmp_path)
    add(generator, horse_id=HORSE)
    source = generator[0]
    target = tmp_path / "research.db"
    runner = repair_runner()
    result = runner.execute(source, target, {HORSE: proof()}, {HORSE}, "fixture")
    assert result["counts"]["updated_rows"] == 1
    assert runner.verify_rows(source, target, {HORSE: proof()}, "fixture") == 1
