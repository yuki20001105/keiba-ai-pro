"""Prepare a NEW H04 daily history revision without changing its frozen runtime.

Dry-run is the default. The explicit build uses the very same immutable SQLite
backup, canonical quality gate and feature-state builder as the fixed collector.
No live DB export substitute, training, network, model activation, daily binding
replacement or publication into the collector is implemented here.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timedelta, timezone
import hashlib
import json
from pathlib import Path
import re
import shutil
import sqlite3
import sys

ROOT = Path(__file__).resolve().parents[1]
for _package in (ROOT / "python-api", ROOT / "keiba"):
    if str(_package) not in sys.path:
        sys.path.insert(0, str(_package))

JST = timezone(timedelta(hours=9))
UTC = timezone.utc
REVISION_ROOT = ROOT / "reports/generated/h04-history-revisions"
RUNTIME_ROOT = ROOT / "reports/generated/forward-runtime/h04-live-20260920-v2"
SOURCE_DB = ROOT / "keiba/data/keiba_ultimate.db"
SCHEMA = "h04-history-revision-preparation-v1"


def read_json(path):
    return json.loads(Path(path).read_text(encoding="utf-8"))


def file_hash(path):
    result = hashlib.sha256()
    with Path(path).open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            result.update(block)
    return result.hexdigest()


def object_hash(value):
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True,
        separators=(",", ":"), allow_nan=False).encode()).hexdigest()


def write_new_json(path, value):
    body = (json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False) + "\n").encode()
    with Path(path).open("xb") as stream:
        stream.write(body)


def validate_target(target_date, *, now):
    if not isinstance(now, datetime) or now.tzinfo is None or now.utcoffset() is None:
        raise ValueError("timezone-aware-preparation-clock-required")
    if not isinstance(target_date, str) or not re.fullmatch(r"\d{8}", target_date):
        raise ValueError("canonical-target-date-required")
    target = datetime.strptime(target_date, "%Y%m%d").date()
    today = now.astimezone(JST).date()
    if target <= today:
        raise ValueError("same-day-or-past-target-preparation-forbidden")
    # A prospective revision is prepared for the next day, not an arbitrary
    # future cohort which might later be mistaken for complete fresh history.
    if target != today + timedelta(days=1):
        raise ValueError("next-calendar-day-target-required")
    return target_date


def validate_output(output_dir, *, revision_root=REVISION_ROOT):
    output, base = Path(output_dir), Path(revision_root).resolve()
    if output.is_symlink():
        raise ValueError("revision-output-symlink-forbidden")
    resolved = output.resolve()
    if resolved == base or not resolved.is_relative_to(base):
        raise ValueError("new-isolated-history-revision-directory-required")
    if output.exists():
        raise ValueError("history-revision-output-must-not-exist")
    for parent in output.parents:
        if parent.exists() and parent.is_symlink():
            raise ValueError("revision-output-parent-symlink-forbidden")
    return resolved


def protection_receipt(*, repo=ROOT, runtime_root=RUNTIME_ROOT):
    """Hash small frozen inputs and the active model, never the source DB."""
    repo, runtime_root = Path(repo), Path(runtime_root)
    envelope = read_json(runtime_root / "protocol.json")
    protocol = envelope["protocol"]
    if object_hash(protocol) != envelope["protocol_sha256"]:
        raise ValueError("frozen-runtime-protocol-integrity-failed")
    if protocol.get("fixed_model_id") != "H04" or protocol.get("paper_only") is not True:
        raise ValueError("fixed-paper-h04-runtime-required")
    pins = protocol["source_manifest"]
    if len(pins) != 35:
        raise ValueError("expected-35-frozen-source-pins")
    actual = {name: file_hash(repo / name) for name in pins}
    if actual != pins:
        raise ValueError("frozen-h04-source-pins-changed")
    objects = {name: file_hash(runtime_root / "objects" / expected)
               for name, expected in protocol["artifact_hashes"].items()}
    if objects != protocol["artifact_hashes"]:
        raise ValueError("frozen-h04-artifact-integrity-failed")
    pointer = repo / "python-api/models/.active_model.json"
    active = read_json(pointer)["model_id"]
    if not isinstance(active, str) or not re.fullmatch(r"model_[A-Za-z0-9_]+", active):
        raise ValueError("active-model-pointer-invalid")
    binding_dir = runtime_root / "daily-bindings"
    bindings = {p.name: file_hash(p) for p in sorted(binding_dir.glob("*.json"))}
    return {"active_model_id": active, "active_pointer_sha256": file_hash(pointer),
        "active_model_sha256": file_hash(pointer.parent / (active + ".joblib")),
        "protocol_sha256": file_hash(runtime_root / "protocol.json"),
        "h04_artifact_sha256": objects,
        "local_config_sha256": file_hash(runtime_root / "local-config.json"),
        "source_pins": actual, "daily_bindings": bindings,
        "feature_count": len(protocol["feature_columns"])}


def capture_idle_receipt(runtime_root, *, now):
    """Refuse heavy work until today's registered natural captures are over."""
    day = now.astimezone(JST).strftime("%Y%m%d")
    path = (Path(runtime_root) / "ledger.sqlite3").resolve(strict=True)
    db = sqlite3.connect(path.as_uri() + "?mode=ro", uri=True, timeout=5)
    try:
        db.execute("PRAGMA query_only=ON")
        db.execute("BEGIN")
        status = db.execute("SELECT status FROM schedule_days WHERE date=?", (day,)).fetchone()
        races = list(db.execute("SELECT race_id,post_time,status FROM races WHERE race_date=?", (day,)))
    finally:
        db.rollback()
        db.close()
    if status is None or status[0] not in ("complete", "not_held"):
        raise ValueError("today-complete-schedule-proof-required-before-heavy-build")
    active = [str(rid) for rid, _, state in races if state in ("queued", "capturing")]
    if active:
        raise ValueError("natural-capture-work-remains-no-heavy-build")
    post_times = [datetime.fromisoformat(post.replace("Z", "+00:00")) for _, post, _ in races]
    if any(post.tzinfo is None for post in post_times):
        raise ValueError("schedule-post-time-must-be-aware")
    latest = max(post_times, default=None)
    if latest is not None and now.astimezone(UTC) < latest.astimezone(UTC) + timedelta(minutes=1):
        raise ValueError("wait-until-final-registered-race-start-plus-one-minute")
    return {"race_date": day, "schedule_status": status[0], "registered_races": len(races),
        "queued_or_capturing": active, "latest_post_time": latest.isoformat() if latest else None,
        "checked_at_utc": now.astimezone(UTC).isoformat(), "ledger_read_only": True}


def reserve_receipt(source_db, output_parent):
    import psutil
    source = Path(source_db).resolve(strict=True)
    if source.is_symlink() or not source.is_file() or source.suffix.lower() != ".db":
        raise ValueError("source-database-required")
    parent = Path(output_parent)
    while not parent.exists():
        parent = parent.parent
    size, available, free = source.stat().st_size, psutil.virtual_memory().available, shutil.disk_usage(parent).free
    required_disk = 2 * size + 6 * 1024**3
    return {"source_size_bytes": size, "available_memory_bytes": available,
        "required_memory_bytes": 4 * 1024**3, "free_disk_bytes": free,
        "required_disk_bytes": required_disk,
        "sufficient": available >= 4 * 1024**3 and free >= required_disk}


def source_epoch_receipt(source_db):
    """Read the existing trigger-backed source epoch; never install metadata.

    The result also binds the quality projection consumed by the fixed gate.
    Prediction/log-only writes are intentionally not confused with raw history
    changes. No full database file hash or copy occurs in this check.
    """
    from services.prediction_source_revision import _validated_state
    path = Path(source_db)
    if path.is_symlink() or any(parent.is_symlink() for parent in path.parents):
        raise ValueError("source-epoch-linked-path-forbidden")
    path = path.resolve(strict=True)
    before = path.stat()
    db = sqlite3.connect(path.as_uri() + "?mode=ro", uri=True, timeout=5)
    try:
        db.execute("PRAGMA query_only=ON")
        db.execute("BEGIN")
        state = _validated_state(db)
        if state is None:
            raise ValueError("validated-source-generation-required-not-file-mtime-fallback")
        tables = {row[0] for row in db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        quality_hash, quality_rows = hashlib.sha256(), 0
        quality_present = "scrape_race_acquisition_state" in tables
        if quality_present:
            for row in db.execute("SELECT race_id,lifecycle,quality_status,excluded_from_standard "
                                  "FROM scrape_race_acquisition_state ORDER BY race_id"):
                quality_hash.update(json.dumps(list(row), ensure_ascii=False,
                    separators=(",", ":"), allow_nan=False).encode() + b"\n")
                quality_rows += 1
        sidecars = {name: bool(db.execute(f"SELECT 1 FROM {name} LIMIT 1").fetchone())
                    for name in ("licensed_history_entries", "official_history_entries") if name in tables}
    finally:
        db.rollback()
        db.close()
    after = path.stat()
    if (before.st_dev, before.st_ino) != (after.st_dev, after.st_ino):
        raise ValueError("source-database-replaced-during-epoch-check")
    return {"schema": "h04-history-source-epoch-v1", "source_path": str(path),
        "file_identity": [before.st_dev, before.st_ino],
        "content_epoch": {"core_generation": list(state), "quality_table_present": quality_present,
            "quality_rows": quality_rows, "quality_projection_sha256": quality_hash.hexdigest(),
            "sidecar_nonempty": sidecars},
        "database_file_hash_or_copy_performed": False}


def build_standard_artifacts(source_db, revision_dir, *, target_date, progress=None):
    """Use the FROZEN standard functions; no independent feature formula."""
    from training.local_retrain import create_sqlite_snapshot
    from forward_runtime.data_quality import audit_clean_history, prepare_daily_history
    directory = Path(revision_dir).resolve()
    source_before = source_epoch_receipt(source_db)
    snapshot = create_sqlite_snapshot(Path(source_db).resolve(), directory / "snapshots")
    snapshot_epoch = source_epoch_receipt(snapshot.path)
    if (snapshot_epoch["content_epoch"] != source_before["content_epoch"]
            or source_epoch_receipt(source_db) != source_before):
        raise ValueError("source-changed-during-standard-snapshot-creation")
    report = audit_clean_history(snapshot.path, directory / "clean", before_date=target_date, progress=progress)
    daily = prepare_daily_history(directory / "clean", directory / "daily", target_date=target_date, progress=progress)
    if daily["snapshot_sha256"] != snapshot.sha256 or report["snapshot_sha256"] != snapshot.sha256:
        raise ValueError("physical-snapshot-proof-not-preserved")
    if not daily["history_max_source_date"] < target_date:
        raise ValueError("same-day-or-future-history-in-revision")
    if daily["same_day_or_future_history_used"] is not False:
        raise ValueError("history-boundary-proof-required")
    source_after = source_epoch_receipt(source_db)
    if source_after != source_before:
        raise ValueError("source-changed-during-history-revision-build")
    return {"snapshot_path": str(snapshot.path), "snapshot_sha256": snapshot.sha256,
        "snapshot_size_bytes": snapshot.size_bytes, "quality": report, "daily": daily,
        "source_revision_before": source_before, "source_revision_after": source_after,
        "snapshot_content_epoch": snapshot_epoch["content_epoch"]}


def prepare_revision(*, target_date, output_dir, build=False, now=None,
                     source_db=SOURCE_DB, runtime_root=RUNTIME_ROOT):
    current = now or datetime.now(UTC)
    validate_target(target_date, now=current)
    output = validate_output(output_dir)
    before = protection_receipt(runtime_root=runtime_root)
    resources = reserve_receipt(source_db, output.parent)
    previous = Path(runtime_root) / "daily-bindings" / (current.astimezone(JST).strftime("%Y%m%d") + ".json")
    previous_manifest = read_json(previous) if previous.exists() else None
    result = {"schema": SCHEMA, "operation": "dry_run", "target_date": target_date,
        "output_dir": str(output), "source_database": str(Path(source_db).resolve()),
        "resources": resources, "protected_before": before,
        "previous_bound_history": previous_manifest,
        "uses_standard_snapshot_quality_and_feature_functions": True,
        "physical_database_copy_or_hash_performed": False, "network": False, "training": False,
        "model_activation": False, "runtime_publication": False, "existing_artifacts_modified": False,
        "next_action": "Explicit --build only after all natural captures finish; retain as isolated revision. Future-day runtime publication is a separate operation, not implemented here."}
    if not build:
        return result
    if not resources["sufficient"]:
        raise ValueError("same-standard-preparation-resource-reserve-required")
    result["capture_idle"] = capture_idle_receipt(runtime_root, now=current)
    result["operation"] = "isolated_build_requested"
    output.mkdir(parents=True, exist_ok=False)
    write_new_json(output / "preparation_request.json", result)
    def progress(stage, pct):
        print(json.dumps({"stage": stage, "progress_pct": pct}, ensure_ascii=False), file=sys.stderr, flush=True)
    try:
        import psutil
        process = psutil.Process()
        if hasattr(psutil, "BELOW_NORMAL_PRIORITY_CLASS"):
            process.nice(psutil.BELOW_NORMAL_PRIORITY_CLASS)
        artifacts = build_standard_artifacts(source_db, output, target_date=target_date, progress=progress)
        after = protection_receipt(runtime_root=runtime_root)
        if before != after:
            raise ValueError("protected-h04-model-protocol-or-binding-changed-during-preparation")
        result.update(operation="completed_isolated_revision", artifacts=artifacts,
            protected_after=after, protected_state_unchanged=True,
            physical_database_copy_or_hash_performed=True,
            completed_at_utc=datetime.now(UTC).isoformat())
        result["history_lag_days"] = (datetime.strptime(target_date, "%Y%m%d").date()
            - datetime.strptime(artifacts["daily"]["history_max_source_date"], "%Y%m%d").date()).days
        write_new_json(output / "revision_receipt.json", result)
        paths = [p for p in sorted(output.rglob("*")) if p.is_file()]
        # The standard snapshot digest is reused, not an additional 3 GB scan.
        snapshot_path = Path(artifacts["snapshot_path"])
        manifest = {str(p.relative_to(output)).replace("\\", "/"):
            (artifacts["snapshot_sha256"] if p == snapshot_path else file_hash(p)) for p in paths}
        write_new_json(output / "artifact_manifest.json", {"schema": SCHEMA, "sha256": manifest})
        result["artifact_manifest_sha256"] = file_hash(output / "artifact_manifest.json")
        return result
    except Exception as exc:
        write_new_json(output / "preparation_failure.json", {"schema": SCHEMA,
            "operation": "failed_isolated_revision_not_published", "error_type": type(exc).__name__,
            "reason": str(exc), "existing_artifacts_modified_by_this_script": False,
            "recorded_at_utc": datetime.now(UTC).isoformat()})
        raise


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--target-date", required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--build", action="store_true")
    args = parser.parse_args()
    result = prepare_revision(target_date=args.target_date, output_dir=args.output_dir, build=args.build)
    if args.build:
        print(json.dumps({key: result.get(key) for key in ("schema", "operation", "target_date", "output_dir", "history_lag_days", "protected_state_unchanged", "artifact_manifest_sha256")}, ensure_ascii=False, indent=2))
    else:
        print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
