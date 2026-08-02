from __future__ import annotations

from datetime import datetime
from typing import Any

from .contracts import ObservationContractError, parse_timestamp
from .service import ObservationGateway, build_result_payload


def _finish_order(data: dict[str, Any]) -> int | None:
    for key in ("finish_position", "finish", "finish_order", "actual_finish"):
        value = data.get(key)
        if value in (None, "", "中止", "取消", "除外"):
            continue
        try:
            parsed = int(float(value))
        except (TypeError, ValueError):
            continue
        return parsed if 1 <= parsed <= 99 else None
    return None


def reconcile_available_results(gateway: ObservationGateway) -> dict[str, int]:
    predictions = gateway.select(
        "phase3n_prediction_observations",
        "observation_id,race_id,horse_id,horse_number,qualifying_bet,wager_amount",
    )
    results = gateway.select("phase3n_result_observation_events", "observation_id")
    settled_ids = {str(row["observation_id"]) for row in results}
    pending = [row for row in predictions if str(row["observation_id"]) not in settled_ids]
    if not pending:
        return {"pending": 0, "inserted": 0, "duplicates": 0, "skipped": 0}
    source_rows = gateway.select_in(
        "race_results_ultimate",
        "race_id,horse_number,data,updated_at",
        "race_id",
        (str(row["race_id"]) for row in pending),
    )
    source_by_key = {
        (str(row.get("race_id") or ""), int(row.get("horse_number") or 0)): row
        for row in source_rows
    }
    inserted = 0
    duplicates = 0
    skipped = 0
    for prediction in pending:
        source = source_by_key.get(
            (str(prediction["race_id"]), int(prediction["horse_number"]))
        )
        if source is None or not isinstance(source.get("data"), dict):
            skipped += 1
            continue
        finish = _finish_order(source["data"])
        if finish is None:
            skipped += 1
            continue
        # A qualifying wager needs an approved, source-backed payout mapping.
        # Until that adapter exists, skipping is safer than recording a zero or
        # inferred return that could fabricate ROI evidence.
        if bool(prediction.get("qualifying_bet")):
            skipped += 1
            continue
        updated_at = source.get("updated_at")
        settled_at = parse_timestamp(updated_at, code="result-source-time-invalid")
        payload = build_result_payload(
            observation_id=str(prediction["observation_id"]),
            settled_at=settled_at,
            y_true=1 if finish == 1 else 0,
            finish_order=finish,
            bet_outcome="not-bet",
            return_amount=0.0,
            baseline_return_amount=0.0,
        )
        outcome = gateway.record_result(payload)["mutation_code"]
        if outcome == "inserted":
            inserted += 1
        else:
            duplicates += 1
    return {
        "pending": len(pending),
        "inserted": inserted,
        "duplicates": duplicates,
        "skipped": skipped,
    }
