from __future__ import annotations

from collections import Counter
from datetime import date, datetime, timezone
from typing import Any, Iterable, Mapping

from .contracts import ObservationContractError, parse_timestamp, timestamp


MIN_OBSERVATION_DAYS = 90
MIN_VALID_SAMPLES = 1_000
MIN_QUALIFYING_BETS = 100
VERDICT_READY = "READY_FOR_TRUSTED_REVIEW"
VERDICT_NOT_READY = "PRODUCTION_NOT_READY"


def _date(value: Any) -> date:
    if not isinstance(value, str):
        raise ObservationContractError("race-date-invalid")
    try:
        return date.fromisoformat(value)
    except ValueError as exc:
        raise ObservationContractError("race-date-invalid") from exc


def _joined_key(row: Mapping[str, Any]) -> tuple[str, str]:
    return str(row.get("race_id") or ""), str(row.get("horse_id") or "")


def build_progress_report(
    predictions: Iterable[Mapping[str, Any]],
    results: Iterable[Mapping[str, Any]],
    ingest_attempts: Iterable[Mapping[str, Any]],
    *,
    generated_at: datetime | None = None,
    environment: str = "staging",
) -> dict[str, Any]:
    now = (generated_at or datetime.now(timezone.utc)).astimezone(timezone.utc)
    prediction_rows = [dict(row) for row in predictions]
    result_rows = [dict(row) for row in results]
    attempts = [dict(row) for row in ingest_attempts]
    result_by_observation: dict[str, dict[str, Any]] = {}
    duplicate_result_ids: set[str] = set()
    for row in result_rows:
        observation_id = str(row.get("observation_id") or "")
        if observation_id in result_by_observation:
            duplicate_result_ids.add(observation_id)
        else:
            result_by_observation[observation_id] = row

    valid = 0
    missing_results = 0
    leakage = 0
    invalid_timestamps = 0
    qualifying_bets = 0
    race_dates: list[date] = []
    prediction_times: list[datetime] = []
    settlement_times: list[datetime] = []
    model_versions: Counter[str] = Counter()
    commit_shas: Counter[str] = Counter()
    seen_keys: set[tuple[str, str]] = set()
    duplicate_rows = len(duplicate_result_ids)

    for row in prediction_rows:
        key = _joined_key(row)
        if key in seen_keys:
            duplicate_rows += 1
        seen_keys.add(key)
        leakage_count = int(row.get("leakage_violation_count") or 0)
        leakage += max(0, leakage_count)
        observation_id = str(row.get("observation_id") or "")
        result = result_by_observation.get(observation_id)
        if result is None:
            missing_results += 1
        try:
            predicted_at = parse_timestamp(row.get("prediction_at"), code="prediction-time-invalid")
            observed_at = parse_timestamp(row.get("data_observed_at"), code="data-observed-time-invalid")
            cutoff_at = parse_timestamp(row.get("data_cutoff_at"), code="data-cutoff-time-invalid")
            race_date = _date(row.get("race_date"))
            settled_at = (
                parse_timestamp(result.get("settled_at"), code="settlement-time-invalid")
                if result is not None
                else None
            )
            if not (observed_at <= cutoff_at <= predicted_at):
                raise ObservationContractError("prediction-temporal-order-invalid")
            if settled_at is not None and settled_at < predicted_at:
                raise ObservationContractError("settlement-before-prediction")
        except (ObservationContractError, TypeError, ValueError):
            invalid_timestamps += 1
            continue
        if result is None or leakage_count != 0:
            continue
        valid += 1
        race_dates.append(race_date)
        prediction_times.append(predicted_at)
        settlement_times.append(settled_at)
        if bool(row.get("qualifying_bet")) and float(row.get("wager_amount") or 0) > 0:
            qualifying_bets += 1
        model_versions[str(row.get("model_version") or "unknown")] += 1
        commit_shas[str(row.get("candidate_commit_sha") or "unknown")] += 1

    # Idempotent replays prove that duplicate insertion was prevented; they are
    # expected when multiple stateless instances race to register the same model.
    # Payload-binding conflicts, unlike safe replays, remain an integrity failure.
    idempotent_replay_count = sum(1 for row in attempts if row.get("outcome") == "duplicate")
    conflict_count = sum(1 for row in attempts if row.get("outcome") == "conflict")
    duplicate_count = duplicate_rows + conflict_count
    if race_dates:
        start_date = min(race_dates)
        end_date = max(race_dates)
        elapsed_days = (end_date - start_date).days + 1
        observation_start = min(prediction_times)
        observation_end = max(settlement_times)
    else:
        elapsed_days = 0
        observation_start = None
        observation_end = None

    days_met = elapsed_days >= MIN_OBSERVATION_DAYS
    samples_met = valid >= MIN_VALID_SAMPLES
    bets_met = qualifying_bets >= MIN_QUALIFYING_BETS
    integrity_met = (
        duplicate_count == 0
        and missing_results == 0
        and leakage == 0
        and invalid_timestamps == 0
    )
    ready = days_met and samples_met and bets_met and integrity_met
    return {
        "schema": "phase3n-observation-progress",
        "schema_version": 1,
        "environment": environment,
        "generated_at": timestamp(now),
        "observation_start_at": timestamp(observation_start) if observation_start else None,
        "observation_end_at": timestamp(observation_end) if observation_end else None,
        "elapsed_days": elapsed_days,
        "valid_sample_count": valid,
        "qualifying_bet_count": qualifying_bets,
        "duplicate_count": duplicate_count,
        "idempotent_replay_count": idempotent_replay_count,
        "payload_conflict_count": conflict_count,
        "missing_result_count": missing_results,
        "leakage_violation_count": leakage,
        "invalid_timestamp_count": invalid_timestamps,
        "model_version_counts": dict(sorted(model_versions.items())),
        "commit_sha_counts": dict(sorted(commit_shas.items())),
        "observation_period_90_days_met": days_met,
        "valid_samples_1000_met": samples_met,
        "qualifying_bets_100_met": bets_met,
        "integrity_conditions_met": integrity_met,
        "readiness_verdict": VERDICT_READY if ready else VERDICT_NOT_READY,
    }


def render_progress_markdown(report: Mapping[str, Any]) -> str:
    def mark(value: Any) -> str:
        return "PASS" if value is True else "NOT MET"

    lines = [
        "# Phase 3N observation progress",
        "",
        f"- Environment: `{report['environment']}`",
        f"- Generated at: `{report['generated_at']}`",
        f"- Observation start: `{report['observation_start_at'] or 'not available'}`",
        f"- Observation end: `{report['observation_end_at'] or 'not available'}`",
        f"- Elapsed days: **{report['elapsed_days']}** / 90 ({mark(report['observation_period_90_days_met'])})",
        f"- Valid samples: **{report['valid_sample_count']}** / 1,000 ({mark(report['valid_samples_1000_met'])})",
        f"- Qualifying bets: **{report['qualifying_bet_count']}** / 100 ({mark(report['qualifying_bets_100_met'])})",
        f"- Duplicate count: **{report['duplicate_count']}**",
        f"- Safely rejected idempotent replays: **{report['idempotent_replay_count']}**",
        f"- Payload-binding conflicts: **{report['payload_conflict_count']}**",
        f"- Missing result count: **{report['missing_result_count']}**",
        f"- Leakage violation count: **{report['leakage_violation_count']}**",
        f"- Invalid timestamp count: **{report['invalid_timestamp_count']}**",
        f"- Readiness verdict: **{report['readiness_verdict']}**",
        "",
        "## Model version counts",
        "",
    ]
    counts = report.get("model_version_counts") or {}
    lines.extend(f"- `{key}`: {value}" for key, value in counts.items())
    if not counts:
        lines.append("- No valid observations")
    lines.extend(("", "## Commit SHA counts", ""))
    commits = report.get("commit_sha_counts") or {}
    lines.extend(f"- `{key}`: {value}" for key, value in commits.items())
    if not commits:
        lines.append("- No valid observations")
    return "\n".join(lines) + "\n"
