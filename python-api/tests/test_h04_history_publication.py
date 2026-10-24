"""Fixture-only publication checks; never touch the actual collector/runtime."""
from datetime import datetime, timezone
import importlib.util
import json
from pathlib import Path
import sqlite3
import sys

import pytest

SCRIPT = Path(__file__).resolve().parents[2] / "scripts/publish_h04_history_revision.py"
spec = importlib.util.spec_from_file_location("publish_h04_history_revision_under_test", SCRIPT)
publish = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = publish
spec.loader.exec_module(publish)
prep = publish.preparation
NOW = datetime(2026, 10, 17, 8, 0, tzinfo=timezone.utc)


def write_json(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False), encoding="utf-8")


def fixture_revision(tmp_path, monkeypatch):
    from services.prediction_source_revision import initialize_source_revision
    source = tmp_path / "source.db"
    db = sqlite3.connect(source)
    db.executescript("CREATE TABLE races_ultimate(race_id TEXT,data TEXT,created_at TEXT); CREATE TABLE race_results_ultimate(race_id TEXT,data TEXT,created_at TEXT)")
    for day, suffix in (("20261016", "01"), ("20261017", "02")):
        rid = "2026050406" + suffix
        race = {"race_id": rid, "date": day, "surface": "turf", "venue": "東京", "distance": 1600, "num_horses": 4}
        db.execute("INSERT INTO races_ultimate VALUES (?,?,'fixture')", (rid, json.dumps(race)))
        for number in range(1, 5):
            runner = {"race_id": rid, "horse_number": number, "horse_id": f"horse{number}",
                "finish": number, "time_seconds": 90 + number, "odds": 2 * number,
                "popularity": number, "sire": "S", "damsire": "D"}
            db.execute("INSERT INTO race_results_ultimate VALUES (?,?,'fixture')", (rid, json.dumps(runner)))
    db.commit(); db.close()
    assert initialize_source_revision(source.resolve(), True)
    runtime = tmp_path / "runtime"
    (runtime / "daily").mkdir(parents=True)
    write_json(runtime / "daily-bindings/20261017.json", {"old_binding": "immutable"})
    write_json(runtime / "local-config.json", {"source_db": str(source.resolve())})
    db = sqlite3.connect(runtime / "ledger.sqlite3")
    db.executescript("CREATE TABLE schedule_days(date TEXT,status TEXT); CREATE TABLE races(race_id TEXT,race_date TEXT,post_time TEXT,status TEXT)")
    db.execute("INSERT INTO schedule_days VALUES ('20261017','complete')")
    db.execute("INSERT INTO races VALUES ('x','20261017','2026-10-17T07:30:00Z','captured')")
    db.commit(); db.close()
    protected = {"active": "unchanged", "source_pins": {"same": "same"},
        "daily_bindings": {"20261017.json": prep.file_hash(runtime / "daily-bindings/20261017.json")}}
    monkeypatch.setattr(prep, "protection_receipt", lambda **kw: protected)
    monkeypatch.setattr(prep, "reserve_receipt", lambda *args: {"sufficient": True})
    revision = tmp_path / "revision"
    revision.mkdir()
    artifacts = prep.build_standard_artifacts(source, revision, target_date="20261018")
    receipt = {"schema": prep.SCHEMA, "operation": "completed_isolated_revision", "target_date": "20261018",
        "protected_state_unchanged": True, "protected_before": protected, "protected_after": protected,
        "runtime_publication": False, "source_database": str(source.resolve()), "artifacts": artifacts}
    write_json(revision / "revision_receipt.json", receipt)
    manifest = {str(p.relative_to(revision)).replace("\\", "/"): prep.file_hash(p)
                for p in revision.rglob("*") if p.is_file()}
    write_json(revision / "artifact_manifest.json", {"schema": prep.SCHEMA, "sha256": manifest})
    return revision, prep.file_hash(revision / "artifact_manifest.json"), runtime, source


def run_fixture_publish(tmp_path, monkeypatch, **kwargs):
    revision, digest, runtime, source = fixture_revision(tmp_path, monkeypatch)
    result = publish.publish_revision(revision_dir=revision, expected_manifest_sha256=digest,
        runtime_root=runtime, now=NOW, **kwargs)
    return result, revision, runtime, source


def test_dry_run_publishes_nothing(tmp_path, monkeypatch):
    result, revision, runtime, source = run_fixture_publish(tmp_path, monkeypatch)
    assert result["operation"] == "dry_run" and result["publication_performed"] is False
    assert not (runtime / "daily/20261018").exists()
    assert list(revision.parent.glob("revision-publication-*")) == []


def test_publish_only_future_append_with_standard_paths(tmp_path, monkeypatch):
    result, revision, runtime, source = run_fixture_publish(tmp_path, monkeypatch, publish=True)
    assert result["operation"] == "published_next_day_append_only"
    assert result["publication_performed"] is True and result["collector_restart_required"] is False
    assert prep.read_json(runtime / "daily-bindings/20261017.json") == {"old_binding": "immutable"}
    assert not (runtime / "daily-bindings/20261018.json").exists()  # Actual collector seals it tomorrow.
    manifest = prep.read_json(runtime / "daily/20261018/preparation_manifest.json")
    assert manifest["history_max_source_date"] == "20261017"
    assert Path(manifest["state_path"]) == (runtime / "daily/20261018/history_state.json").resolve()
    held = Path(result["receipt_directory"]) / "held_preparation_manifest.json"
    assert held.exists() and held.read_bytes() == (runtime / "daily/20261018/preparation_manifest.json").read_bytes()


@pytest.mark.parametrize("which", ["binding", "directory"])
def test_never_overwrites_existing_target(tmp_path, monkeypatch, which):
    revision, digest, runtime, source = fixture_revision(tmp_path, monkeypatch)
    if which == "binding":
        write_json(runtime / "daily-bindings/20261018.json", {"do_not_change": True})
    else:
        (runtime / "daily/20261018").mkdir()
    with pytest.raises(ValueError, match="already-exists"):
        publish.publish_revision(revision_dir=revision, expected_manifest_sha256=digest, runtime_root=runtime, now=NOW, publish=True)


@pytest.mark.parametrize("name", ["daily/history_state.json", "clean/clean_history.parquet", "snapshot"])
def test_physical_artifact_tampering_blocks_publication(tmp_path, monkeypatch, name):
    revision, digest, runtime, source = fixture_revision(tmp_path, monkeypatch)
    path = Path(prep.read_json(revision / "revision_receipt.json")["artifacts"]["snapshot_path"]) if name == "snapshot" else revision / name
    path.chmod(0o666)
    with path.open("ab") as stream:
        stream.write(b"tampered")
    with pytest.raises(ValueError, match="artifact-changed"):
        publish.publish_revision(revision_dir=revision, expected_manifest_sha256=digest, runtime_root=runtime, now=NOW, publish=True)
    assert not (runtime / "daily/20261018").exists()


def test_manifest_seal_must_be_explicit(tmp_path, monkeypatch):
    revision, digest, runtime, source = fixture_revision(tmp_path, monkeypatch)
    with pytest.raises(ValueError, match="manifest-seal"):
        publish.publish_revision(revision_dir=revision, expected_manifest_sha256="0" * 64, runtime_root=runtime, now=NOW)


def test_source_raw_change_since_revision_rejects(tmp_path, monkeypatch):
    revision, digest, runtime, source = fixture_revision(tmp_path, monkeypatch)
    db = sqlite3.connect(source)
    db.execute("UPDATE race_results_ultimate SET data=data || ' ' WHERE rowid=1"); db.commit(); db.close()
    with pytest.raises(ValueError, match="source-changed-since"):
        publish.publish_revision(revision_dir=revision, expected_manifest_sha256=digest, runtime_root=runtime, now=NOW, publish=True)
    assert not (runtime / "daily/20261018").exists()


def test_source_change_during_generation_holds_manifest_and_keeps_partial_dir(tmp_path, monkeypatch):
    from forward_runtime import data_quality
    revision, digest, runtime, source = fixture_revision(tmp_path, monkeypatch)
    original = data_quality.prepare_daily_history
    def changed(*args, **kwargs):
        result = original(*args, **kwargs)
        db = sqlite3.connect(source)
        db.execute("UPDATE race_results_ultimate SET data=data || ' ' WHERE rowid=1"); db.commit(); db.close()
        return result
    monkeypatch.setattr(data_quality, "prepare_daily_history", changed)
    with pytest.raises(ValueError, match="source-changed-during"):
        publish.publish_revision(revision_dir=revision, expected_manifest_sha256=digest, runtime_root=runtime, now=NOW, publish=True)
    assert (runtime / "daily/20261018/history_state.json").exists()
    assert not (runtime / "daily/20261018/preparation_manifest.json").exists()
    records = list(revision.parent.glob("revision-publication-*/publication_failure.json"))
    assert len(records) == 1
    assert prep.read_json(records[0])["operation"] == "failed_no_completed_runtime_manifest"
    assert (records[0].parent / "held_preparation_manifest.json").exists()


def test_partial_builder_failure_never_removes_files(tmp_path, monkeypatch):
    from forward_runtime import data_quality
    revision, digest, runtime, source = fixture_revision(tmp_path, monkeypatch)
    def partial(clean, output, **kwargs):
        write_json(Path(output) / "partial.json", {"retained": True})
        raise RuntimeError("fixture-failed-during-builder")
    monkeypatch.setattr(data_quality, "prepare_daily_history", partial)
    with pytest.raises(RuntimeError, match="fixture-failed"):
        publish.publish_revision(revision_dir=revision, expected_manifest_sha256=digest, runtime_root=runtime, now=NOW, publish=True)
    assert prep.read_json(runtime / "daily/20261018/partial.json") == {"retained": True}
    assert not (runtime / "daily/20261018/preparation_manifest.json").exists()


def test_next_day_target_cannot_be_published_as_same_day(tmp_path, monkeypatch):
    revision, digest, runtime, source = fixture_revision(tmp_path, monkeypatch)
    with pytest.raises(ValueError, match="same-day-or-past"):
        publish.publish_revision(revision_dir=revision, expected_manifest_sha256=digest, runtime_root=runtime,
            now=datetime(2026, 10, 17, 16, tzinfo=timezone.utc), publish=True)


def test_old_binding_change_since_build_blocks(tmp_path, monkeypatch):
    revision, digest, runtime, source = fixture_revision(tmp_path, monkeypatch)
    monkeypatch.setattr(prep, "protection_receipt", lambda **kw: {"changed": True})
    with pytest.raises(ValueError, match="protected-state-changed"):
        publish.publish_revision(revision_dir=revision, expected_manifest_sha256=digest, runtime_root=runtime, now=NOW, publish=True)


def test_checked_artifact_path_escape_rejected(tmp_path):
    with pytest.raises(ValueError, match="relative-path"):
        publish.checked_child(tmp_path, "../outside")


def test_midnight_natural_load_margin_is_required():
    with pytest.raises(ValueError, match="30-minute-margin"):
        publish.publication_clock("20261018", now=datetime(2026, 10, 17, 14, 45, tzinfo=timezone.utc))


def test_source_change_at_final_boundary_recovers_new_visible_manifest(tmp_path, monkeypatch):
    revision, digest, runtime, source = fixture_revision(tmp_path, monkeypatch)
    original = prep.source_epoch_receipt
    calls = {"live": 0}
    def changed_at_boundary(path):
        if Path(path).resolve() == source.resolve():
            calls["live"] += 1
            if calls["live"] == 3:  # After exclusive final manifest creation.
                db = sqlite3.connect(source)
                db.execute("UPDATE race_results_ultimate SET data=data || ' ' WHERE rowid=1")
                db.commit(); db.close()
        return original(path)
    monkeypatch.setattr(prep, "source_epoch_receipt", changed_at_boundary)
    with pytest.raises(ValueError, match="source-changed-at-publication-boundary"):
        publish.publish_revision(revision_dir=revision, expected_manifest_sha256=digest,
            runtime_root=runtime, now=NOW, publish=True)
    assert not (runtime / "daily/20261018/preparation_manifest.json").exists()
    recovered = list(revision.parent.glob("revision-publication-*/failed_visible_manifest_*.json"))
    assert len(recovered) == 1
    assert (runtime / "daily/20261018/history_state.json").exists()

