"""Normal raw47 cache admission: immutable lineage, PIT and atomic reuse."""
from concurrent.futures import ThreadPoolExecutor
import json
from pathlib import Path
import shutil

import pandas as pd
import pytest

from training import db_shared_cache as cache
from training import db_shared_features as shared
from training import db_shared_fit as fit
from test_db_shared_features import write_db


def fixture(tmp_path):
    return write_db(tmp_path / "snapshot.db"), tmp_path / "normal-cache"


def prepare(source, root, **kwargs):
    return cache.prepare_shared_inputs(source, "2024-01", "2025-01", root, **kwargs)


def generation(root, receipt):
    entry = root / receipt["cache_key_sha256"]
    pointer = json.loads((entry / "current.json").read_text(encoding="utf-8"))
    return entry / pointer["generation"]


def mutate_manifest(root, receipt, mutate):
    entry = root / receipt["cache_key_sha256"]
    pointer = json.loads((entry / "current.json").read_text(encoding="utf-8"))
    manifest_path = entry / pointer["generation"] / "manifest.json"
    value = json.loads(manifest_path.read_text(encoding="utf-8"))
    mutate(value)
    manifest_path.write_text(json.dumps(value, ensure_ascii=False), encoding="utf-8")
    pointer["manifest_sha256"] = cache.file_hash(manifest_path)
    (entry / "current.json").write_text(json.dumps(pointer), encoding="utf-8")


def test_cold_then_hit_exact_nan_dtype_cohort_and_readonly(tmp_path, monkeypatch):
    source, root = fixture(tmp_path)
    before = cache.file_hash(source)
    cold = prepare(source, root)
    assert cold[3]["cache_status"] == "miss"
    assert cold[3]["reused_bytes"] == 0
    monkeypatch.setattr(shared, "load_db_frames", lambda *args: pytest.fail("Cache hit must not scan DB"))
    hit = prepare(source, root)
    assert hit[3]["cache_status"] == "hit"
    assert hit[3]["reused_bytes"] > 0
    assert hit[3]["input_hashes"] == cold[3]["input_hashes"]
    pd.testing.assert_frame_equal(hit[0], cold[0], check_exact=True)
    pd.testing.assert_frame_equal(hit[1], cold[1], check_exact=True)
    assert hit[2] == cold[2]
    assert hit[1].isna().any().any()
    assert hit[1].attrs["same_day_or_future_history_used"] is False
    assert hit[1].attrs["current_outcomes_consumed"] is False
    assert hit[3]["train_encoder_reused"] is False
    assert hit[3]["fitted_baseline_reused"] is False
    assert hit[3]["calibrator_reused"] is False
    assert cache.file_hash(source) == before


def test_month_and_whole_date_spellings_share_normalized_entry(tmp_path):
    source, root = fixture(tmp_path)
    first = prepare(source, root)
    second = cache.prepare_shared_inputs(source, "20240101", "20250131", root)
    assert second[3]["cache_status"] == "hit"
    assert first[3]["cache_key_sha256"] == second[3]["cache_key_sha256"]
    assert second[3]["requested_period"] == ["20240101", "20250131"]


def test_reuse_disabled_forces_same_raw47_refresh_without_reading_old_entry(tmp_path, monkeypatch):
    source, root = fixture(tmp_path)
    first = prepare(source, root)
    original_generation = generation(root, first[3])
    original_loader = shared.load_db_frames
    calls = []
    def counted(*args):
        calls.append(args)
        return original_loader(*args)
    monkeypatch.setattr(shared, "load_db_frames", counted)
    monkeypatch.setattr(cache, "_read_entry", lambda *args: pytest.fail("Explicit refresh must not read old entry"))
    fresh = prepare(source, root, reuse_features=False)
    assert len(calls) == 1
    assert fresh[3]["cache_status"] == "refresh"
    assert fresh[3]["reuse_requested"] is False
    assert fresh[3]["reused_bytes"] == 0
    assert fresh[3]["input_hashes"] == first[3]["input_hashes"]
    assert original_generation.exists()
    assert generation(root, fresh[3]) != original_generation
    with pytest.raises(ValueError, match="boolean"):
        prepare(source, root, reuse_features="false")


def test_period_and_snapshot_changes_never_reuse(tmp_path):
    source, root = fixture(tmp_path)
    first = prepare(source, root)
    second = cache.prepare_shared_inputs(source, "2024-01", "2024-01", root)
    assert second[3]["cache_status"] == "miss"
    assert first[3]["cache_key_sha256"] != second[3]["cache_key_sha256"]
    # A copied identical checkpoint has the same key, regardless of filename.
    copied = tmp_path / "copied.db"
    shutil.copyfile(source, copied)
    assert prepare(copied, root)[3]["cache_status"] == "hit"
    with copied.open("ab") as stream:
        stream.write(b"ledger-only-change")
    assert prepare(copied, root)[3]["cache_status"] == "miss"


def test_source_contract_change_never_reuses(tmp_path, monkeypatch):
    source, root = fixture(tmp_path)
    first = prepare(source, root)
    old = fit.source_hashes()
    monkeypatch.setattr(fit, "source_hashes", lambda: {**old, "cache.test.additional_source": "a" * 64})
    second = prepare(source, root)
    assert second[3]["cache_status"] == "miss"
    assert first[3]["cache_key_sha256"] != second[3]["cache_key_sha256"]
    with pytest.raises(ValueError, match="expected-source-hash"):
        prepare(source, root, expected_source_hashes=old)


@pytest.mark.parametrize("change,expected", [
    (lambda value: value.update(schema="unknown-v9"), "key-lineage"),
    (lambda value: value["key_payload"].update(period=["20250101", "20250131"]), "key-lineage"),
    (lambda value: value["files"]["features"]["dtypes"].__setitem__(0, "float64"), "schema-or-digest"),
    (lambda value: value["point_in_time_audit"].update(current_outcomes_consumed=True), "point-in-time"),
    (lambda value: value["point_in_time_audit"]["point_in_time_audit"].update(same_day_future_references=1), "point-in-time"),
    (lambda value: value["coverage"].update(date_to="20241231"), "coverage-lineage"),
    (lambda value: value["coverage"]["target"].update(eligible_rows=999), "coverage-lineage"),
    (lambda value: value["files"]["features"].update(path="../../model.joblib"), "frame-path"),
    (lambda value: value.update(actual_date_to="20990101"), "manifest-cohort"),
    (lambda value: value.update(files=None), "files-manifest"),
    (lambda value: value.update(stored_bytes=1), "manifest-cohort"),
])
def test_tampered_metadata_rebuilds_and_retains_rejected_generation(tmp_path, change, expected):
    source, root = fixture(tmp_path)
    first = prepare(source, root)
    rejected = generation(root, first[3])
    mutate_manifest(root, first[3], change)
    repaired = prepare(source, root)
    assert repaired[3]["cache_status"] == "rebuilt"
    assert expected in repaired[3]["rejected_cache_reason"]
    assert rejected.exists()
    assert rejected != generation(root, repaired[3])
    pd.testing.assert_frame_equal(first[1], repaired[1], check_exact=True)
    assert prepare(source, root)[3]["cache_status"] == "hit"


def test_tampered_parquet_hash_rebuilds_without_loading_it(tmp_path, monkeypatch):
    source, root = fixture(tmp_path)
    first = prepare(source, root)
    corrupted = generation(root, first[3]) / "features.parquet"
    corrupted.write_bytes(b"not parquet")
    repaired = prepare(source, root)
    assert repaired[3]["cache_status"] == "rebuilt"
    assert "file-hash-mismatch" in repaired[3]["rejected_cache_reason"]
    assert corrupted.read_bytes() == b"not parquet"


def test_changed_actual_feature_dtypes_fail_even_with_updated_file_receipt(tmp_path):
    source, root = fixture(tmp_path)
    first = prepare(source, root)
    stored = generation(root, first[3]) / "features.parquet"
    frame = pd.read_parquet(stored)
    frame.iloc[:, 0] = frame.iloc[:, 0].astype("float64")
    # Assigning via iloc may preserve dtype on some pandas versions.
    frame[frame.columns[0]] = frame[frame.columns[0]].astype("float64")
    frame.to_parquet(stored, index=False)
    mutate_manifest(root, first[3], lambda value: value["files"].update(features=cache._receipt(frame, stored)))
    result = prepare(source, root)
    assert result[3]["cache_status"] == "rebuilt"
    assert "dtype-contract" in result[3]["rejected_cache_reason"]


def test_non_object_manifest_is_rebuilt(tmp_path):
    source, root = fixture(tmp_path)
    first = prepare(source, root)
    directory = generation(root, first[3])
    path = directory / "manifest.json"
    path.write_text("[]", encoding="utf-8")
    entry = directory.parent
    pointer = json.loads((entry / "current.json").read_text(encoding="utf-8"))
    pointer["manifest_sha256"] = cache.file_hash(path)
    (entry / "current.json").write_text(json.dumps(pointer), encoding="utf-8")
    result = prepare(source, root)
    assert result[3]["cache_status"] == "rebuilt"
    assert "key-lineage" in result[3]["rejected_cache_reason"]


def test_incomplete_staging_is_not_admitted(tmp_path):
    source, root = fixture(tmp_path)
    key = cache._key(cache.file_hash(source), ["20240101", "20250131"], fit.source_hashes())
    stage = root / cache.canonical_hash(key) / ".staging-orphan"
    stage.mkdir(parents=True)
    (stage / "features.parquet").write_bytes(b"partial")
    result = prepare(source, root)
    assert result[3]["cache_status"] == "rebuilt"
    assert result[3]["rejected_cache_reason"] == "shared-cache-incomplete-entry"
    assert stage.is_dir()


def test_concurrent_writers_publish_complete_generations_only(tmp_path):
    source, root = fixture(tmp_path)
    with ThreadPoolExecutor(max_workers=2) as executor:
        results = list(executor.map(lambda _: prepare(source, root), range(2)))
    assert results[0][3]["input_hashes"] == results[1][3]["input_hashes"]
    hit = prepare(source, root)
    assert hit[3]["cache_status"] == "hit"
    assert list(root.rglob(".staging-*")) == []
    assert len(list(root.rglob("generation-*"))) in (1, 2)


def test_snapshot_change_during_preparation_fails_before_admission(tmp_path, monkeypatch):
    source, root = fixture(tmp_path)
    original = shared.build_training_features
    def mutate(*args):
        value = original(*args)
        with source.open("ab") as stream:
            stream.write(b"changed-during-load")
        return value
    monkeypatch.setattr(shared, "build_training_features", mutate)
    with pytest.raises(ValueError, match="snapshot-changed"):
        prepare(source, root)
    assert list(root.rglob("current.json")) == []


def test_uncheckpointed_snapshot_and_bad_period_fail_before_cache(tmp_path):
    source, root = fixture(tmp_path)
    Path(str(source) + "-wal").write_bytes(b"active")
    with pytest.raises(ValueError, match="checkpointed"):
        prepare(source, root)
    assert not root.exists()
    with pytest.raises(ValueError, match="period"):
        cache.prepare_shared_inputs(source, "2012-01", "2026-09", root)


def benchmark_fixture(tmp_path, monkeypatch):
    source, root = fixture(tmp_path)
    cold = prepare(source, root)
    research = tmp_path / "research"
    external = research / "prior-run" / "raw47-cache"
    external.mkdir(parents=True)
    own = generation(root, cold[3])
    normal = json.loads((own / "manifest.json").read_text(encoding="utf-8"))
    old_key = {"schema": cache.BENCHMARK_SCHEMA, "snapshot_sha256": cache.file_hash(source),
               "period": ["2024-01", "2025-01"], "source_hashes": fit.source_hashes(),
               "feature_columns": list(fit.FEATURE_COLUMNS), "categorical_columns": list(fit.CATEGORICAL_COLUMNS),
               "history_policy": cache.HISTORY_POLICY}
    manifest = {**old_key, "key_payload": old_key, "key_sha256": cache.canonical_hash(old_key),
                "files": normal["files"], "coverage": normal["coverage"],
                "point_in_time_audit": normal["point_in_time_audit"]}
    for name in ("target.parquet", "features.parquet"):
        shutil.copyfile(own / name, external / name)
    (external / "manifest.json").write_text(json.dumps(manifest, ensure_ascii=False), encoding="utf-8")
    monkeypatch.setattr(cache, "_IMPORT_ROOT", research)
    return source, external, cold


def test_explicit_benchmark_import_verifies_and_keeps_original(tmp_path, monkeypatch):
    source, external, cold = benchmark_fixture(tmp_path, monkeypatch)
    before = {path.name: cache.file_hash(path) for path in external.iterdir()}
    root = tmp_path / "new-normal-cache"
    receipt = cache.import_shared_cache(source, "202401", "202501", root, external)
    assert receipt["cache_status"] == "imported"
    hit = prepare(source, root)
    assert hit[3]["cache_status"] == "hit"
    pd.testing.assert_frame_equal(hit[1], cold[1], check_exact=True)
    assert {path.name: cache.file_hash(path) for path in external.iterdir()} == before


def test_benchmark_import_rejects_other_period_and_unsafe_paths(tmp_path, monkeypatch):
    source, external, _ = benchmark_fixture(tmp_path, monkeypatch)
    root = tmp_path / "new-normal-cache"
    with pytest.raises(ValueError, match="lineage"):
        cache.import_shared_cache(source, "202401", "202401", root, external)
    outside = tmp_path / "outside"
    outside.mkdir()
    with pytest.raises(ValueError, match="research-boundary"):
        cache.import_shared_cache(source, "202401", "202501", root, outside)
    manifest_path = external / "manifest.json"
    value = json.loads(manifest_path.read_text(encoding="utf-8"))
    value["files"]["features"]["path"] = "../../model.joblib"
    manifest_path.write_text(json.dumps(value), encoding="utf-8")
    with pytest.raises(ValueError, match="import-path"):
        cache.import_shared_cache(source, "202401", "202501", root, external)
