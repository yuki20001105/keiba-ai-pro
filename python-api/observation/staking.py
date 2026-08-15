from __future__ import annotations

import json
import math
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable, Mapping

from .contracts import ObservationContractError, canonical_sha256


ROOT = Path(__file__).resolve().parents[2]
DEFAULT_POLICY_PATH = ROOT / "config" / "phase3n_staking_payout_policy.v1.json"
POLICY_KEYS = frozenset(
    {
        "schema",
        "schema_version",
        "policy_id",
        "status",
        "approval_reference",
        "candidate",
        "baseline",
        "payout",
        "costs",
    }
)
POLICY_ID_RE = re.compile(r"^[a-z0-9][a-z0-9.-]{2,127}$")


@dataclass(frozen=True)
class StakingPayoutPolicy:
    policy_id: str
    status: str
    approval_reference: str | None
    policy_sha256: str
    candidate_minimum_expected_value: float
    candidate_stake_yen: int
    baseline_stake_yen: int
    payout_unit_yen: int
    payout_aliases: frozenset[str]
    payout_source_tables: tuple[str, ...]
    transaction_cost_yen_per_wager: int

    @property
    def approved(self) -> bool:
        return self.status == "approved" and bool(self.approval_reference)


@dataclass(frozen=True)
class StakingDecision:
    recommendation: str
    qualifying_bet: bool
    wager_amount: float
    baseline_wager_amount: float


def _positive_int(value: Any, *, code: str) -> int:
    if type(value) is not int or value <= 0:
        raise ObservationContractError(code)
    return value


def _finite(value: Any, *, minimum: float, code: str) -> float:
    if type(value) not in (int, float) or not math.isfinite(float(value)):
        raise ObservationContractError(code)
    parsed = float(value)
    if parsed < minimum:
        raise ObservationContractError(code)
    return parsed


def load_staking_payout_policy(
    path: Path | None = None,
    *,
    require_approved: bool = False,
) -> StakingPayoutPolicy:
    source = DEFAULT_POLICY_PATH if path is None else path
    try:
        document = json.loads(source.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ObservationContractError("staking-policy-unavailable-or-invalid") from exc
    if not isinstance(document, dict) or frozenset(document) != POLICY_KEYS:
        raise ObservationContractError("staking-policy-schema-invalid")
    if document["schema"] != "phase3n-staking-payout-policy" or document["schema_version"] != 1:
        raise ObservationContractError("staking-policy-schema-invalid")
    policy_id = document["policy_id"]
    if not isinstance(policy_id, str) or POLICY_ID_RE.fullmatch(policy_id) is None:
        raise ObservationContractError("staking-policy-id-invalid")
    status = document["status"]
    if status not in {"draft", "approved"}:
        raise ObservationContractError("staking-policy-status-invalid")
    approval_reference = document["approval_reference"]
    if approval_reference is not None and (
        not isinstance(approval_reference, str)
        or not approval_reference.startswith("https://github.com/")
    ):
        raise ObservationContractError("staking-policy-approval-reference-invalid")
    if status == "approved" and not approval_reference:
        raise ObservationContractError("staking-policy-approval-required")
    if status == "draft" and approval_reference is not None:
        raise ObservationContractError("staking-policy-draft-has-approval")

    candidate = document["candidate"]
    baseline = document["baseline"]
    payout = document["payout"]
    costs = document["costs"]
    if not all(isinstance(value, dict) for value in (candidate, baseline, payout, costs)):
        raise ObservationContractError("staking-policy-schema-invalid")
    if candidate != {
        "maximum_wagers_per_race": candidate.get("maximum_wagers_per_race"),
        "minimum_expected_value": candidate.get("minimum_expected_value"),
        "selection": candidate.get("selection"),
        "stake_yen": candidate.get("stake_yen"),
    } or baseline != {
        "maximum_wagers_per_race": baseline.get("maximum_wagers_per_race"),
        "selection": baseline.get("selection"),
        "stake_yen": baseline.get("stake_yen"),
    }:
        raise ObservationContractError("staking-policy-schema-invalid")
    if candidate["selection"] != "highest_model_expected_value":
        raise ObservationContractError("staking-policy-candidate-selection-invalid")
    if baseline["selection"] != "lowest_valid_win_odds":
        raise ObservationContractError("staking-policy-baseline-selection-invalid")
    if candidate["maximum_wagers_per_race"] != 1 or baseline["maximum_wagers_per_race"] != 1:
        raise ObservationContractError("staking-policy-wager-limit-invalid")
    candidate_stake = _positive_int(candidate["stake_yen"], code="staking-policy-stake-invalid")
    baseline_stake = _positive_int(baseline["stake_yen"], code="staking-policy-stake-invalid")
    minimum_ev = _finite(
        candidate["minimum_expected_value"], minimum=1.0, code="staking-policy-ev-invalid"
    )
    if frozenset(payout) != {"bet_type", "bet_type_aliases", "source_tables", "unit_yen"}:
        raise ObservationContractError("staking-policy-payout-invalid")
    if payout["bet_type"] != "tansho":
        raise ObservationContractError("staking-policy-payout-invalid")
    aliases = payout["bet_type_aliases"]
    tables = payout["source_tables"]
    if (
        not isinstance(aliases, list)
        or not aliases
        or any(not isinstance(value, str) or not value.strip() for value in aliases)
        or not isinstance(tables, list)
        or tables != ["race_payouts", "payouts"]
    ):
        raise ObservationContractError("staking-policy-payout-invalid")
    payout_unit = _positive_int(payout["unit_yen"], code="staking-policy-payout-invalid")
    if frozenset(costs) != {"transaction_cost_yen_per_wager"}:
        raise ObservationContractError("staking-policy-cost-invalid")
    transaction_cost = costs["transaction_cost_yen_per_wager"]
    if type(transaction_cost) is not int or transaction_cost < 0:
        raise ObservationContractError("staking-policy-cost-invalid")

    policy = StakingPayoutPolicy(
        policy_id=policy_id,
        status=status,
        approval_reference=approval_reference,
        policy_sha256=canonical_sha256(document),
        candidate_minimum_expected_value=minimum_ev,
        candidate_stake_yen=candidate_stake,
        baseline_stake_yen=baseline_stake,
        payout_unit_yen=payout_unit,
        payout_aliases=frozenset(value.strip().casefold() for value in aliases),
        payout_source_tables=tuple(tables),
        transaction_cost_yen_per_wager=transaction_cost,
    )
    if require_approved and not policy.approved:
        raise ObservationContractError("staking-policy-not-approved")
    return policy


def _horse_number(row: Mapping[str, Any]) -> int:
    try:
        number = int(row.get("horse_number") or row.get("horse_no") or 0)
    except (TypeError, ValueError) as exc:
        raise ObservationContractError("staking-policy-horse-number-invalid") from exc
    if not 1 <= number <= 99:
        raise ObservationContractError("staking-policy-horse-number-invalid")
    return number


def _odds(row: Mapping[str, Any]) -> float | None:
    value = row.get("odds")
    if type(value) not in (int, float) or not math.isfinite(float(value)) or float(value) <= 1.0:
        return None
    return float(value)


def build_staking_decisions(
    predictions: Iterable[Mapping[str, Any]],
    policy: StakingPayoutPolicy,
) -> dict[int, StakingDecision]:
    rows = [dict(row) for row in predictions]
    numbers = [_horse_number(row) for row in rows]
    if len(numbers) != len(set(numbers)):
        raise ObservationContractError("staking-policy-horse-number-duplicate")
    decisions = {
        number: StakingDecision("pass", False, 0.0, 0.0) for number in numbers
    }
    if not policy.approved:
        return decisions

    valid = [(row, _horse_number(row), _odds(row)) for row in rows]
    valid = [(row, number, odds) for row, number, odds in valid if odds is not None]
    if not valid:
        return decisions
    baseline_number = min(valid, key=lambda item: (float(item[2]), item[1]))[1]

    def candidate_key(item: tuple[dict[str, Any], int, float]) -> tuple[float, float, int]:
        row, number, odds = item
        probability = row.get("p_norm")
        if type(probability) not in (int, float):
            probability = row.get("win_probability")
        if type(probability) not in (int, float) or not math.isfinite(float(probability)):
            probability = 0.0
        return (float(probability) * odds, float(probability), -number)

    candidate = max(valid, key=candidate_key)
    candidate_number = candidate[1]
    candidate_ev = candidate_key(candidate)[0]
    for number in numbers:
        baseline_wager = float(policy.baseline_stake_yen) if number == baseline_number else 0.0
        qualifies = number == candidate_number and candidate_ev >= policy.candidate_minimum_expected_value
        decisions[number] = StakingDecision(
            recommendation="bet" if qualifies else "pass",
            qualifying_bet=qualifies,
            wager_amount=float(policy.candidate_stake_yen) if qualifies else 0.0,
            baseline_wager_amount=baseline_wager,
        )
    return decisions


def normalize_combination(value: Any) -> str:
    digits = re.findall(r"\d+", str(value or ""))
    if len(digits) != 1:
        return ""
    return str(int(digits[0]))


def resolve_tansho_payout(
    payout_rows: Iterable[Mapping[str, Any]],
    *,
    horse_number: int,
    policy: StakingPayoutPolicy,
) -> float:
    matches: set[float] = set()
    target = str(horse_number)
    for row in payout_rows:
        bet_type = str(row.get("bet_type") or "").strip().casefold()
        if bet_type not in policy.payout_aliases or normalize_combination(row.get("combination")) != target:
            continue
        payout = row.get("payout")
        if type(payout) not in (int, float) or not math.isfinite(float(payout)) or float(payout) <= 0:
            raise ObservationContractError("staking-payout-value-invalid")
        matches.add(float(payout))
    if not matches:
        raise ObservationContractError("staking-payout-missing")
    if len(matches) != 1:
        raise ObservationContractError("staking-payout-conflict")
    return next(iter(matches))


def settled_returns(
    prediction: Mapping[str, Any],
    *,
    finish_order: int,
    payout_rows: Iterable[Mapping[str, Any]],
    policy: StakingPayoutPolicy,
) -> tuple[str, float, float]:
    candidate_wager = float(prediction.get("wager_amount") or 0.0)
    baseline_wager = float(prediction.get("baseline_wager_amount") or 0.0)
    if candidate_wager <= 0 and baseline_wager <= 0:
        return "not-bet", 0.0, 0.0
    if finish_order != 1:
        return ("lost" if candidate_wager > 0 else "not-bet"), 0.0, 0.0
    payout = resolve_tansho_payout(
        payout_rows,
        horse_number=int(prediction["horse_number"]),
        policy=policy,
    )
    candidate_return = candidate_wager / policy.payout_unit_yen * payout
    baseline_return = baseline_wager / policy.payout_unit_yen * payout
    return (
        "won" if candidate_wager > 0 else "not-bet",
        candidate_return,
        baseline_return,
    )
