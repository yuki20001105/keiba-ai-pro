"""Explicitly append a verified NEXT-day H04 history; never replace a binding.

This helper is dry-run by default. A successful --publish only adds an absent
future daily directory via the frozen standard builder. Its completion manifest
is held outside the runtime until all checks pass. Failures keep the new partial
directory and recoverable manifest evidence; old files are never removed.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timedelta, timezone
import json
import os
from pathlib import Path
import re
import sys
import uuid

ROOT = Path(__file__).resolve().parents[1]
if str(Path(__file__).resolve().parent) not in sys.path:
    sys.path.insert(0, str(Path(__file__).resolve().parent))
import prepare_h04_history_revision as preparation

SCHEMA = "h04-history-next-day-publication-v1"
UTC = timezone.utc


def publication_clock(target_date, *, now):
    preparation.validate_target(target_date, now=now)
    target_midnight = datetime.strptime(target_date, "%Y%m%d").replace(tzinfo=preparation.JST)
    if now.astimezone(preparation.JST) >= target_midnight - timedelta(minutes=30):
        raise ValueError("publication-needs-30-minute-margin-before-target-day-natural-load")


def checked_child(directory, relative):
    path = Path(relative)
    if not isinstance(relative, str) or path.is_absolute() or ".." in path.parts:
        raise ValueError("revision-manifest-relative-path-required")
    base = Path(directory).resolve(strict=True)
    candidate = base / path
    if candidate.is_symlink() or any(parent.is_symlink() for parent in candidate.parents):
        raise ValueError("revision-artifact-symlink-forbidden")
    resolved = candidate.resolve(strict=True)
    if not resolved.is_relative_to(base) or not resolved.is_file():
        raise ValueError("revision-artifact-must-be-contained-file")
    return resolved


def verify_revision(revision_dir, *, expected_manifest_sha256, runtime_root):
    directory = Path(revision_dir)
    if directory.is_symlink():
        raise ValueError("revision-directory-symlink-forbidden")
    directory = directory.resolve(strict=True)
    manifest_path = directory / "artifact_manifest.json"
    if not isinstance(expected_manifest_sha256, str) or not re.fullmatch(r"[0-9a-f]{64}", expected_manifest_sha256):
        raise ValueError("explicit-revision-manifest-sha256-required")
    if preparation.file_hash(manifest_path) != expected_manifest_sha256:
        raise ValueError("revision-manifest-seal-mismatch")
    manifest = preparation.read_json(manifest_path)
    if manifest.get("schema") != preparation.SCHEMA or not isinstance(manifest.get("sha256"), dict):
        raise ValueError("completed-revision-artifact-manifest-required")
    required = {"revision_receipt.json", "clean/repair_manifest.json", "clean/quality_report.json",
                "clean/clean_history.parquet", "daily/preparation_manifest.json",
                "daily/history_state.json", "daily/prior_bloodlines.json"}
    if not required.issubset(manifest["sha256"]):
        raise ValueError("revision-manifest-required-artifacts-missing")
    for name, digest in manifest["sha256"].items():
        if not isinstance(digest, str) or not re.fullmatch(r"[0-9a-f]{64}", digest):
            raise ValueError("revision-artifact-digest-invalid")
        if preparation.file_hash(checked_child(directory, name)) != digest:
            raise ValueError("revision-artifact-changed:" + name)
    receipt = preparation.read_json(directory / "revision_receipt.json")
    if (receipt.get("schema") != preparation.SCHEMA
            or receipt.get("operation") != "completed_isolated_revision"
            or receipt.get("protected_state_unchanged") is not True
            or receipt.get("runtime_publication") is not False):
        raise ValueError("completed-unpublished-isolated-revision-required")
    actual_protected = preparation.protection_receipt(runtime_root=runtime_root)
    if receipt["protected_before"] != actual_protected or receipt["protected_after"] != actual_protected:
        raise ValueError("protected-state-changed-since-isolated-revision")
    artifacts = receipt["artifacts"]
    runtime_source = preparation.read_json(Path(runtime_root) / "local-config.json")["source_db"]
    if Path(receipt["source_database"]).resolve(strict=True) != Path(runtime_source).resolve(strict=True):
        raise ValueError("revision-source-differs-from-frozen-runtime-source")
    snapshot = Path(artifacts["snapshot_path"]).resolve(strict=True)
    if not snapshot.is_relative_to(directory) or snapshot.suffix != ".db":
        raise ValueError("physical-snapshot-within-revision-required")
    snapshot_relative = str(snapshot.relative_to(directory)).replace("\\", "/")
    if manifest["sha256"].get(snapshot_relative) != artifacts["snapshot_sha256"]:
        raise ValueError("physical-snapshot-manifest-proof-required")
    if snapshot.stat().st_size != artifacts["snapshot_size_bytes"]:
        raise ValueError("physical-snapshot-size-proof-mismatch")
    # The manifest verification above rehashed the physical snapshot, not an
    # epoch or logical row digest mislabeled as snapshot_sha256.
    source = preparation.source_epoch_receipt(receipt["source_database"])
    if source != artifacts["source_revision_before"] or source != artifacts["source_revision_after"]:
        raise ValueError("source-changed-since-isolated-revision")
    if preparation.source_epoch_receipt(snapshot)["content_epoch"] != source["content_epoch"]:
        raise ValueError("snapshot-source-content-epoch-mismatch")
    daily = preparation.read_json(directory / "daily/preparation_manifest.json")
    if artifacts["daily"] != daily or daily["target_date"] != receipt["target_date"]:
        raise ValueError("isolated-daily-manifest-receipt-mismatch")
    if daily["snapshot_sha256"] != artifacts["snapshot_sha256"]:
        raise ValueError("daily-physical-snapshot-proof-mismatch")
    return {"directory": directory, "receipt": receipt, "protected": actual_protected,
        "source_revision": source, "daily": daily, "manifest_sha256": expected_manifest_sha256}


def target_paths(runtime_root, target_date, *, require_absent_directory):
    root = Path(runtime_root).resolve(strict=True)
    path = root / "daily" / target_date
    binding = root / "daily-bindings" / (target_date + ".json")
    if path.is_symlink() or binding.is_symlink():
        raise ValueError("future-runtime-path-symlink-forbidden")
    if binding.exists():
        raise ValueError("target-daily-binding-already-exists")
    if require_absent_directory and path.exists():
        raise ValueError("target-daily-directory-already-exists-no-overwrite")
    if not path.parent.exists() or path.parent.is_symlink():
        raise ValueError("existing-runtime-daily-parent-required")
    return path, binding


def owned_directory(path, identity):
    stat = Path(path).stat()
    if Path(path).is_symlink() or (stat.st_dev, stat.st_ino) != identity:
        raise ValueError("new-target-directory-identity-changed")


def hold_completion_manifest(path, receipt_dir, *, owned_identity):
    """Recoverably move only THIS invocation's newly generated manifest."""
    owned_directory(Path(path).parent, owned_identity)
    held = Path(receipt_dir) / "held_preparation_manifest.json"
    if held.exists():
        raise ValueError("held-completion-manifest-already-exists")
    Path(path).rename(held)
    return held


def verify_generated_daily(target_dir, held_path, expected, *, target_date):
    from forward_adapters.feature_state import _hash
    manifest = preparation.read_json(held_path)
    if (manifest.get("status") != "completed" or manifest.get("target_date") != target_date
            or not manifest.get("history_max_source_date", "99999999") < target_date
            or manifest.get("same_day_or_future_history_used") is not False):
        raise ValueError("future-daily-point-in-time-proof-invalid")
    # Only location/preparation timestamp should differ from the isolated
    # standard run. Both state bodies must be byte-identical.
    for name in ("state_sha256", "state_content_sha256", "bloodline_sha256", "snapshot_sha256",
                 "history_rows", "history_races", "history_max_source_date", "clean_history_sha256",
                 "repair_manifest_sha256", "feature_state_module_sha256"):
        if manifest.get(name) != expected.get(name):
            raise ValueError("future-daily-differs-from-isolated-standard-result:" + name)
    for filename, field in (("history_state.json", "state_sha256"), ("prior_bloodlines.json", "bloodline_sha256")):
        actual = Path(target_dir) / filename
        if preparation.file_hash(actual) != manifest[field]:
            raise ValueError("future-daily-body-hash-mismatch:" + filename)
    if Path(manifest["state_path"]).resolve() != (Path(target_dir) / "history_state.json").resolve():
        raise ValueError("future-daily-state-path-mismatch")
    if Path(manifest["bloodline_path"]).resolve() != (Path(target_dir) / "prior_bloodlines.json").resolve():
        raise ValueError("future-daily-bloodline-path-mismatch")
    state = preparation.read_json(Path(target_dir) / "history_state.json")
    if state["state_sha256"] != _hash(state["state"]) or state["state_sha256"] != manifest["state_content_sha256"]:
        raise ValueError("future-daily-state-content-integrity-failed")
    return manifest


def publish_revision(*, revision_dir, expected_manifest_sha256, publish=False,
                     runtime_root=preparation.RUNTIME_ROOT, now=None):
    instant = lambda: now or datetime.now(UTC)
    # Cheap schedule checks precede verification's physical snapshot hash.
    # A dry-run must not monopolize I/O during a natural capture window either.
    preview = preparation.read_json(Path(revision_dir) / "revision_receipt.json")
    publication_clock(preview["target_date"], now=instant())
    target_paths(runtime_root, preview["target_date"], require_absent_directory=True)
    preparation.capture_idle_receipt(runtime_root, now=instant())
    verified = verify_revision(revision_dir, expected_manifest_sha256=expected_manifest_sha256, runtime_root=runtime_root)
    receipt, target = verified["receipt"], verified["receipt"]["target_date"]
    publication_clock(target, now=instant())
    target_dir, binding = target_paths(runtime_root, target, require_absent_directory=True)
    idle = preparation.capture_idle_receipt(runtime_root, now=instant())
    today = instant().astimezone(preparation.JST).strftime("%Y%m%d")
    if today + ".json" not in verified["protected"]["daily_bindings"]:
        raise ValueError("current-day-existing-binding-protection-required")
    result = {"schema": SCHEMA, "operation": "dry_run", "target_date": target,
        "revision_directory": str(verified["directory"]), "runtime_target_directory": str(target_dir),
        "revision_manifest_sha256": expected_manifest_sha256, "protected_before": verified["protected"],
        "source_revision": verified["source_revision"], "capture_idle": idle,
        "existing_binding_replaced": False, "model_or_protocol_changed": False,
        "network": False, "training": False, "real_purchase": False,
        "publication_performed": False}
    if not publish:
        return result
    resources = preparation.reserve_receipt(receipt["source_database"], target_dir.parent)
    if not resources["sufficient"]:
        raise ValueError("standard-memory-and-disk-reserve-required-for-publication")
    record_dir = verified["directory"].parent / (verified["directory"].name + "-publication-" + uuid.uuid4().hex)
    record_dir.mkdir(exist_ok=False)
    preparation.write_new_json(record_dir / "publication_request.json", result)
    # Reserve the exact absent directory ourselves. The frozen helper accepts
    # an existing empty directory. A future day cannot be loaded by the current
    # collector; target/date checks are repeated before final manifest publish.
    target_dir.mkdir(exist_ok=False)
    identity = (target_dir.stat().st_dev, target_dir.stat().st_ino)
    manifest_path = target_dir / "preparation_manifest.json"
    held_path = None
    try:
        from forward_runtime.data_quality import prepare_daily_history
        prepare_daily_history(verified["directory"] / "clean", target_dir, target_date=target)
        held_path = hold_completion_manifest(manifest_path, record_dir, owned_identity=identity)
        manifest = verify_generated_daily(target_dir, held_path, verified["daily"], target_date=target)
        publication_clock(target, now=instant())
        target_paths(runtime_root, target, require_absent_directory=False)
        owned_directory(target_dir, identity)
        preparation.capture_idle_receipt(runtime_root, now=instant())
        after = preparation.protection_receipt(runtime_root=runtime_root)
        if after != verified["protected"]:
            raise ValueError("protected-model-protocol-or-old-binding-changed-during-publication")
        if preparation.source_epoch_receipt(receipt["source_database"]) != verified["source_revision"]:
            raise ValueError("source-changed-during-publication")
        # Exclusive create is the only point at which the frozen runtime sees
        # a completed target-day manifest again. The held original is retained.
        body = held_path.read_bytes()
        with manifest_path.open("xb") as stream:
            stream.write(body)
            stream.flush()
            os.fsync(stream.fileno())
        if preparation.file_hash(manifest_path) != preparation.file_hash(held_path):
            raise ValueError("published-completion-manifest-hash-mismatch")
        if preparation.source_epoch_receipt(receipt["source_database"]) != verified["source_revision"]:
            raise ValueError("source-changed-at-publication-boundary")
        publication_clock(target, now=instant())
        target_paths(runtime_root, target, require_absent_directory=False)
        if preparation.protection_receipt(runtime_root=runtime_root) != verified["protected"]:
            raise ValueError("protected-state-changed-at-publication-boundary")
        result.update(operation="published_next_day_append_only", publication_performed=True,
            protected_after=after, protected_state_unchanged=True,
            published_manifest_sha256=preparation.file_hash(manifest_path),
            published_manifest=manifest, receipt_directory=str(record_dir),
            completed_at_utc=datetime.now(UTC).isoformat(), collector_restart_required=False,
            collection_adoption="Existing collector loads this next-day manifest naturally; today and previous bindings remain unchanged.")
        preparation.write_new_json(record_dir / "publication_receipt.json", result)
        return result
    except Exception as exc:
        # Never erase partial state. If OUR new completion manifest is present,
        # keep it recoverably outside runtime so failure cannot be auto-adopted.
        if manifest_path.exists():
            owned_directory(target_dir, identity)
            failure_manifest = record_dir / ("failed_visible_manifest_" + uuid.uuid4().hex + ".json")
            manifest_path.rename(failure_manifest)
        preparation.write_new_json(record_dir / "publication_failure.json", {"schema": SCHEMA,
            "operation": "failed_no_completed_runtime_manifest", "reason": str(exc),
            "runtime_partial_directory_retained": str(target_dir),
            "held_manifest_retained": str(held_path) if held_path else None,
            "existing_artifacts_removed": False, "old_bindings_modified": False,
            "recorded_at_utc": datetime.now(UTC).isoformat()})
        raise


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--revision-dir", required=True, type=Path)
    parser.add_argument("--expected-manifest-sha256", required=True)
    parser.add_argument("--publish", action="store_true")
    args = parser.parse_args()
    result = publish_revision(revision_dir=args.revision_dir,
        expected_manifest_sha256=args.expected_manifest_sha256, publish=args.publish)
    print(json.dumps({key: result.get(key) for key in ("schema", "operation", "target_date",
        "runtime_target_directory", "publication_performed", "receipt_directory", "collector_restart_required")},
        ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
