"""Read-only source/model revisions, not proof of pre-race availability."""
from __future__ import annotations

from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import sqlite3
from services.prediction_source_revision import read_source_revision


SCHEMA_VERSION = "prediction-freshness-v1"
API_ROOT = Path(__file__).resolve().parents[1]
ROOT = API_ROOT.parent


def _hash(value) -> str:
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def data_revision(db_path: str | Path) -> str:
    """Prefer source-only generation; absent tracking uses conservative metadata."""
    state = read_source_revision(db_path)
    return _hash([state["data_revision"], _quality_revision(db_path)])


def _quality_revision(db_path):
    """Only history eligibility fields, not job progress or prediction logs."""
    path = Path(db_path).resolve()
    if not path.is_file():
        return None
    try:
        with sqlite3.connect(path.as_uri()+"?mode=ro", uri=True, timeout=2) as connection:
            connection.execute("PRAGMA query_only=ON")
            if not connection.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='scrape_race_acquisition_state'").fetchone():
                return "absent"
            digest = hashlib.sha256()
            for row in connection.execute("SELECT race_id,lifecycle,quality_status,excluded_from_standard FROM scrape_race_acquisition_state ORDER BY race_id"):
                digest.update(json.dumps(row, ensure_ascii=False, separators=(",", ":")).encode()+b"\n")
            return digest.hexdigest()
    except (sqlite3.Error, OSError):
        return None


def model_runtime_revision(model_path: str | Path) -> str:
    """Bind the selected model, calibration/publication and serving code.

    Revision checks never deserialize models, fetch missing artifacts, or
    grant model/purchase authority. Actual inference keeps its signed guards.
    """
    path = Path(model_path).resolve(strict=True)
    files = [path, path.with_suffix(".reference-manifest.json"), path.with_suffix(".betting-validation.json"),
             path.with_suffix(".calibration.json"), Path(str(path) + ".calibration.json"),
             API_ROOT / "routers" / "predict.py", API_ROOT / "services" / "prediction_compute.py",
             API_ROOT / "services" / "prediction_freshness.py",
             API_ROOT / "services" / "prediction_source_revision.py",
             API_ROOT / "training" / "db_context_cache.py",
             API_ROOT / "services" / "db_shared_input.py",
             API_ROOT / "training" / "db_context_registry.py",
             API_ROOT / "training" / "db_context_features.py",
             API_ROOT / "training" / "db_context_fit.py",
             API_ROOT / "services" / "win_nll_reference_input.py",
             API_ROOT / "services" / "local_candidate_input.py",
             API_ROOT / "betting" / "value_policy.py",
             ROOT / "keiba" / "keiba_ai" / "feature_engineering.py",
             ROOT / "keiba" / "keiba_ai" / "db_ultimate_loader.py"]
    values = []
    for candidate in files:
        digest = None
        if candidate.is_file():
            hasher = hashlib.sha256()
            with candidate.open("rb") as stream:
                while chunk := stream.read(1024 * 1024):
                    hasher.update(chunk)
            digest = hasher.hexdigest()
        values.append([str(candidate), digest])
    return _hash(values)


def freshness_metadata(*, db_path: str | Path, model_path: str | Path, source: str,
                       cache_hit: bool = False, initial_revision: str | None = None,
                       generated_at: str | None = None) -> dict:
    state = read_source_revision(db_path)
    quality = _quality_revision(db_path)
    revision = _hash([state["data_revision"], quality])
    return {
        "schema_version": SCHEMA_VERSION,
        "generated_at": generated_at or datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
        "data_revision": revision,
        "model_runtime_revision": model_runtime_revision(model_path),
        "resolved_model_id": Path(model_path).stem,
        "source": source,
        "cache_hit": bool(cache_hit),
        "data_changed_during_prediction": initial_revision is not None and initial_revision != revision,
        "revision_kind": state["revision_kind"],
        "source_tracking": state["source_tracking"] and quality is not None,
        "history_quality_tracking": quality is not None,
        # Revision checks do not create or replace acquisition evidence.
        "source_snapshot_verified": False,
        "capabilities": {"saved_db_recompute": True},
    }
