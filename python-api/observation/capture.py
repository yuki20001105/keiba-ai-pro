from __future__ import annotations

import logging
import os
from datetime import datetime, time, timezone
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


def _training_cutoff(bundle: Mapping[str, Any]) -> datetime:
    value = bundle.get("training_date_to")
    if not isinstance(value, str):
        raise ObservationContractError("training-cutoff-missing")
    compact = value.replace("-", "")
    if len(compact) != 8 or not compact.isdigit():
        raise ObservationContractError("training-cutoff-invalid")
    parsed = datetime.strptime(compact, "%Y%m%d").date()
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
    config.require_staging_boundary()
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
        training_data_ended_at=_training_cutoff(bundle),
        environment="staging",
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
            data_cutoff_at=max(_source_timestamp(row) for row in source_records),
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
