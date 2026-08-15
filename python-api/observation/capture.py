from __future__ import annotations

import logging
import os
import re
from datetime import datetime, time, timedelta, timezone
from pathlib import Path
from typing import Any, Mapping
from zoneinfo import ZoneInfo

from .contracts import ObservationContractError, parse_timestamp
from .service import (
    ObservationConfig,
    ObservationGateway,
    build_model_manifest_payload,
    build_prediction_payload,
)
from .staking import build_staking_decisions, load_staking_payout_policy


logger = logging.getLogger(__name__)
JRA_TIMEZONE = ZoneInfo("Asia/Tokyo")
MAX_SOURCE_AGE = timedelta(minutes=30)


def _source_timestamp(record: Mapping[str, Any]) -> datetime:
    for key in ("data_observed_at", "source_observed_at", "observed_at", "updated_at", "fetched_at"):
        value = record.get(key)
        if isinstance(value, (int, float)):
            return datetime.fromtimestamp(float(value), tz=timezone.utc)
        if isinstance(value, str) and value.strip():
            try:
                return parse_timestamp(value, code="source-observed-at-invalid")
            except ObservationContractError:
                continue
    raise ObservationContractError("authoritative-source-observed-at-missing")


_MODEL_TRAINING_RANGE_RE = re.compile(r"(?:^|_)(\d{8})_(\d{8})(?:_|$)")


def _training_cutoff(bundle: Mapping[str, Any], model_path: Path | None = None) -> datetime:
    value = bundle.get("training_date_to")
    if not isinstance(value, str):
        raise ObservationContractError("training-cutoff-missing")
    compact = value.replace("-", "")
    if len(compact) == 6 and compact.isdigit() and model_path is not None:
        match = _MODEL_TRAINING_RANGE_RE.search(model_path.stem)
        if match is None or not match.group(2).startswith(compact):
            raise ObservationContractError("training-cutoff-artifact-mismatch")
        compact = match.group(2)
        training_from = str(bundle.get("training_date_from") or "").replace("-", "")
        if training_from and not match.group(1).startswith(training_from):
            raise ObservationContractError("training-cutoff-artifact-mismatch")
    if len(compact) != 8 or not compact.isdigit():
        raise ObservationContractError("training-cutoff-invalid")
    try:
        parsed = datetime.strptime(compact, "%Y%m%d").date()
    except ValueError as exc:
        raise ObservationContractError("training-cutoff-invalid") from exc
    return datetime.combine(parsed, time.max, tzinfo=JRA_TIMEZONE).astimezone(timezone.utc)


def _race_date(race_info: Mapping[str, Any]) -> str:
    value = str(race_info.get("date") or "").replace("-", "")
    if len(value) != 8 or not value.isdigit():
        raise ObservationContractError("race-date-missing")
    return f"{value[:4]}-{value[4:6]}-{value[6:]}"


def _feature_row_by_horse_number(frame: Any, source_records: list[Mapping[str, Any]]) -> dict[int, list[Any]]:
    columns = list(frame.columns)
    rows: dict[int, list[Any]] = {}
    for index, record in enumerate(source_records):
        number = record.get("horse_number")
        if number is None:
            number = record.get("horse_no")
        if number is None:
            number = index + 1
        rows[int(number)] = [frame.iloc[index][column] for column in columns]
    return rows


def capture_analyze_predictions(
    *,
    bundle: Mapping[str, Any],
    model_path: Path,
    feature_frame: Any,
    source_records: list[Mapping[str, Any]],
    race_id: str,
    race_info: Mapping[str, Any],
    predictions: list[Mapping[str, Any]],
    latency_ms: float,
    environ: Mapping[str, str] | None = None,
    client: Any | None = None,
) -> dict[str, Any]:
    values = os.environ if environ is None else environ
    config = ObservationConfig.from_env(values)
    if not config.enabled:
        return {"enabled": False, "inserted": 0, "duplicates": 0}
    config.require_environment_boundary()
    if client is None:
        from app_config import get_supabase_client  # type: ignore

        client = get_supabase_client()
    gateway = ObservationGateway(client)
    feature_columns = [str(column) for column in feature_frame.columns]
    model_id = str(bundle.get("model_id") or bundle.get("created_at") or model_path.stem)
    model_version = str(bundle.get("created_at") or model_id)
    manifest = build_model_manifest_payload(
        model_id=model_id,
        model_version=model_version,
        model_path=model_path,
        candidate_commit_sha=str(config.candidate_commit_sha),
        feature_columns=feature_columns,
        training_data_ended_at=_training_cutoff(bundle, model_path),
        environment=config.app_env,
        expanding_window_checks_passed=config.expanding_window_checks_passed,
    )
    registered = gateway.register_manifest(manifest)
    manifest_id = str(registered.get("returned_manifest_id") or "")
    if not manifest_id:
        raise ObservationContractError("model-manifest-id-missing")
    source_by_number: dict[int, Mapping[str, Any]] = {}
    for index, source in enumerate(source_records):
        number = source.get("horse_number")
        if number is None:
            number = source.get("horse_no")
        source_by_number[int(number or index + 1)] = source
    statuses = {
        str(source.get("odds_status") or "").strip().lower()
        for source in source_records
    }
    if statuses != {"middle"}:
        raise ObservationContractError("prediction-odds-status-not-middle")
    source_timestamps = [_source_timestamp(source) for source in source_records]
    data_cutoff_at = max(source_timestamps)
    now = datetime.now(timezone.utc)
    if data_cutoff_at > now or now - data_cutoff_at > MAX_SOURCE_AGE:
        raise ObservationContractError("prediction-source-freshness-invalid")
    features_by_number = _feature_row_by_horse_number(feature_frame, source_records)
    staking_policy = load_staking_payout_policy()
    staking_decisions = build_staking_decisions(predictions, staking_policy)
    inserted = 0
    duplicates = 0
    for prediction in predictions:
        number = int(prediction.get("horse_number") or prediction.get("horse_no") or 0)
        source = source_by_number.get(number)
        feature_values = features_by_number.get(number)
        if source is None or feature_values is None:
            raise ObservationContractError("prediction-source-binding-missing")
        horse_id = str(prediction.get("horse_id") or source.get("horse_id") or "")
        if not horse_id:
            raise ObservationContractError("prediction-horse-id-missing")
        observed_at = _source_timestamp(source)
        decision = staking_decisions[number]
        payload = build_prediction_payload(
            manifest_id=manifest_id,
            race_id=race_id,
            horse_id=horse_id,
            horse_number=number,
            race_date=_race_date(race_info),
            data_observed_at=observed_at,
            data_cutoff_at=data_cutoff_at,
            feature_columns=feature_columns,
            feature_values=feature_values,
            predicted_value=float(prediction.get("p_raw") or 0.0),
            predicted_probability=float(
                prediction.get("win_probability")
                if prediction.get("win_probability") is not None
                else prediction.get("p_norm") or 0.0
            ),
            predicted_rank=int(prediction.get("predicted_rank") or number),
            odds_at_prediction=(
                float(prediction["odds"]) if prediction.get("odds") not in (None, 0, 0.0) else None
            ),
            recommendation=decision.recommendation,
            qualifying_bet=decision.qualifying_bet,
            wager_amount=decision.wager_amount,
            baseline_wager_amount=decision.baseline_wager_amount,
            latency_ms=latency_ms,
            source_environment=config.app_env,
        )
        result = gateway.record_prediction(payload)
        if result["mutation_code"] == "inserted":
            inserted += 1
        else:
            duplicates += 1
    return {
        "enabled": True,
        "inserted": inserted,
        "duplicates": duplicates,
        "staking_policy_id": staking_policy.policy_id,
        "staking_policy_sha256": staking_policy.policy_sha256,
        "staking_policy_approved": staking_policy.approved,
    }
