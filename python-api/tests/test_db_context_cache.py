"""Context cache audit with tiny synthetic SQLite sources, never a live DB."""
from __future__ import annotations

import json
import sqlite3

import pandas as pd
import pytest

from training import db_context_cache as cache
from training import db_context_features as features
from training import db_context_fit as fit
from test_db_context_features import fixture, write_db


@pytest.fixture
def prepared(tmp_path):
    db = write_db(tmp_path / "source.db", fixture())
    root = tmp_path / "cache"
    def prepare(*, date_from="2024-01", date_to="2025-01", reuse_features=True, expected_source_hashes=None):
        return cache.prepare_context_inputs(db, date_from, date_to, root,
            reuse_features=reuse_features, expected_source_hashes=expected_source_hashes)
    return db, root, prepare


def pointer_paths(root, receipt):
    entry = root / "context-v1" / receipt["cache_key_sha256"]
    pointer_path = entry / "current.json"
    pointer = json.loads(pointer_path.read_text(encoding="utf-8"))
    directory = entry / pointer["generation"]
    return pointer_path, pointer, directory


def update_manifest(pointer_path, pointer, directory, manifest):
    path = directory / "manifest.json"
    path.write_text(json.dumps(manifest, ensure_ascii=False, allow_nan=False), encoding="utf-8")
    pointer["manifest_sha256"] = fit.file_hash(path)
    pointer_path.write_text(json.dumps(pointer), encoding="utf-8")


def update_frame(pointer_path, pointer, directory, name, frame):
    path = directory / (name + ".parquet")
    frame.to_parquet(path, index=False)
    manifest = json.loads((directory / "manifest.json").read_text(encoding="utf-8"))
    manifest["files"][name].update(sha256=fit.file_hash(path),
        frame_digest=fit.legacy._frame_digest(frame), rows=len(frame), columns=list(frame.columns))
    update_manifest(pointer_path, pointer, directory, manifest)


def test_miss_hit_reuse_keeps_exact80_point_in_time_values_and_raw_db(prepared, monkeypatch):
    db, root, prepare = prepared
    before = fit.file_hash(db)
    target, matrix, coverage, first = prepare()
    assert first["cache_status"] == "miss" and first["cache_hit"] is False
    assert list(matrix) == list(features.FEATURE_COLUMNS)
    assert len(target) == len(matrix) == 12
    assert first["snapshot_sha256"] == before
    assert first["point_in_time_validated"] is True
    assert coverage["source_hashes"] == features.source_hashes()
    monkeypatch.setattr(features, "load_db_frames", lambda *_: pytest.fail("hit must not scan source again"))
    target2, matrix2, coverage2, second = prepare()
    assert second["cache_status"] == "hit" and second["cache_hit"] is True
    pd.testing.assert_frame_equal(target, target2, check_exact=True)
    pd.testing.assert_frame_equal(matrix, matrix2, check_exact=True)
    assert coverage == coverage2 and fit.file_hash(db) == before
    assert list(root.iterdir()) == [root / "context-v1"]
    assert second["calibrator_reused"] is False
    assert second["train_encoder_reused"] is False
    assert second["fitted_baseline_reused"] is False


def test_cache_opt_out_publishes_new_generation_without_deleting_previous(prepared):
    _db, root, prepare = prepared
    _, _, _, first = prepare()
    _, pointer, old = pointer_paths(root, first)
    _, _, _, second = prepare(reuse_features=False)
    _, newer, new = pointer_paths(root, second)
    assert second["cache_status"] == "refresh"
    assert second["cache_hit"] is False
    assert pointer["generation"] != newer["generation"]
    assert old.is_dir() and new.is_dir()


@pytest.mark.parametrize("problem", ["invalid_json", "null", "array", "scalar", "wrong_generation", "wrong_manifest_hash"])
def test_corrupt_pointer_rebuilds_safely_without_removing_original_data(prepared, problem):
    db, root, prepare = prepared
    _, _, _, first = prepare()
    path, pointer, old = pointer_paths(root, first)
    content = {"invalid_json": "{", "null": "null", "array": "[]", "scalar": "1"}.get(problem)
    if content is None:
        pointer["generation" if problem == "wrong_generation" else "manifest_sha256"] = "../../bad" if problem == "wrong_generation" else "0" * 64
        content = json.dumps(pointer)
    path.write_text(content, encoding="utf-8")
    source_hash = fit.file_hash(db)
    _, _, _, rebuilt = prepare()
    assert rebuilt["cache_status"] == "rebuilt"
    assert rebuilt["cache_hit"] is False and rebuilt["rejected_cache_reason"]
    assert old.is_dir() and fit.file_hash(db) == source_hash


def test_frame_bytes_corruption_is_rebuilt_not_deserialized_as_a_model(prepared):
    _, root, prepare = prepared
    _, _, _, first = prepare()
    _, _, old = pointer_paths(root, first)
    (old / "features.parquet").write_bytes(b"broken parquet bytes")
    target, matrix, _, rebuilt = prepare()
    assert rebuilt["cache_status"] == "rebuilt" and rebuilt["cache_hit"] is False
    assert len(target) == len(matrix)
    assert old.is_dir()


@pytest.mark.parametrize("problem", ["bad_feature_order", "target_outside_period", "bad_source_audit", "bad_point_in_time_audit", "bad_coverage_count"])
def test_self_consistent_receipt_cannot_override_schema_cohort_or_pit_contract(prepared, problem):
    _, root, prepare = prepared
    _, _, _, first = prepare()
    pointer_path, pointer, directory = pointer_paths(root, first)
    manifest = json.loads((directory / "manifest.json").read_text(encoding="utf-8"))
    if problem == "bad_feature_order":
        frame = pd.read_parquet(directory / "features.parquet")
        update_frame(pointer_path, pointer, directory, "features", frame[list(reversed(frame.columns))])
    elif problem == "target_outside_period":
        target = pd.read_parquet(directory / "target.parquet")
        selected = target.race_id.eq(target.race_id.iloc[0])
        target.loc[selected, "race_date"] = "20140101"
        update_frame(pointer_path, pointer, directory, "target", target)
    else:
        if problem == "bad_source_audit":
            manifest["feature_audit"]["source_hashes"] = {"unknown": "0" * 64}
        elif problem == "bad_point_in_time_audit":
            manifest["feature_audit"]["point_in_time_audit"]["future_leakage_rows"] = 1
        else:
            manifest["coverage"]["target"]["eligible_rows"] = 1000000
        update_manifest(pointer_path, pointer, directory, manifest)
    _, matrix, _, receipt = prepare()
    assert receipt["cache_status"] == "rebuilt" and receipt["cache_hit"] is False
    assert receipt["point_in_time_validated"] is True
    assert list(matrix) == list(features.FEATURE_COLUMNS)


def test_period_and_raw_source_revision_get_separate_keys(prepared):
    db, root, prepare = prepared
    _, _, _, first = prepare()
    narrower, _, _, second = prepare(date_from="2025-01", date_to="2025-01")
    assert second["cache_status"] == "miss" and len(narrower) == 4
    assert second["cache_key_sha256"] != first["cache_key_sha256"]
    with sqlite3.connect(db) as connection:
        connection.execute("UPDATE races_ultimate SET data=json_set(data,'$.weather','曇')")
    _, _, _, changed = prepare()
    assert changed["cache_status"] == "miss"
    assert changed["snapshot_sha256"] != first["snapshot_sha256"]
    assert changed["cache_key_sha256"] != first["cache_key_sha256"]
    assert pointer_paths(root, first)[2].is_dir()


def test_bound_expected_sources_and_immutable_snapshot_are_required(prepared):
    db, _root, prepare = prepared
    with pytest.raises(ValueError, match="expected-source-mismatch"):
        prepare(expected_source_hashes={"other": "0" * 64})
    with pytest.raises(ValueError, match="reuse-boolean"):
        prepare(reuse_features=1)
    with pytest.raises(ValueError, match="period-invalid"):
        prepare(date_from="2025-01", date_to="2024-01")
    db.with_name(db.name + "-wal").write_bytes(b"noncheckpointed")
    with pytest.raises(ValueError, match="checkpointed-snapshot"):
        prepare()


def test_snapshot_that_gains_a_wal_during_preparation_cannot_be_attested(prepared, monkeypatch):
    db, _root, prepare = prepared
    original = features.build_training_features
    def create_wal_after_read(*args, **kwargs):
        result = original(*args, **kwargs)
        # Test-local WAL marker only, not an actual mutation of live DB rows.
        db.with_name(db.name + "-wal").write_bytes(b"snapshot changed after initial admission")
        return result
    monkeypatch.setattr(features, "build_training_features", create_wal_after_read)
    with pytest.raises(ValueError, match="checkpointed-snapshot|snapshot-changed"):
        prepare()
