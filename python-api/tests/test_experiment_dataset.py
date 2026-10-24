from __future__ import annotations

import hashlib
import json
import sqlite3

import pandas as pd
import pytest

from experiments.dataset import build_dataset
from experiments.features import ALLOWED_FEATURES


def race(rid="201605010101", day="2016-01-10", **changes):
    return dict(race_id=rid, date=day, venue="東京", distance=1600, track_type="芝",
                num_horses=2, race_class="未勝利", **changes)


def runners(rid="201605010101"):
    return [dict(race_id=rid, horse_id=f"H{number}", horse_number=number, finish=number,
                 time_seconds=100 + number, odds=number + 1, popularity=number,
                 jockey_id=f"J{number}", trainer_id="T", sire="S", damsire="D",
                 prev_race_finish=999, horse_total_runs=999, speed_deviation=999,
                 safe_prev_finish=999, surprise_numeric=999)
            for number in (1, 2)]


def database(path, extra=None, quality=None, payout=None, sidecar=False):
    with sqlite3.connect(path) as connection:
        connection.executescript("""
            CREATE TABLE races_ultimate (race_id TEXT PRIMARY KEY, data TEXT);
            CREATE TABLE race_results_ultimate (id INTEGER PRIMARY KEY, race_id TEXT, data TEXT);
            CREATE TABLE scrape_race_acquisition_state
              (race_id TEXT PRIMARY KEY, lifecycle TEXT, quality_status TEXT, excluded_from_standard INTEGER);
            CREATE TABLE return_tables_ultimate (race_id TEXT, bet_type TEXT, combinations TEXT, payout REAL);
        """)
        history_id = "201505010101"
        races = [(race(history_id, "20151201"), runners(history_id)), (race(), runners())]
        races += extra or []
        for meta, rows in races:
            connection.execute("INSERT INTO races_ultimate VALUES (?,?)", (meta["race_id"], json.dumps(meta)))
            for row in rows:
                connection.execute("INSERT INTO race_results_ultimate (race_id,data) VALUES (?,?)",
                                   (meta["race_id"], json.dumps(row)))
        for values in quality or []:
            connection.execute("INSERT INTO scrape_race_acquisition_state VALUES (?,?,?,?)", values)
        for values in payout or [("201605010101", "単勝", "1", 230)]:
            connection.execute("INSERT INTO return_tables_ultimate VALUES (?,?,?,?)", values)
        if sidecar:
            connection.execute("CREATE TABLE licensed_history_entries (id INTEGER)")
            connection.execute("INSERT INTO licensed_history_entries VALUES (1)")
    return path


def test_build_is_readonly_scoped_and_uses_official_payouts_only(tmp_path):
    source = database(tmp_path / "snapshot.db")
    original = source.read_bytes()
    output = tmp_path / "dataset"
    manifest = build_dataset(source, output)
    data = pd.read_parquet(output / "dataset.parquet")
    assert source.read_bytes() == original
    assert {path.name for path in output.iterdir()} == {"dataset.parquet", "dataset_manifest.json"}
    assert manifest == json.loads((output / "dataset_manifest.json").read_text(encoding="utf-8"))
    assert manifest["snapshot_sha256"] == hashlib.sha256(original).hexdigest()
    assert manifest["dataset_sha256"] == hashlib.sha256((output / "dataset.parquet").read_bytes()).hexdigest()
    assert manifest["row_count"] == 2 and manifest["race_count"] == 1
    assert manifest["history_row_count"] == 4 and manifest["history_race_count"] == 2
    assert data.race_date.eq("20160110").all()
    assert data.safe_prior_starts.eq(1).all()
    assert data.safe_prev_finish.tolist() == [1, 2]
    assert data.num_horses.eq(2).all()
    assert data.loc[data.finish.eq(1), "tansho_payout"].tolist() == [230]
    assert data.loc[data.finish.eq(2), "tansho_payout"].isna().all()
    assert manifest["payout_scope"] == "winner_only"
    assert set(manifest["feature_sets"]["M03"]) == ALLOWED_FEATURES
    assert not {"prev_race_finish", "horse_total_runs", "speed_deviation", "surprise_numeric"} & set(data)
    assert not {"odds", "popularity", "finish", "tansho_payout"} & ALLOWED_FEATURES


@pytest.mark.parametrize("field,value,reason", [
    ("track_type", "障害", "jump_evidence_quarantined"),
    ("track_type", "unknown", "not_confirmed_flat_turf_dirt"),
    ("surface", "dirt", "not_confirmed_flat_turf_dirt"),
    ("race_type", "steeplechase", "jump_evidence_quarantined"),
    ("venue", "", "venue_identity_unconfirmed"),
    ("venue", "中山", "venue_identity_unconfirmed"),
    ("num_horses", None, "required_race_metadata_invalid"),
    ("date", "20160231", "race_date_invalid_or_inconsistent"),
    ("race_date", "20160111", "race_date_invalid_or_inconsistent"),
])
def test_scope_and_required_metadata_fail_closed(tmp_path, field, value, reason):
    rid = "201605010102"
    meta = race(rid)
    meta[field] = value
    source = database(tmp_path / "snapshot.db", [(meta, runners(rid))])
    manifest = build_dataset(source, tmp_path / "dataset")
    assert manifest["row_count"] == 2
    assert manifest["source_audit"]["scope_excluded_races"][reason] == 1


def test_nar_and_pre_2013_are_excluded(tmp_path):
    nar, old = "201635010101", "201205010101"
    source = database(tmp_path / "snapshot.db", [
        (race(nar, "20160110"), runners(nar)), (race(old, "20120101"), runners(old))])
    manifest = build_dataset(source, tmp_path / "dataset")
    assert manifest["source_audit"]["scope_excluded_races"]["outside_jra"] == 1
    assert manifest["source_audit"]["scope_excluded_races"]["before_history_start"] == 1
    assert manifest["row_count"] == 2


@pytest.mark.parametrize("field,text", [
    ("race_name", "第18回京都ハイジャンプ(JGII)"),
    ("race_name", "春麗ジャンプS(OP)"), ("race_name", "イルミネーションJS(OP)"),
    ("race_class", "JGIII"), ("race_grade", "J・GⅢ"), ("grade", "J.G.1"),
    ("course_type", "障害"), ("course_info", "steeplechase"),
])
def test_explicit_jumping_evidence_rejected_even_when_surface_says_turf(tmp_path, field, text):
    rid = "201605010102"
    meta = race(rid)
    meta[field] = text
    meta["distance"] = 3200  # overlaps legitimate flat turf races: name evidence is necessary.
    source = database(tmp_path / "snapshot.db", [(meta, runners(rid))])
    manifest = build_dataset(source, tmp_path / "dataset")
    assert manifest["row_count"] == 2
    assert manifest["source_audit"]["scope_excluded_races"]["jump_evidence_quarantined"] == 1


@pytest.mark.parametrize("surface,distance", [("芝", 3930), ("芝", 4250), ("芝", 3110),
                                             ("芝", 3140), ("芝", 3350), ("芝", 3570),
                                             ("ダート", 3100), ("ダート", 3200), ("芝", 999)])
def test_unconfirmed_course_distance_quarantined_not_assumed_flat(tmp_path, surface, distance):
    rid = "201605010102"
    meta = race(rid)
    meta.update(track_type=surface, distance=distance)
    source = database(tmp_path / "snapshot.db", [(meta, runners(rid))])
    manifest = build_dataset(source, tmp_path / "dataset")
    assert manifest["row_count"] == 2
    assert manifest["source_audit"]["scope_excluded_races"]["outside_conservative_flat_distance_contract"] == 1
    assert "not exhaustive official" in manifest["scope"]["domain_policy"]


def test_quarantined_jumping_history_never_enters_flat_prediction_features(tmp_path):
    rid = "201505010201"
    meta = race(rid, "20151220")
    meta.update(race_name="イルミネーションJS(OP)", distance=3200)
    rows = runners(rid)
    for item in rows:
        item.update(time_seconds=250, last_3f_time=13)
    source = database(tmp_path / "snapshot.db", [(meta, rows)])
    manifest = build_dataset(source, tmp_path / "dataset")
    data = pd.read_parquet(tmp_path / "dataset/dataset.parquet")
    assert manifest["history_row_count"] == 4
    assert data.safe_prior_starts.eq(1).all()
    assert data.safe_prev_distance.eq(1600).all()
    assert data.safe_past5_avg_last3f_time.isna().all()


@pytest.mark.parametrize("surface,distance", [("芝", 3200), ("芝", 3400), ("芝", 3600), ("ダート", 2500)])
def test_ordinary_long_flat_race_within_explicit_contract_is_retained(tmp_path, surface, distance):
    rid = "201605010102"
    meta = race(rid)
    meta.update(race_name="天皇賞(GI)", track_type=surface, distance=distance)
    source = database(tmp_path / "snapshot.db", [(meta, runners(rid))])
    manifest = build_dataset(source, tmp_path / "dataset")
    assert manifest["row_count"] == 4


@pytest.mark.parametrize("field,value", [("time_seconds", None), ("finish", None)])
def test_partial_targets_remove_whole_race_not_only_unfavourable_runner(tmp_path, field, value):
    rid = "201605010102"
    rows = runners(rid)
    rows[1][field] = value
    source = database(tmp_path / "snapshot.db", [(race(rid), rows)],
                      [(rid, "settled", "complete", 0)])
    manifest = build_dataset(source, tmp_path / "dataset")
    data = pd.read_parquet(tmp_path / "dataset/dataset.parquet")
    assert rid not in set(data.race_id)
    assert manifest["exclusion_reason_counts"]["complete_runner_target_cohort"] == {"races": 1, "rows": 2}
    assert "selected-cohort" in manifest["cohort_policy"]


@pytest.mark.parametrize("field,value", [("race_date", "20160111"), ("distance", 1800),
                                         ("race_id", "201605010103"), ("venue", "中山")])
def test_one_conflicting_runner_rejects_whole_race(tmp_path, field, value):
    rid = "201605010102"
    rows = runners(rid)
    rows[1][field] = value
    source = database(tmp_path / "snapshot.db", [(race(rid), rows)])
    manifest = build_dataset(source, tmp_path / "dataset")
    assert manifest["row_count"] == 2
    assert sum(item["races"] for item in manifest["source_audit"]["integrity_exclusions"].values()) == 1


def test_optional_missing_features_remain_missing_not_required_for_acceptance(tmp_path):
    rid = "201605010102"
    rows = runners(rid)
    for row in rows:
        for key in ("jockey_id", "trainer_id", "sire", "damsire"):
            row.pop(key)
    source = database(tmp_path / "snapshot.db", [(race(rid), rows)])
    manifest = build_dataset(source, tmp_path / "dataset")
    data = pd.read_parquet(tmp_path / "dataset/dataset.parquet")
    assert manifest["row_count"] == 4
    assert data.loc[data.race_id.eq(rid), "safe_jockey_starts"].isna().all()
    assert data.safe_horse_weight.isna().all()


@pytest.mark.parametrize("number", [None, 0, -1, 1.5, 19, "unknown"])
def test_missing_or_invalid_horse_number_rejects_race_before_evaluation(tmp_path, number):
    rid = "201605010102"
    rows = runners(rid)
    rows[0]["horse_number"] = number
    source = database(tmp_path / "snapshot.db", [(race(rid), rows)])
    manifest = build_dataset(source, tmp_path / "dataset")
    assert manifest["row_count"] == 2
    assert manifest["source_audit"]["integrity_exclusions"]["runner_number_invalid"] == {"races": 1, "rows": 2}


def test_missing_horse_id_is_not_replaced_with_shared_placeholder_history(tmp_path):
    rid = "201605010102"
    rows = runners(rid)
    rows[0]["horse_id"], rows[1]["horse_id"] = "unknown", "-"
    source = database(tmp_path / "snapshot.db", [(race(rid), rows)])
    manifest = build_dataset(source, tmp_path / "dataset")
    data = pd.read_parquet(tmp_path / "dataset/dataset.parquet")
    missing_id = data.loc[data.race_id.eq(rid)]
    assert manifest["row_count"] == 4
    assert missing_id.horse_id.eq("").all()
    assert missing_id.safe_prior_starts.eq(0).all()
    assert missing_id.safe_prev_finish.isna().all()


def test_incomplete_quality_or_headcount_excluded_by_existing_policy(tmp_path):
    rid1, rid2 = "201605010102", "201605010103"
    source = database(tmp_path / "snapshot.db", [(race(rid1), runners(rid1)),
                      (race(rid2), runners(rid2)[:1])], [(rid1, "entry", "partial", 0)])
    manifest = build_dataset(source, tmp_path / "dataset")
    assert manifest["row_count"] == 2
    assert manifest["exclusion_reason_counts"]["recorded_quality_incomplete"]["races"] == 1
    assert manifest["exclusion_reason_counts"]["horse_count_inconsistent"]["races"] == 1


def test_conflicting_payout_is_unknown_never_substituted_with_odds(tmp_path):
    rid = "201605010101"
    source = database(tmp_path / "snapshot.db", payout=[(rid, "単勝", "1", 230), (rid, "単勝", "1", 240)])
    manifest = build_dataset(source, tmp_path / "dataset")
    data = pd.read_parquet(tmp_path / "dataset/dataset.parquet")
    assert data.tansho_payout.isna().all()
    assert manifest["source_audit"]["conflicting_win_payout_entries"] == 1


def test_snapshot_and_output_guards(tmp_path):
    source = database(tmp_path / "snapshot.db")
    output = tmp_path / "dataset"
    first = build_dataset(source, output)
    second = build_dataset(source, tmp_path / "second")
    assert first["dataset_sha256"] == second["dataset_sha256"]
    assert first["eligibility_sha256"] == second["eligibility_sha256"]
    with pytest.raises(FileExistsError, match="already-exists"):
        build_dataset(source, output)


def test_pending_wal_snapshot_is_rejected(tmp_path):
    source = database(tmp_path / "snapshot.db")
    source.with_name(source.name + "-wal").write_bytes(b"uncheckpointed")
    with pytest.raises(ValueError, match="pending-wal"):
        build_dataset(source, tmp_path / "dataset")


def test_nonempty_provider_sidecar_is_not_silently_shadowed(tmp_path):
    source = database(tmp_path / "snapshot.db", sidecar=True)
    with pytest.raises(ValueError, match="sidecar-source-not-supported"):
        build_dataset(source, tmp_path / "dataset")
