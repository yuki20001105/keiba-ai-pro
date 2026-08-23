from __future__ import annotations

from datetime import datetime
from typing import Any

from .contracts import ObservationContractError, parse_timestamp
from .service import ObservationGateway, build_result_payload
from .staking import load_staking_payout_policy, settled_returns


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


def reconcile_available_results(
    gateway: ObservationGateway,
    *,
    source_environment: str | None = None,
) -> dict[str, int]:
    if source_environment not in {None, "staging", "production"}:
        raise ObservationContractError("observation-environment-invalid")
    predictions = gateway.select(
        "phase3n_prediction_observations",
        "observation_id,race_id,horse_id,horse_number,qualifying_bet,wager_amount,baseline_wager_amount,source_environment",
    )
    if source_environment is not None:
        predictions = [
            row for row in predictions if row.get("source_environment") == source_environment
        ]
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
    wagering_race_ids = {
        str(row["race_id"])
        for row in pending
        if float(row.get("wager_amount") or 0.0) > 0
        or float(row.get("baseline_wager_amount") or 0.0) > 0
    }
    staking_policy = load_staking_payout_policy()
    if wagering_race_ids and not staking_policy.approved:
        raise ObservationContractError("staking-policy-not-approved")
    payout_rows: list[dict[str, Any]] = []
    for table in staking_policy.payout_source_tables:
        payout_rows.extend(
            gateway.select_in(
                table,
                "race_id,bet_type,combination,payout,created_at",
                "race_id",
                wagering_race_ids,
            )
        )
    payouts_by_race: dict[str, list[dict[str, Any]]] = {}
    for row in payout_rows:
        payouts_by_race.setdefault(str(row.get("race_id") or ""), []).append(row)
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
        updated_at = source.get("updated_at")
        settled_at = parse_timestamp(updated_at, code="result-source-time-invalid")
        for payout_row in payouts_by_race.get(str(prediction["race_id"]), []):
            created_at = payout_row.get("created_at")
            if created_at:
                payout_time = parse_timestamp(created_at, code="payout-source-time-invalid")
                settled_at = max(settled_at, payout_time)
        bet_outcome, return_amount, baseline_return_amount = settled_returns(
            prediction,
            finish_order=finish,
            payout_rows=payouts_by_race.get(str(prediction["race_id"]), []),
            policy=staking_policy,
        )
        payload = build_result_payload(
            observation_id=str(prediction["observation_id"]),
            settled_at=settled_at,
            y_true=1 if finish == 1 else 0,
            finish_order=finish,
            bet_outcome=bet_outcome,
            return_amount=return_amount,
            baseline_return_amount=baseline_return_amount,
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


def reconcile_result_snapshot(
    gateway: ObservationGateway,
    *,
    race_id: str,
    horse_rows: list[dict[str, Any]],
    payout_rows: list[dict[str, Any]],
    settled_at: datetime,
    source_environment: str,
) -> dict[str, int]:
    """Append settlement events for one already-observed race.

    The snapshot is an external result source only; it cannot create prediction
    rows.  Every existing observation must have an official numeric finish, and
    idempotent result RPCs remain the only database mutation.
    """

    if source_environment not in {"staging", "production"}:
        raise ObservationContractError("observation-environment-invalid")
    if settled_at.tzinfo is None:
        raise ObservationContractError("settlement-timezone-required")
    predictions = gateway.select(
        "phase3n_prediction_observations",
        "observation_id,race_id,horse_id,horse_number,qualifying_bet,wager_amount,baseline_wager_amount,source_environment",
    )
    predictions = [
        row
        for row in predictions
        if str(row.get("race_id")) == str(race_id)
        and row.get("source_environment") == source_environment
    ]
    if not predictions:
        raise ObservationContractError("settlement-predictions-missing")
    results = gateway.select("phase3n_result_observation_events", "observation_id")
    settled_ids = {str(row["observation_id"]) for row in results}
    pending = [row for row in predictions if str(row["observation_id"]) not in settled_ids]
    if not pending:
        return {"pending": 0, "inserted": 0, "duplicates": 0, "skipped": 0}

    source_by_number: dict[int, dict[str, Any]] = {}
    for row in horse_rows:
        try:
            number = int(row.get("horse_number") or row.get("horse_num") or 0)
        except (TypeError, ValueError):
            continue
        if number > 0:
            source_by_number[number] = row
    finishes: dict[int, int] = {}
    for prediction in pending:
        number = int(prediction["horse_number"])
        source = source_by_number.get(number)
        finish = _finish_order(source or {})
        if finish is None:
            raise ObservationContractError("settlement-result-incomplete")
        finishes[number] = finish

    policy = load_staking_payout_policy()
    wagering = any(
        float(row.get("wager_amount") or 0.0) > 0
        or float(row.get("baseline_wager_amount") or 0.0) > 0
        for row in pending
    )
    if wagering and not policy.approved:
        raise ObservationContractError("staking-policy-not-approved")

    inserted = 0
    duplicates = 0
    for prediction in pending:
        finish = finishes[int(prediction["horse_number"])]
        bet_outcome, return_amount, baseline_return_amount = settled_returns(
            prediction,
            finish_order=finish,
            payout_rows=payout_rows,
            policy=policy,
        )
        payload = build_result_payload(
            observation_id=str(prediction["observation_id"]),
            settled_at=settled_at,
            y_true=1 if finish == 1 else 0,
            finish_order=finish,
            bet_outcome=bet_outcome,
            return_amount=return_amount,
            baseline_return_amount=baseline_return_amount,
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
        "skipped": 0,
    }
