"""Audited local DB models, separate from legacy and frozen forward adapters."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
import hashlib
import json
from pathlib import Path
import sqlite3

import numpy as np
import pandas as pd
from fastapi import HTTPException
from training.db_shared_profiles import OPTIMIZED_PIPELINE
from training.db_context_registry import CONTEXT_PIPELINE, SHARED_PIPELINES

VERSION = "local-db-shared-v1"
PREFIX = "model_speed_deviation_lightgbm_dbshared_"
ROOT = Path(__file__).resolve().parents[2]
JST = timezone(timedelta(hours=9))


def file_hash(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def validate_published_artifact(path: Path, *, ledger_path: Path | None = None) -> str:
    """Check every admission against the durable publisher BEFORE unpickling.

    Source edits after training are handled separately by the input contract.
    No registry initialization, arbitrary path, or client validation flag.
    """
    from app_config import APP_ENV, MODELS_DIR
    resolved = path.resolve(strict=True)
    if APP_ENV not in {"local", "development", "test"}:
        raise ValueError("shared-db-local-only")
    if path.is_symlink() or resolved.parent != MODELS_DIR.resolve() or not resolved.name.startswith(PREFIX):
        raise ValueError("shared-db-published-path-invalid")
    ledger = ledger_path or ROOT / "python-api/data/local_retrain_jobs.db"
    with sqlite3.connect(f"{ledger.resolve().as_uri()}?mode=ro", uri=True) as db:
        row = db.execute(
            "SELECT p.artifact_sha256 FROM local_retrain_publications p "
            "JOIN local_retrain_jobs j ON j.job_id=p.job_id "
            "WHERE p.published_path=? AND p.state='published' AND j.state='completed'",
            (str(resolved),),
        ).fetchone()
    digest = file_hash(resolved)
    if not row or row[0] != digest:
        raise ValueError("shared-db-published-artifact-hash-mismatch")
    return digest


def verify_bundle_sources(bundle: dict) -> None:
    if bundle.get("feature_pipeline") == CONTEXT_PIPELINE:
        from training import db_context_fit as context
        if (bundle.get("feature_columns") != list(context.FEATURE_COLUMNS)
                or bundle.get("feature_source_hashes") != context.feature_source_hashes()
                or bundle.get("fit_source_sha256") != file_hash(Path(context.__file__))):
            raise ValueError("context-db-feature-contract-source-mismatch")
        context._validate_prediction_bundle(bundle)
        return
    if bundle.get("feature_pipeline") == OPTIMIZED_PIPELINE:
        from training import db_shared_optimized as optimized
        if (bundle.get("feature_columns") != list(optimized.FEATURE_COLUMNS)
                or bundle.get("feature_source_hashes") != optimized.feature_source_hashes()
                or bundle.get("fit_source_sha256") != file_hash(Path(optimized.__file__))):
            raise ValueError("shared-db-feature-contract-source-mismatch")
        optimized._validate_prediction_bundle(bundle)
        return
    from training.db_shared_features import FEATURE_COLUMNS, source_hashes
    if (bundle.get("feature_pipeline") != VERSION
            or bundle.get("feature_columns") != list(FEATURE_COLUMNS)
            or bundle.get("feature_source_hashes") != source_hashes()
            or bundle.get("fit_source_sha256") != file_hash(ROOT / "python-api/training/db_shared_fit.py")):
        raise ValueError("shared-db-feature-contract-source-mismatch")


def _instant(value):
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        return parsed.astimezone(timezone.utc) if parsed.tzinfo else None
    except (TypeError, ValueError):
        return None


def shared_pre_race_input_needs_refresh(
    bundle: dict, race_id: str, info: dict, horse_rows, *,
    trusted_db_card: bool, now: datetime | None = None,
) -> bool:
    """Refresh stale current DB cards, never retrofit past/result inputs.

    The caller must first admit the selected artifact through its publisher
    SHA check. A client flag, candidate name or timestamp alone is no authority.
    This predicate only adds a refresh reason; source timestamps are supplied
    by the existing actual-fetch snapshot binder, never changed here.
    """
    if trusted_db_card is not True or bundle.get("feature_pipeline") not in SHARED_PIPELINES:
        return False
    verify_bundle_sources(bundle)
    from services.race_snapshot import is_trustworthy_race_date

    now = now or datetime.now(timezone.utc)
    if now.tzinfo is None:
        return False
    now = now.astimezone(timezone.utc)
    dates = {str(info[key]).strip().replace("/", "").replace("-", "")
             for key in ("date", "race_date") if info.get(key) not in (None, "")}
    if len(dates) != 1:
        return False
    date = next(iter(dates))
    if not is_trustworthy_race_date(race_id, date):
        return False
    try:
        post = datetime.strptime(date + " " + str(info.get("post_time", "")),
                                 "%Y%m%d %H:%M").replace(tzinfo=JST)
    except ValueError:
        return False
    if post.date() != now.astimezone(JST).date() or now >= post:
        return False
    try:
        horses = [json.loads(row[0]) if isinstance(row, (tuple, list)) else row
                  for row in horse_rows]
    except (TypeError, ValueError, IndexError):
        return False  # Existing incomplete-snapshot repair handles these rows.
    if not horses or any(not isinstance(row, dict) for row in horses):
        return False
    # Inspect aliases independently: finish_position=None must not hide finish=1.
    # Result pages can also carry a time without a parsed finishing position.
    for row in [info, *horses]:
        if (row.get("odds_status") in {"result", "settled"}
                or any(row.get(key) not in (None, "", 0, "0")
                       for key in ("finish_position", "finish", "finish_time", "time", "time_seconds"))):
            return False
        if row.get("race_id") not in (None, "", race_id):
            return False
    observed = [_instant(row.get("data_observed_at")) for row in [info, *horses]]
    return any(value is None or value > now or now - value > timedelta(minutes=5)
               or value >= post for value in observed)


def record_pre_race_input(
    *, info: dict, horses, features, diagnostics: dict, model_path: Path,
    trusted_context: dict | None, receipt_path: Path | None = None,
    now: datetime | None = None,
) -> dict:
    """Bind the exact DB input at prediction time; never backdate a receipt.

    This proves a local pre-race prediction input, not historical HTML capture
    or paid-source provenance. A past race/client-supplied card cannot qualify.
    """
    unavailable = {"verified": False, "reason": "pre_race_db_capture_unverified"}
    context = trusted_context or {}
    if context.get("trusted_db_card") is not True or context.get("race_has_result") is not False:
        return unavailable
    now = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)
    try:
        date = str(info.get("date", "")).replace("/", "").replace("-", "")
        post = datetime.strptime(date + " " + str(info.get("post_time", "")), "%Y%m%d %H:%M").replace(tzinfo=JST)
    except ValueError:
        return unavailable
    observed = [_instant(info.get("data_observed_at")), *[
        _instant(row.get("data_observed_at", info.get("data_observed_at")))
        for row in horses.to_dict("records")
    ]]
    if (post.date() != now.astimezone(JST).date() or now >= post
            or not observed or any(value is None or value > now or now - value > timedelta(minutes=5)
                                       or value >= post for value in observed)):
        return unavailable
    if diagnostics.get("issue_codes"):
        return unavailable
    pipeline = diagnostics.get("feature_pipeline", VERSION)
    if pipeline not in SHARED_PIPELINES:
        return unavailable
    model_sha = validate_published_artifact(model_path)
    # JSON null is ordinary missingness, never zero; categories remain strings.
    values = []
    for row in features.to_numpy(dtype=object):
        values.append([None if item is None or pd.isna(item)
                       else item.item() if isinstance(item, np.generic) else item for item in row])
    payload = {
        "schema": "local-db-input-receipt-v1", "feature_pipeline": pipeline,
        "race_id": str(info.get("race_id", "")), "captured_at": now.isoformat(),
        "post_time": post.isoformat(), "model_sha256": model_sha,
        "feature_source_hashes": diagnostics.get("feature_source_hashes"),
        "columns": list(features.columns), "values": values,
        "horse_numbers": [str(row.get("horse_number", "")) for row in horses.to_dict("records")],
        "source_observed_at": [value.isoformat() for value in observed],
        "history_audit": diagnostics.get("history_audit"),
    }
    body = json.dumps(payload, ensure_ascii=False, sort_keys=True, allow_nan=False)
    digest = hashlib.sha256(body.encode()).hexdigest()
    receipt_path = receipt_path or ROOT / "python-api/data/db_shared_input_observations.db"
    receipt_path.parent.mkdir(parents=True, exist_ok=True)
    with sqlite3.connect(str(receipt_path), timeout=5) as db:
        db.executescript("""
            CREATE TABLE IF NOT EXISTS input_receipts (
                receipt_sha256 TEXT PRIMARY KEY, race_id TEXT NOT NULL,
                model_sha256 TEXT NOT NULL, captured_at TEXT NOT NULL,
                post_time TEXT NOT NULL, payload_json TEXT NOT NULL);
            CREATE TRIGGER IF NOT EXISTS receipt_no_update BEFORE UPDATE ON input_receipts
                BEGIN SELECT RAISE(ABORT, 'immutable input receipt'); END;
            CREATE TRIGGER IF NOT EXISTS receipt_no_delete BEFORE DELETE ON input_receipts
                BEGIN SELECT RAISE(ABORT, 'immutable input receipt'); END;
        """)
        db.execute("INSERT OR IGNORE INTO input_receipts VALUES (?,?,?,?,?,?)",
                   (digest, payload["race_id"], model_sha, payload["captured_at"], payload["post_time"], body))
    return {"verified": True, "receipt_sha256": digest,
            "scope": "server DB inputs recorded before post; not historical HTML provenance"}


def client_race_metadata(horses) -> dict:
    """One complete client card may supply metadata, never capture authority."""
    frame = horses if isinstance(horses, pd.DataFrame) else pd.DataFrame(horses)
    keys = ["race_id", "distance", "venue", "num_horses"]
    for choices in (("date", "race_date"), ("track_type", "surface")):
        key = next((name for name in choices if name in frame and frame[name].notna().all()), None)
        if key is None:
            raise ValueError("shared-client-complete-race-metadata-required")
        keys.append(key)
    metadata = {}
    for key in keys + [name for name in ("field_condition", "weather", "race_class") if name in frame]:
        if frame.empty or key not in frame or frame[key].isna().any() or frame[key].astype(str).nunique() != 1:
            raise ValueError("shared-client-single-consistent-race-required")
        metadata[key] = frame[key].iloc[0]
    metadata["date"] = metadata.get("date", metadata.get("race_date"))
    return metadata


def build_shared_input(bundle, horses, *, db_path, race_info, model_path, trusted_context=None):
    from training.db_shared_features import build_serving_features
    verify_bundle_sources(bundle)
    if race_info is None:
        race_info = client_race_metadata(horses)
    pipeline = bundle["feature_pipeline"]
    if pipeline == CONTEXT_PIPELINE:
        from training.db_context_features import build_serving_features as build_context_features
        try:
            raw, audit = build_context_features(db_path, race_info, horses)
        except ValueError as error:
            if str(error) in {"context-source-changed-during-preparation-retry",
                              "context-source-changed-during-prediction-retry"}:
                raise HTTPException(409, "データ更新中です。取得完了後に最新データで再予測してください。") from error
            raise
    else:
        raw, audit = build_serving_features(db_path, race_info, horses)
    if pipeline == OPTIMIZED_PIPELINE:
        from training.db_shared_optimized import select_features
        raw = select_features(raw)
    # The canonical parser above has already rejected conflicting aliases.
    from experiments.dataset import _date
    race_info = {**race_info, "date": _date(race_info.get("date") or race_info.get("race_date"))}
    encoded = bundle["encoder"].transform(raw)
    expected = list(bundle["feature_columns"])
    if list(encoded) != expected:
        raise ValueError("shared-db-encoder-column-mismatch")
    # Pending capture is replaced by an actual receipt check below, never by
    # a caller flag. All unrelated formula/history issues remain blocking.
    issues = [code for code in audit.get("issue_codes", []) if code != "card_capture_provenance_unverified"]
    target_date = str(race_info.get("date", "")).replace("-", "").replace("/", "")
    if target_date <= str(bundle.get("dataset_period_end", "99999999")):
        issues.append("target_not_after_training_evaluation_period")
    diagnostics = {
        **audit, "schema": "prediction-input-contract-v1", "status": "unverified",
        "expected_count": len(expected), "actual_count": len(encoded.columns),
        "missing_columns": [], "blocked_columns": [], "issue_codes": issues,
        "source_hash_matches": True, "history_available": True,
        "semantic_parity_verified": True, "feature_source_hashes": bundle["feature_source_hashes"],
        "feature_pipeline": pipeline, "scope": pipeline, "current_outcomes_consumed": False,
        "all_missing_columns": [name for name in encoded if encoded[name].isna().all()],
        "missing_value_rate": float(encoded.isna().to_numpy().mean()),
        "live_capture_hash_bound": False,
    }
    if pipeline == CONTEXT_PIPELINE:
        # A correctly timed input receipt proves inputs, not an independently
        # validated new model or betting strategy. The candidate stays opt-in.
        diagnostics["reference_only"] = True
        diagnostics["probability_validation_status"] = "candidate_live_validation_pending"
    try:
        proof = record_pre_race_input(
            info=race_info, horses=horses, features=raw, diagnostics=diagnostics,
            model_path=Path(model_path), trusted_context=trusted_context,
        )
    except (OSError, sqlite3.Error, ValueError, TypeError):
        proof = {"verified": False, "reason": "pre_race_db_capture_failed"}
    diagnostics["input_receipt"] = proof
    if proof["verified"]:
        diagnostics.update(status="aligned", live_capture_hash_bound=True, card_capture_time_verified=True)
    else:
        diagnostics["issue_codes"].append(proof["reason"])
    return encoded, diagnostics


def embedded_probability_report(bundle: dict, path: Path) -> dict:
    """A validated speed-score probability layer, not a borrowed win model."""
    unavailable = {"validated": False, "eligible_bet_types": []}
    if bundle.get("feature_pipeline") == CONTEXT_PIPELINE:
        return {**unavailable, "reference_only": True,
                "eligible_for_ev": False, "reason": "context_candidate_live_validation_pending"}
    try:
        verify_bundle_sources(bundle)
        digest = validate_published_artifact(path)
        if bundle.get("_shared_artifact_sha256") != digest:
            return unavailable
        report = bundle.get("probability_validation", {})
        if (report.get("schema") != "local-db-probability-validation-v1"
                or report.get("validated") is not True or report.get("eligible_for_ev") is not True
                or report.get("calibrated") is not True or report.get("status") != "validated"
                or bundle.get("calibrator") is None):
            return unavailable
        created = _instant(bundle.get("created_at"))
        end = datetime.strptime(report["validation_period_end"], "%Y-%m-%d").date()
        if created is None or created.astimezone(JST).date() <= end:
            return unavailable
        return {**report, "model_id": path.stem, "model_sha256": digest,
                "schema_version": 1, "probability_kind": "race_normalized_win",
                "evaluation_method": "out_of_sample", "selection_calibration_reviewed": True,
                "eligible_bet_types": ["単勝"], "validated_at": created.isoformat()}
    except (OSError, sqlite3.Error, ValueError, TypeError, KeyError):
        return unavailable
