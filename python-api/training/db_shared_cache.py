"""Verified raw-feature reuse for the normal local DB training workflow.

Only readonly, checkpointed snapshots and raw point-in-time columns are cached.
TRAIN encoders, fitted baselines, model selection, CAL and TEST remain fresh.
Entries are immutable generations; an atomic pointer exposes only complete
validated generations. Rejected entries are retained, never recursively deleted.
"""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import re
import time
from typing import Callable
from uuid import uuid4

import pandas as pd

from training import db_shared_features as shared
from training import db_shared_fit as fit


CACHE_SCHEMA = "local-db-shared-raw47-cache-v1"
BENCHMARK_SCHEMA = "full-db-shared-raw47-cache-v1"
HISTORY_POLICY = "source_race_date < target_race_date; same-day outcomes excluded"
_IMPORT_ROOT = Path(__file__).resolve().parents[2] / "reports" / "generated"
_SHA = re.compile(r"[0-9a-f]{64}")
_GENERATION = re.compile(r"generation-[0-9a-f]{32}")


def file_hash(path: Path) -> str:
    digest = hashlib.sha256()
    with Path(path).open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def canonical_hash(value) -> str:
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":"),
                                     ensure_ascii=False, allow_nan=False).encode()).hexdigest()


def _plain(path: Path, *, directory: bool | None = None) -> Path:
    """Do not let cache-owned filenames redirect writes through links/junctions."""
    path = Path(path).absolute()
    for parent in (path, *path.parents):
        if parent.is_symlink():
            raise ValueError("shared-cache-linked-path-not-allowed")
        if parent.exists():
            stat = parent.lstat()
            if getattr(stat, "st_file_attributes", 0) & 0x400:
                raise ValueError("shared-cache-linked-path-not-allowed")
    if path.exists() and directory is not None and path.is_dir() != directory:
        raise ValueError("shared-cache-path-type-invalid")
    return path


def _snapshot(path) -> Path:
    value = Path(path).resolve(strict=True)
    if not value.is_file():
        raise ValueError("shared-cache-snapshot-file-required")
    for suffix in ("-wal", "-journal"):
        sidecar = Path(str(value) + suffix)
        if sidecar.exists() and sidecar.stat().st_size:
            raise ValueError("shared-cache-checkpointed-snapshot-required")
    return value


def _dates(date_from, date_to) -> list[str]:
    lower, upper = shared._period_date(date_from, end=False), shared._period_date(date_to, end=True)
    if lower < "20130101" or lower > upper:
        raise ValueError("shared-cache-period-outside-contract")
    return [lower, upper]


def _feature_dtypes() -> list[str]:
    return ["string" if name in fit.CATEGORICAL_COLUMNS else "float32" for name in fit.FEATURE_COLUMNS]


def _key(snapshot_sha, period, sources) -> dict:
    return {"schema": CACHE_SCHEMA, "snapshot_sha256": snapshot_sha, "period": period,
            "source_hashes": sources, "feature_pipeline": fit.FEATURE_PIPELINE,
            "feature_columns": list(fit.FEATURE_COLUMNS), "feature_dtypes": _feature_dtypes(),
            "categorical_columns": list(fit.CATEGORICAL_COLUMNS), "history_policy": HISTORY_POLICY}


def _source_guard(expected=None) -> dict:
    current = fit.source_hashes()
    if expected is not None and current != expected:
        raise ValueError("shared-cache-expected-source-hash-mismatch")
    if not current or any(not isinstance(value, str) or not _SHA.fullmatch(value) for value in current.values()):
        raise ValueError("shared-cache-source-hash-invalid")
    return current


def _audit(features: pd.DataFrame) -> dict:
    value = {name: features.attrs.get(name) for name in
             ("current_outcomes_consumed", "same_day_or_future_history_used", "point_in_time_audit")}
    _validate_audit(value)
    return value


def _validate_audit(value: dict) -> None:
    detail = value.get("point_in_time_audit") if isinstance(value, dict) else None
    if (not isinstance(detail, dict) or value.get("current_outcomes_consumed") is not False
            or value.get("same_day_or_future_history_used") is not False
            or detail.get("strict_predicate") != "source_race_date < target_race_date"
            or detail.get("future_leakage_rows") != 0 or detail.get("same_day_future_references") != 0
            or detail.get("same_day_results_excluded") is not True):
        raise ValueError("shared-cache-point-in-time-proof-invalid")


def _receipt(frame, path) -> dict:
    return {"path": path.name, "sha256": file_hash(path), "frame_digest": fit._frame_digest(frame),
            "rows": len(frame), "columns": list(frame.columns), "dtypes": [str(value) for value in frame.dtypes]}


def _validate_inputs(target, features, coverage, key):
    if not isinstance(coverage, dict):
        raise ValueError("shared-cache-coverage-required")
    before = (fit._frame_digest(target), fit._frame_digest(features))
    prepared, raw = fit._prepare(target, features)
    if before != (fit._frame_digest(prepared), fit._frame_digest(raw)):
        raise ValueError("shared-cache-canonical-order-required")
    if [str(value) for value in raw.dtypes] != key["feature_dtypes"]:
        raise ValueError("shared-cache-raw47-dtype-contract-mismatch")
    lower, upper = key["period"]
    source_hashes = coverage.get("source_hashes")
    target_coverage = coverage.get("target")
    if (coverage.get("feature_version") != fit.FEATURE_PIPELINE
            or [coverage.get("date_from"), coverage.get("date_to")] != key["period"]
            or not isinstance(source_hashes, dict) or source_hashes != shared.source_hashes()
            or coverage.get("complete_career_coverage_verified") is not False
            or not isinstance(target_coverage, dict) or target_coverage.get("eligible_rows") != len(target)
            or target_coverage.get("eligible_races") != int(target.race_id.nunique())
            or target.race_date.lt(lower).any() or target.race_date.gt(upper).any()):
        raise ValueError("shared-cache-cohort-or-coverage-lineage-mismatch")
    return prepared, raw


def _restore_attrs(features, audit):
    features.attrs = {**audit, "feature_version": shared.FEATURE_VERSION,
                      "source_hashes": shared.source_hashes(), "complete_career_coverage_verified": False}


def _read_generation(directory: Path, key, expected_manifest_sha=None):
    directory = _plain(directory, directory=True)
    manifest_path = _plain(directory / "manifest.json", directory=False)
    original = manifest_path.read_bytes()
    digest = hashlib.sha256(original).hexdigest()
    if expected_manifest_sha is not None and digest != expected_manifest_sha:
        raise ValueError("shared-cache-manifest-hash-mismatch")
    manifest = json.loads(original)
    if (not isinstance(manifest, dict) or manifest.get("schema") != CACHE_SCHEMA or manifest.get("key_payload") != key
            or manifest.get("key_sha256") != canonical_hash(key)):
        raise ValueError("shared-cache-key-lineage-mismatch")
    audit = manifest.get("point_in_time_audit")
    _validate_audit(audit)
    files = manifest.get("files")
    if not isinstance(files, dict) or set(files) != {"target", "features"}:
        raise ValueError("shared-cache-files-manifest-invalid")
    frames = {}
    for name in ("target", "features"):
        expected = files[name]
        if not isinstance(expected, dict) or expected.get("path") != f"{name}.parquet":
            raise ValueError("shared-cache-frame-path-invalid")
        path = _plain(directory / f"{name}.parquet", directory=False)
        if file_hash(path) != expected.get("sha256"):
            raise ValueError("shared-cache-frame-file-hash-mismatch")
        frame = pd.read_parquet(path)
        if _receipt(frame, path) != expected:
            raise ValueError("shared-cache-frame-schema-or-digest-mismatch")
        frames[name] = frame
    target, features = _validate_inputs(frames["target"], frames["features"], manifest.get("coverage"), key)
    if (manifest.get("runner_count") != len(target) or manifest.get("race_count") != int(target.race_id.nunique())
            or manifest.get("actual_date_from") != str(target.race_date.min())
            or manifest.get("actual_date_to") != str(target.race_date.max())
            or type(manifest.get("stored_bytes")) is not int
            or manifest["stored_bytes"] != sum((directory / row["path"]).stat().st_size for row in files.values())
            or manifest_path.read_bytes() != original):
        raise ValueError("shared-cache-manifest-cohort-or-mutation-mismatch")
    _restore_attrs(features, audit)
    return target, features, manifest, digest


def _read_entry(root: Path, key):
    entry = _plain(root / canonical_hash(key), directory=True)
    pointer = json.loads(_plain(entry / "current.json", directory=False).read_text(encoding="utf-8"))
    if (set(pointer) != {"schema", "key_sha256", "generation", "manifest_sha256"}
            or pointer["schema"] != CACHE_SCHEMA or pointer["key_sha256"] != canonical_hash(key)
            or not isinstance(pointer["generation"], str) or not _GENERATION.fullmatch(pointer["generation"])
            or not isinstance(pointer["manifest_sha256"], str) or not _SHA.fullmatch(pointer["manifest_sha256"])):
        raise ValueError("shared-cache-atomic-pointer-invalid")
    return _read_generation(entry / pointer["generation"], key, pointer["manifest_sha256"])


def _write_json(path, value):
    with _plain(path, directory=False).open("x", encoding="utf-8") as stream:
        json.dump(value, stream, ensure_ascii=False, sort_keys=True, indent=2, allow_nan=False)
        stream.flush()
        os.fsync(stream.fileno())


def _publish(root, key, target, features, coverage, *, imported_from=None):
    target, features = _validate_inputs(target, features, coverage, key)
    audit = _audit(features)
    entry = _plain(root / canonical_hash(key), directory=True)
    entry.mkdir(parents=True, exist_ok=True)
    token = uuid4().hex
    staged = _plain(entry / f".staging-{token}", directory=True)
    staged.mkdir()
    files = {}
    for name, frame in (("target", target), ("features", features)):
        stored = frame.copy()
        stored.attrs = {}
        path = staged / f"{name}.parquet"
        with path.open("xb") as stream:
            stored.to_parquet(stream, index=False)
            stream.flush()
            os.fsync(stream.fileno())
        files[name] = _receipt(frame, path)
    manifest = {"schema": CACHE_SCHEMA, "key_payload": key, "key_sha256": canonical_hash(key),
                "files": files, "coverage": coverage, "point_in_time_audit": audit,
                "actual_date_from": str(target.race_date.min()), "actual_date_to": str(target.race_date.max()),
                "runner_count": len(target), "race_count": int(target.race_id.nunique()),
                "created_at_unix": time.time(), "imported_from": imported_from,
                "stored_bytes": sum((staged / row["path"]).stat().st_size for row in files.values()),
                "contents": "raw47 and targets only; no fitted encoder/baseline/model/calibrator"}
    _write_json(staged / "manifest.json", manifest)
    # Re-read on-disk parquet before admission, including dtype and NaN parity.
    _read_generation(staged, key)
    generation = f"generation-{token}"
    os.replace(staged, _plain(entry / generation, directory=True))
    manifest_sha = file_hash(entry / generation / "manifest.json")
    pointer = {"schema": CACHE_SCHEMA, "key_sha256": canonical_hash(key),
               "generation": generation, "manifest_sha256": manifest_sha}
    pending = entry / f".pointer-{token}.json"
    _write_json(pending, pointer)
    os.replace(pending, _plain(entry / "current.json", directory=False))
    return manifest, manifest_sha


def _complete_guard(snapshot, snapshot_sha, sources):
    if file_hash(_snapshot(snapshot)) != snapshot_sha:
        raise ValueError("shared-cache-snapshot-changed-during-preparation")
    if _source_guard(sources) != sources:
        raise ValueError("shared-cache-source-changed-during-preparation")


def _output_receipt(key, manifest, manifest_sha, status, stages, started, *, rejected=None):
    return {"schema": "local-db-shared-cache-receipt-v1", "cache_status": status,
            "cache_hit": status == "hit", "cache_key_sha256": canonical_hash(key),
            "cache_manifest_sha256": manifest_sha, "snapshot_sha256": key["snapshot_sha256"],
            "requested_period": key["period"], "actual_period": [manifest["actual_date_from"], manifest["actual_date_to"]],
            "source_hashes": key["source_hashes"], "feature_count": len(key["feature_columns"]),
            "runner_count": manifest["runner_count"], "race_count": manifest["race_count"],
            "input_hashes": {name: row["frame_digest"] for name, row in manifest["files"].items()},
            "stage_seconds": stages, "preparation_seconds": time.perf_counter() - started,
            "reused_bytes": manifest.get("stored_bytes", 0) if status == "hit" else 0,
            "rejected_cache_reason": rejected, "snapshot_unchanged": True, "source_unchanged": True,
            "point_in_time_validated": True, "train_encoder_reused": False,
            "fitted_baseline_reused": False, "calibrator_reused": False,
            "complete_career_coverage_verified": False}


def prepare_shared_inputs(snapshot_path, date_from, date_to, cache_root,
                          progress: Callable[[str, int], None] | None = None,
                          expected_source_hashes: dict | None = None,
                          reuse_features: bool = True):
    """Return (target, raw47, coverage, receipt), admitting only exact lineage.

    Cache corruption/mismatched metadata is recorded, then fresh readonly
    preparation replaces its pointer. No rejected or user source files are deleted.
    """
    started = time.perf_counter()
    if type(reuse_features) is not bool:
        raise ValueError("shared-cache-reuse-features-boolean-required")
    period, snapshot = _dates(date_from, date_to), _snapshot(snapshot_path)
    root = _plain(Path(cache_root), directory=True)
    if root == Path(root.anchor) or snapshot.is_relative_to(root):
        raise ValueError("shared-cache-snapshot-and-cache-must-be-separated")
    root.mkdir(parents=True, exist_ok=True)
    sources = _source_guard(expected_source_hashes)
    clock = time.perf_counter()
    snapshot_sha = file_hash(snapshot)
    stages = {"initial_snapshot_hash": time.perf_counter() - clock}
    key = _key(snapshot_sha, period, sources)
    rejected = None
    clock = time.perf_counter()
    if reuse_features:
        try:
            target, features, manifest, manifest_sha = _read_entry(root, key)
            status = "hit"
            stages["cache_validate_read"] = time.perf_counter() - clock
            if progress:
                progress("保存済みの共通特徴量を再利用", 3)
        except FileNotFoundError:
            status = "miss"
            if (root / canonical_hash(key)).exists():
                status, rejected = "rebuilt", "shared-cache-incomplete-entry"
        except (ValueError, KeyError, TypeError, OSError) as error:
            status, rejected = "rebuilt", str(error)
    else:
        status = "refresh"
    if status != "hit":
        if progress:
            progress("保存済みDBから共通特徴量を生成", 1)
        clock = time.perf_counter()
        target, history, coverage = shared.load_db_frames(snapshot, *period)
        stages["db_read"] = time.perf_counter() - clock
        clock = time.perf_counter()
        features = shared.build_training_features(target, history)
        del history
        stages["feature_generation"] = time.perf_counter() - clock
        clock = time.perf_counter()
        target, features = fit._prepare(target, features)
        _audit(features)
        stages["canonical_prepare"] = time.perf_counter() - clock
        _complete_guard(snapshot, snapshot_sha, sources)
        clock = time.perf_counter()
        manifest, manifest_sha = _publish(root, key, target, features, coverage)
        stages["cache_publish_verify"] = time.perf_counter() - clock
    coverage = manifest["coverage"]
    clock = time.perf_counter()
    _complete_guard(snapshot, snapshot_sha, sources)
    stages["final_integrity_guard"] = time.perf_counter() - clock
    receipt = _output_receipt(key, manifest, manifest_sha, status, stages, started, rejected=rejected)
    receipt["reuse_requested"] = reuse_features
    return target, features, coverage, receipt


def import_shared_cache(snapshot_path, date_from, date_to, cache_root, source_directory,
                        *, expected_source_hashes: dict | None = None):
    """Explicit one-time admission of a fully verified benchmark raw47 cache.

    Only repository research parquet is accepted; model/pickle inputs are never
    deserialized. This is not automatic broad discovery of historical directories.
    """
    started = time.perf_counter()
    period, snapshot = _dates(date_from, date_to), _snapshot(snapshot_path)
    sources = _source_guard(expected_source_hashes)
    snapshot_sha = file_hash(snapshot)
    root = _plain(Path(cache_root), directory=True)
    if root == Path(root.anchor) or snapshot.is_relative_to(root):
        raise ValueError("shared-cache-snapshot-and-cache-must-be-separated")
    source = _plain(Path(source_directory), directory=True).resolve(strict=True)
    if not source.is_relative_to(_IMPORT_ROOT.resolve()) or source == _IMPORT_ROOT.resolve():
        raise ValueError("shared-cache-import-research-boundary-required")
    root.mkdir(parents=True, exist_ok=True)
    key = _key(snapshot_sha, period, sources)
    manifest_path = _plain(source / "manifest.json", directory=False)
    original = manifest_path.read_bytes()
    manifest = json.loads(original)
    if not isinstance(manifest, dict):
        raise ValueError("shared-cache-benchmark-import-manifest-invalid")
    old_key = manifest.get("key_payload")
    if (not isinstance(old_key, dict) or manifest.get("schema") != BENCHMARK_SCHEMA
            or manifest.get("key_sha256") != canonical_hash(old_key)
            or old_key != {"schema": BENCHMARK_SCHEMA, "snapshot_sha256": snapshot_sha,
                           "period": manifest.get("period"), "source_hashes": sources,
                           "feature_columns": list(fit.FEATURE_COLUMNS), "categorical_columns": list(fit.CATEGORICAL_COLUMNS),
                           "history_policy": HISTORY_POLICY}
            or _dates(*manifest.get("period", [])) != period
            or manifest.get("snapshot_sha256") != snapshot_sha or manifest.get("source_hashes") != sources
            or manifest.get("feature_columns") != list(fit.FEATURE_COLUMNS)
            or manifest.get("categorical_columns") != list(fit.CATEGORICAL_COLUMNS)
            or manifest.get("history_policy") != HISTORY_POLICY):
        raise ValueError("shared-cache-benchmark-import-lineage-mismatch")
    _validate_audit(manifest.get("point_in_time_audit"))
    if not isinstance(manifest.get("files"), dict) or set(manifest["files"]) != {"target", "features"}:
        raise ValueError("shared-cache-benchmark-import-files-invalid")
    frames = {}
    for name in ("target", "features"):
        expected = manifest.get("files", {}).get(name)
        if not isinstance(expected, dict) or expected.get("path") != f"{name}.parquet":
            raise ValueError("shared-cache-benchmark-import-path-invalid")
        path = _plain(source / expected["path"], directory=False)
        if file_hash(path) != expected.get("sha256"):
            raise ValueError("shared-cache-benchmark-import-file-hash-mismatch")
        frame = pd.read_parquet(path)
        if _receipt(frame, path) != expected:
            raise ValueError("shared-cache-benchmark-import-frame-mismatch")
        frames[name] = frame
    target, features = _validate_inputs(frames["target"], frames["features"], manifest.get("coverage"), key)
    _restore_attrs(features, manifest["point_in_time_audit"])
    if manifest_path.read_bytes() != original:
        raise ValueError("shared-cache-benchmark-import-source-mutated")
    _complete_guard(snapshot, snapshot_sha, sources)
    normal, normal_sha = _publish(root, key, target, features, manifest["coverage"],
                                  imported_from={"path": str(source), "manifest_sha256": hashlib.sha256(original).hexdigest()})
    _complete_guard(snapshot, snapshot_sha, sources)
    return _output_receipt(key, normal, normal_sha, "imported", {}, started)
