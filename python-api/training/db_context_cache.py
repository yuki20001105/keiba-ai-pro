"""Own atomic generations for context features; old raw47 caches are untouched."""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import time
import uuid

import pandas as pd

SCHEMA = "local-db-context-cache-v1"


def canonical_hash(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False,
        separators=(",", ":"), allow_nan=False).encode()).hexdigest()


def _plain(path):
    value = Path(path).absolute()
    if value.is_symlink() or any(parent.is_symlink() for parent in value.parents):
        raise ValueError("context-cache-linked-path-rejected")
    return value.resolve()


def _write(path, body):
    with Path(path).open("x", encoding="utf-8") as stream:
        json.dump(body, stream, indent=2, ensure_ascii=False, allow_nan=False)


def _snapshot_guard(snapshot):
    if not _plain(snapshot).is_file():
        raise ValueError("context-cache-existing-snapshot-required")
    for suffix in ("-wal", "-journal"):
        sidecar = Path(str(snapshot)+suffix)
        if sidecar.exists() and sidecar.stat().st_size:
            raise ValueError("context-cache-checkpointed-snapshot-required")


def _audit(features):
    from training.db_shared_cache import _validate_audit
    from training.db_context_features import feature_source_hashes, FEATURE_PIPELINE
    _validate_audit(features.attrs)
    context = features.attrs.get("context_audit")
    if (features.attrs.get("current_outcomes_consumed") is not False
            or features.attrs.get("same_day_or_future_history_used") is not False
            or not isinstance(context, dict)
            or context.get("strict_predicate") != "history_race_date < target_race_date"
            or context.get("same_day_results_used") is not False
            or context.get("target_corners_or_outcomes_consumed") is not False):
        raise ValueError("context-cache-strict-point-in-time-audit-required")
    if (features.attrs.get("source_hashes") != feature_source_hashes()
            or features.attrs.get("feature_version") != FEATURE_PIPELINE):
        raise ValueError("context-cache-feature-audit-source-mismatch")
    return dict(features.attrs)


def _read(root, key, fit):
    entry = _plain(root / canonical_hash(key))
    pointer = json.loads((entry / "current.json").read_text(encoding="utf-8"))
    if not isinstance(pointer, dict) or set(pointer) != {"schema", "generation", "manifest_sha256"}:
        raise ValueError("context-cache-pointer-shape-invalid")
    generation = pointer.get("generation", "")
    if (pointer.get("schema") != SCHEMA or not isinstance(generation, str)
            or len(generation) != 32 or any(c not in "0123456789abcdef" for c in generation)):
        raise ValueError("context-cache-pointer-invalid")
    directory = _plain(entry / generation)
    manifest_path = directory / "manifest.json"
    if fit.file_hash(manifest_path) != pointer.get("manifest_sha256"):
        raise ValueError("context-cache-manifest-hash-invalid")
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    if not isinstance(manifest, dict) or manifest.get("schema") != SCHEMA or manifest.get("key") != key:
        raise ValueError("context-cache-lineage-invalid")
    frames = {}
    if not isinstance(manifest.get("files"), dict) or set(manifest["files"]) != {"target", "features"}:
        raise ValueError("context-cache-frame-set-invalid")
    for name in ("target", "features"):
        path = _plain(directory / (name + ".parquet"))
        receipt = manifest["files"][name]
        if not isinstance(receipt, dict) or fit.file_hash(path) != receipt.get("sha256"):
            raise ValueError("context-cache-frame-hash-invalid")
        frame = pd.read_parquet(path)
        if (fit.legacy._frame_digest(frame) != receipt.get("frame_digest")
                or list(frame.columns) != receipt.get("columns") or len(frame) != receipt.get("rows")
                or [str(dtype) for dtype in frame.dtypes] != receipt.get("dtypes")):
            raise ValueError("context-cache-frame-schema-invalid")
        frames[name] = frame
    initial = {name: fit.legacy._frame_digest(frame) for name, frame in frames.items()}
    target, features = fit.prepare(frames["target"], frames["features"])
    if initial != {"target": fit.legacy._frame_digest(target), "features": fit.legacy._frame_digest(features)}:
        raise ValueError("context-cache-canonical-order-changed")
    coverage = manifest.get("coverage")
    from training.db_context_features import feature_source_hashes
    if (not isinstance(coverage, dict) or coverage.get("feature_version") != fit.FEATURE_PIPELINE
            or [coverage.get("date_from"), coverage.get("date_to")] != key["period"]
            or coverage.get("source_hashes") != feature_source_hashes()
            or not isinstance(coverage.get("target"), dict)
            or coverage["target"].get("eligible_rows") != len(target)
            or coverage["target"].get("eligible_races") != int(target.race_id.nunique())
            or not target.race_date.between(*key["period"]).all()
            or [str(dtype) for dtype in features.dtypes] != key["feature_dtypes"]):
        raise ValueError("context-cache-coverage-period-dtype-mismatch")
    features.attrs = manifest["feature_audit"]
    _audit(features)
    return target, features, manifest, pointer["manifest_sha256"]


def prepare_context_inputs(snapshot_path, date_from, date_to, cache_root, *, progress=None,
                           expected_source_hashes=None, reuse_features=True):
    from training import db_context_fit as fit
    from training import db_context_features as source
    from training import db_shared_features as shared
    if type(reuse_features) is not bool:
        raise ValueError("context-cache-reuse-boolean-required")
    started = time.perf_counter()
    snapshot = _plain(snapshot_path)
    _snapshot_guard(snapshot)
    root = _plain(Path(cache_root) / "context-v1")
    if root == Path(root.anchor) or snapshot.is_relative_to(root):
        raise ValueError("context-cache-independent-snapshot-required")
    root.mkdir(parents=True, exist_ok=True)
    sources = fit.source_hashes()
    if expected_source_hashes is not None and expected_source_hashes != sources:
        raise ValueError("context-cache-expected-source-mismatch")
    period = [shared._period_date(date_from, end=False), shared._period_date(date_to, end=True)]
    if period[0] < "20130101" or period[0] > period[1]:
        raise ValueError("context-cache-period-invalid")
    snapshot_sha = fit.file_hash(snapshot)
    key = {"schema": SCHEMA, "snapshot_sha256": snapshot_sha, "period": period,
        "source_hashes": sources, "feature_pipeline": fit.FEATURE_PIPELINE,
        "feature_dtypes": ["string" if name in fit.CATEGORICAL_COLUMNS else "float32" for name in fit.FEATURE_COLUMNS],
        "feature_columns": list(fit.FEATURE_COLUMNS), "history_policy": "all eligible JRA; source date strictly before target"}
    status, rejected, stages = ("refresh" if not reuse_features else "miss"), None, {}
    if reuse_features:
        clock = time.perf_counter()
        try:
            target, features, manifest, manifest_sha = _read(root, key, fit)
            status = "hit"
        except FileNotFoundError:
            pass
        except (OSError, ValueError, KeyError, TypeError) as error:
            status, rejected = "rebuilt", type(error).__name__
        stages["cache_read"] = time.perf_counter()-clock
    if status != "hit":
        if progress:
            progress("全中央平地履歴から条件・血統・展開を生成", 1)
        clock = time.perf_counter()
        target, history, coverage = source.load_db_frames(snapshot, *period)
        stages["db_read"] = time.perf_counter()-clock
        clock = time.perf_counter()
        features = source.build_training_features(target, history)
        del history
        stages["feature_generation"] = time.perf_counter()-clock
        audit = _audit(features)
        target, features = fit.prepare(target, features)
        features.attrs = audit
        _snapshot_guard(snapshot)
        if fit.file_hash(snapshot) != snapshot_sha or fit.source_hashes() != sources:
            raise ValueError("context-cache-source-or-snapshot-changed")
        entry = root / canonical_hash(key)
        generation = uuid.uuid4().hex
        directory = _plain(entry / generation)
        directory.mkdir(parents=True, exist_ok=False)
        files = {}
        for name, frame in (("target", target), ("features", features)):
            path = directory / (name+".parquet")
            frame.to_parquet(path, index=False)
            files[name] = {"sha256": fit.file_hash(path), "frame_digest": fit.legacy._frame_digest(frame),
                "rows": len(frame), "columns": list(frame.columns), "dtypes": [str(dtype) for dtype in frame.dtypes]}
        manifest = {"schema": SCHEMA, "key": key, "files": files, "coverage": coverage, "feature_audit": audit}
        _write(directory / "manifest.json", manifest)
        manifest_sha = fit.file_hash(directory / "manifest.json")
        pending = entry / (".pointer-"+uuid.uuid4().hex+".json")
        _write(pending, {"schema": SCHEMA, "generation": generation, "manifest_sha256": manifest_sha})
        os.replace(pending, entry / "current.json")
        target, features, manifest, manifest_sha = _read(root, key, fit)
    _snapshot_guard(snapshot)
    if fit.file_hash(snapshot) != snapshot_sha or fit.source_hashes() != sources:
        raise ValueError("context-cache-source-or-snapshot-changed")
    receipt = {"schema": SCHEMA, "cache_hit": status == "hit", "cache_status": status,
        "cache_key_sha256": canonical_hash(key), "cache_manifest_sha256": manifest_sha,
        "snapshot_sha256": snapshot_sha, "source_hashes": sources, "stage_seconds": stages,
        "requested_period": period, "actual_period": [str(target.race_date.min()), str(target.race_date.max())],
        "runner_count": len(target), "race_count": int(target.race_id.nunique()),
        "feature_count": len(fit.FEATURE_COLUMNS), "preparation_seconds": time.perf_counter()-started,
        "rejected_cache_reason": rejected, "point_in_time_validated": True,
        "fitted_baseline_reused": False, "train_encoder_reused": False, "calibrator_reused": False}
    return target, features, manifest["coverage"], receipt
