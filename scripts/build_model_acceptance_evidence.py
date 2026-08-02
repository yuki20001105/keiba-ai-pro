from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
import math
import os
import re
from collections import defaultdict
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any, Iterable
from zoneinfo import ZoneInfo


ROOT = Path(__file__).resolve().parents[1]
VERIFIER_PATH = ROOT / "scripts" / "verify_model_acceptance.py"
CONSTANTS_PATH = ROOT / "keiba" / "keiba_ai" / "constants.py"

SOURCE_SCHEMA = "model-evaluation-observations"
SOURCE_SCHEMA_VERSION = 1
MAX_INPUT_BYTES = 16 * 1024 * 1024
MAX_ROWS = 100_000
MAX_FEATURE_COLUMNS = 2_048
ECE_BIN_COUNT = 10
JRA_TIMEZONE = ZoneInfo("Asia/Tokyo")

COMMIT_RE = re.compile(r"^[0-9a-f]{40}$")
IDENTIFIER_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.:-]{0,127}$")
DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")

SOURCE_KEYS = frozenset(
    {
        "schema",
        "schema_version",
        "candidate_commit_sha",
        "model_id",
        "model_artifact_sha256",
        "generated_at",
        "training_data_ended_at",
        "holdout_kind",
        "model_feature_columns",
        "expanding_window_checks_passed",
        "initial_bankroll",
        "rows",
    }
)
ROW_KEYS = frozenset(
    {
        "observation_id",
        "race_date",
        "prediction_at",
        "data_observed_at",
        "settled_at",
        "y_true",
        "predicted_probability",
        "wager_amount",
        "return_amount",
        "baseline_wager_amount",
        "baseline_return_amount",
        "latency_ms",
    }
)


class DuplicateKeyError(ValueError):
    pass


class EvidenceBuildError(ValueError):
    def __init__(self, failure_codes: Iterable[str]):
        self.failure_codes = tuple(dict.fromkeys(failure_codes))
        super().__init__(self.failure_codes[0] if self.failure_codes else "evidence-build-failed")


def _load_module(name: str, path: Path) -> Any:
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"cannot load {path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


acceptance_gate = _load_module("model_acceptance_verifier_for_builder", VERIFIER_PATH)
constants = _load_module("keiba_constants_for_acceptance_builder", CONSTANTS_PATH)
FUTURE_FIELDS = frozenset(constants.FUTURE_FIELDS)


def _object_without_duplicates(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise DuplicateKeyError("duplicate JSON key")
        result[key] = value
    return result


def _reject_json_constant(_value: str) -> None:
    raise ValueError("non-finite JSON number")


def load_json(path: Path, *, prefix: str, max_bytes: int = MAX_INPUT_BYTES) -> Any:
    try:
        stat = path.stat()
    except OSError as exc:
        raise EvidenceBuildError((f"{prefix}-file-unavailable",)) from exc
    if not path.is_file():
        raise EvidenceBuildError((f"{prefix}-file-unavailable",))
    if stat.st_size <= 0:
        raise EvidenceBuildError((f"{prefix}-file-empty",))
    if stat.st_size > max_bytes:
        raise EvidenceBuildError((f"{prefix}-file-too-large",))
    try:
        return json.loads(
            path.read_bytes().decode("utf-8", errors="strict"),
            object_pairs_hook=_object_without_duplicates,
            parse_constant=_reject_json_constant,
        )
    except UnicodeDecodeError as exc:
        raise EvidenceBuildError((f"{prefix}-invalid-utf8",)) from exc
    except DuplicateKeyError as exc:
        raise EvidenceBuildError((f"{prefix}-duplicate-json-key",)) from exc
    except (json.JSONDecodeError, ValueError, RecursionError) as exc:
        raise EvidenceBuildError((f"{prefix}-invalid-json",)) from exc
    except OSError as exc:
        raise EvidenceBuildError((f"{prefix}-file-unavailable",)) from exc


def _finite_number(value: Any, *, minimum: float | None = None) -> bool:
    if type(value) not in (int, float) or not math.isfinite(float(value)):
        return False
    return minimum is None or float(value) >= minimum


def _parse_timestamp(value: Any) -> datetime | None:
    if not isinstance(value, str):
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        return None
    return parsed.astimezone(timezone.utc)


def _parse_date(value: Any) -> date | None:
    if not isinstance(value, str) or DATE_RE.fullmatch(value) is None:
        return None
    try:
        return date.fromisoformat(value)
    except ValueError:
        return None


def _canonical_timestamp(value: datetime) -> str:
    return value.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def _rounded(value: float) -> float:
    return round(value, 12)


def _canonical_sha256(value: Any) -> str:
    serialized = json.dumps(
        value,
        ensure_ascii=True,
        allow_nan=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(serialized).hexdigest()


def _auc(labels: list[int], probabilities: list[float]) -> float:
    positives = sum(labels)
    negatives = len(labels) - positives
    if positives == 0 or negatives == 0:
        raise EvidenceBuildError(("observations-auc-class-balance-invalid",))

    ordered = sorted(zip(probabilities, labels), key=lambda item: item[0])
    positive_rank_sum = 0.0
    index = 0
    while index < len(ordered):
        end = index + 1
        while end < len(ordered) and ordered[end][0] == ordered[index][0]:
            end += 1
        average_rank = ((index + 1) + end) / 2.0
        positive_rank_sum += average_rank * sum(label for _, label in ordered[index:end])
        index = end
    return (positive_rank_sum - positives * (positives + 1) / 2.0) / (positives * negatives)


def _expected_calibration_error(labels: list[int], probabilities: list[float]) -> float:
    counts = [0] * ECE_BIN_COUNT
    probability_sums = [0.0] * ECE_BIN_COUNT
    label_sums = [0] * ECE_BIN_COUNT
    for label, probability in zip(labels, probabilities):
        bin_index = min(int(probability * ECE_BIN_COUNT), ECE_BIN_COUNT - 1)
        counts[bin_index] += 1
        probability_sums[bin_index] += probability
        label_sums[bin_index] += label
    total = len(labels)
    return math.fsum(
        (count / total)
        * abs((probability_sums[index] / count) - (label_sums[index] / count))
        for index, count in enumerate(counts)
        if count
    )


def _roi_percent(wagers: list[float], returns: list[float], *, prefix: str) -> float:
    total_wager = math.fsum(wagers)
    if total_wager <= 0:
        raise EvidenceBuildError((f"observations-{prefix}-wagers-required",))
    return ((math.fsum(returns) / total_wager) - 1.0) * 100.0


def _maximum_drawdown_percent(
    initial_bankroll: float,
    settlements: list[tuple[datetime, float]],
) -> float:
    net_by_settlement: dict[datetime, list[float]] = defaultdict(list)
    for settled_at, net_return in settlements:
        net_by_settlement[settled_at].append(net_return)
    equity = initial_bankroll
    peak = initial_bankroll
    maximum = 0.0
    for settled_at in sorted(net_by_settlement):
        equity += math.fsum(net_by_settlement[settled_at])
        if equity > peak:
            peak = equity
        maximum = max(maximum, ((peak - equity) / peak) * 100.0)
    return maximum


def _nearest_rank_p95(values: list[float]) -> float:
    ordered = sorted(values)
    rank = math.ceil(0.95 * len(ordered))
    return ordered[rank - 1]


def _validate_contract(contract: Any) -> tuple[str, str]:
    failures: list[str] = []
    blockers: list[str] = []
    if not acceptance_gate._validate_contract(contract, failures, blockers):
        raise EvidenceBuildError(("contract-invalid", *failures))
    digest = acceptance_gate.contract_sha256(contract)
    if not isinstance(digest, str):
        raise EvidenceBuildError(("contract-digest-unavailable",))
    return contract["contract_id"], digest


def build_evidence(
    source: Any,
    contract: Any,
    *,
    expected_commit: str,
    now: datetime | None = None,
) -> dict[str, Any]:
    failures: list[str] = []
    now_utc = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)
    if COMMIT_RE.fullmatch(expected_commit or "") is None:
        failures.append("expected-commit-invalid")
    if not isinstance(source, dict) or frozenset(source) != SOURCE_KEYS:
        failures.append("observations-schema-invalid")
        raise EvidenceBuildError(failures)
    if source["schema"] != SOURCE_SCHEMA:
        failures.append("observations-schema-invalid")
    if type(source["schema_version"]) is not int or source["schema_version"] != SOURCE_SCHEMA_VERSION:
        failures.append("observations-schema-version-invalid")
    if source["candidate_commit_sha"] != expected_commit:
        failures.append("observations-candidate-commit-mismatch")
    if not isinstance(source["model_id"], str) or IDENTIFIER_RE.fullmatch(source["model_id"]) is None:
        failures.append("observations-model-id-invalid")
    if (
        not isinstance(source["model_artifact_sha256"], str)
        or acceptance_gate.DIGEST_RE.fullmatch(source["model_artifact_sha256"]) is None
    ):
        failures.append("observations-model-artifact-digest-invalid")
    if source["holdout_kind"] != "out_of_time":
        failures.append("observations-holdout-not-out-of-time")
    if source["expanding_window_checks_passed"] is not True:
        failures.append("observations-expanding-window-check-required")
    if not _finite_number(source["initial_bankroll"], minimum=0.000000001):
        failures.append("observations-initial-bankroll-invalid")

    generated_at = _parse_timestamp(source["generated_at"])
    training_ended_at = _parse_timestamp(source["training_data_ended_at"])
    if generated_at is None:
        failures.append("observations-generated-at-invalid")
    elif (generated_at - now_utc).total_seconds() > 300:
        failures.append("observations-generated-in-future")
    if training_ended_at is None:
        failures.append("observations-training-cutoff-invalid")
    elif generated_at is not None and training_ended_at >= generated_at:
        failures.append("observations-training-cutoff-invalid")

    columns = source["model_feature_columns"]
    if (
        not isinstance(columns, list)
        or not 1 <= len(columns) <= MAX_FEATURE_COLUMNS
        or any(not isinstance(column, str) or IDENTIFIER_RE.fullmatch(column) is None for column in columns)
    ):
        failures.append("observations-model-feature-columns-invalid")
    elif len(set(columns)) != len(columns):
        failures.append("observations-model-feature-columns-duplicate")
    elif FUTURE_FIELDS.intersection(columns):
        failures.append("observations-future-field-leakage-detected")

    rows = source["rows"]
    if not isinstance(rows, list) or not 1 <= len(rows) <= MAX_ROWS:
        failures.append("observations-row-count-invalid")
    if failures:
        raise EvidenceBuildError(failures)

    labels: list[int] = []
    probabilities: list[float] = []
    wagers: list[float] = []
    returns: list[float] = []
    baseline_wagers: list[float] = []
    baseline_returns: list[float] = []
    latencies: list[float] = []
    freshness_minutes: list[float] = []
    race_dates: list[date] = []
    prediction_times: list[datetime] = []
    settlement_times: list[datetime] = []
    settlements: list[tuple[datetime, float]] = []
    observation_ids: set[str] = set()

    for row in rows:
        if not isinstance(row, dict) or frozenset(row) != ROW_KEYS:
            failures.append("observation-row-schema-invalid")
            continue
        observation_id = row["observation_id"]
        if not isinstance(observation_id, str) or IDENTIFIER_RE.fullmatch(observation_id) is None:
            failures.append("observation-id-invalid")
        elif observation_id in observation_ids:
            failures.append("observation-id-duplicate")
        else:
            observation_ids.add(observation_id)

        race_date = _parse_date(row["race_date"])
        prediction_at = _parse_timestamp(row["prediction_at"])
        data_observed_at = _parse_timestamp(row["data_observed_at"])
        settled_at = _parse_timestamp(row["settled_at"])
        if race_date is None:
            failures.append("observation-race-date-invalid")
        if prediction_at is None or data_observed_at is None or settled_at is None:
            failures.append("observation-timestamp-invalid")
        else:
            if data_observed_at > prediction_at:
                failures.append("observation-data-after-prediction")
            if race_date is not None and race_date != prediction_at.astimezone(JRA_TIMEZONE).date():
                failures.append("observation-race-date-prediction-mismatch")
            if prediction_at <= training_ended_at:
                failures.append("observation-not-after-training-cutoff")
            if settled_at < prediction_at:
                failures.append("observation-settled-before-prediction")
            if settled_at > generated_at:
                failures.append("observation-settled-after-generation")

        if type(row["y_true"]) is not int or row["y_true"] not in (0, 1):
            failures.append("observation-label-invalid")
        probability = row["predicted_probability"]
        if not _finite_number(probability) or not 0.0 <= float(probability) <= 1.0:
            failures.append("observation-probability-invalid")
        numeric_fields = (
            "wager_amount",
            "return_amount",
            "baseline_wager_amount",
            "baseline_return_amount",
            "latency_ms",
        )
        if any(not _finite_number(row[field], minimum=0.0) for field in numeric_fields):
            failures.append("observation-numeric-value-invalid")
        else:
            if float(row["wager_amount"]) == 0.0 and float(row["return_amount"]) != 0.0:
                failures.append("observation-return-without-wager")
            if (
                float(row["baseline_wager_amount"]) == 0.0
                and float(row["baseline_return_amount"]) != 0.0
            ):
                failures.append("observation-baseline-return-without-wager")

        if failures:
            continue
        labels.append(row["y_true"])
        probabilities.append(float(probability))
        wager = float(row["wager_amount"])
        returned = float(row["return_amount"])
        wagers.append(wager)
        returns.append(returned)
        baseline_wagers.append(float(row["baseline_wager_amount"]))
        baseline_returns.append(float(row["baseline_return_amount"]))
        latencies.append(float(row["latency_ms"]))
        freshness_minutes.append((prediction_at - data_observed_at).total_seconds() / 60.0)
        race_dates.append(race_date)
        prediction_times.append(prediction_at)
        settlement_times.append(settled_at)
        settlements.append((settled_at, returned - wager))

    if failures:
        raise EvidenceBuildError(failures)

    auc = _auc(labels, probabilities)
    candidate_roi = _roi_percent(wagers, returns, prefix="candidate")
    baseline_roi = _roi_percent(baseline_wagers, baseline_returns, prefix="baseline")
    contract_id, contract_digest = _validate_contract(contract)
    metrics = {
        "auc": _rounded(auc),
        "brier_score": _rounded(
            math.fsum((probability - label) ** 2 for label, probability in zip(labels, probabilities))
            / len(labels)
        ),
        "expected_calibration_error": _rounded(
            _expected_calibration_error(labels, probabilities)
        ),
        "roi_percent": _rounded(candidate_roi),
        "max_drawdown_percent": _rounded(
            _maximum_drawdown_percent(float(source["initial_bankroll"]), settlements)
        ),
        "bet_count": sum(wager > 0.0 for wager in wagers),
        "sample_count": len(rows),
        "p95_latency_ms": _rounded(_nearest_rank_p95(latencies)),
        "data_freshness_minutes": _rounded(max(freshness_minutes)),
        "observation_period_days": (max(race_dates) - min(race_dates)).days + 1,
        "baseline_roi_delta_percent": _rounded(candidate_roi - baseline_roi),
    }
    return {
        "schema": acceptance_gate.EVIDENCE_SCHEMA,
        "schema_version": acceptance_gate.SCHEMA_VERSION,
        "candidate_commit_sha": expected_commit,
        "model_id": source["model_id"],
        "model_artifact_sha256": source["model_artifact_sha256"],
        "model_feature_columns_sha256": _canonical_sha256(columns),
        "observations_sha256": _canonical_sha256(source),
        "contract_id": contract_id,
        "contract_sha256": contract_digest,
        "observed_at": _canonical_timestamp(generated_at),
        "evaluation": {
            "holdout_kind": "out_of_time",
            "future_field_leakage_detected": False,
            "started_at": _canonical_timestamp(min(prediction_times)),
            "ended_at": _canonical_timestamp(max(settlement_times)),
        },
        "metrics": metrics,
    }


def write_json_atomic(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    serialized = json.dumps(payload, ensure_ascii=True, allow_nan=False, sort_keys=True, indent=2) + "\n"
    try:
        temporary.write_text(serialized, encoding="utf-8")
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Recompute model acceptance evidence from strict out-of-time row observations."
    )
    parser.add_argument("--input", type=Path, required=True)
    parser.add_argument("--contract", type=Path, required=True)
    parser.add_argument("--expected-commit", required=True)
    parser.add_argument("--output", type=Path, required=True)
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    try:
        source = load_json(args.input, prefix="observations")
        contract = load_json(
            args.contract,
            prefix="contract",
            max_bytes=acceptance_gate.MAX_INPUT_BYTES,
        )
        evidence = build_evidence(
            source,
            contract,
            expected_commit=args.expected_commit,
        )
        write_json_atomic(args.output, evidence)
    except EvidenceBuildError as exc:
        print(
            json.dumps(
                {"failure_codes": list(exc.failure_codes), "success": False},
                ensure_ascii=True,
                sort_keys=True,
            )
        )
        return 1
    print(
        json.dumps(
            {
                "model_id": evidence["model_id"],
                "output": str(args.output),
                "sample_count": evidence["metrics"]["sample_count"],
                "success": True,
            },
            ensure_ascii=True,
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
