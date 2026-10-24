"""Premetric, conservative common-cohort and settled-label audit.

Legal dead heats are not corruption. They are explicitly excluded from this
research cohort so ordinary Top2/Top3 events need no dead-heat payout rules.
Stored payouts are outcome evidence, NEVER odds, EV inputs or selection data.
"""
from __future__ import annotations

from collections import Counter, defaultdict
from itertools import combinations
import json
import math
import re

import pandas as pd


BET_TYPES = ("win", "place", "wide", "quinella", "exacta", "trio", "trifecta")
ALIASES = {"単勝": "win", "複勝": "place", "ワイド": "wide", "馬連": "quinella", "馬単": "exacta",
           "3連複": "trio", "三連複": "trio", "3連単": "trifecta", "三連単": "trifecta"}
SIZES = {"win": 1, "place": 1, "wide": 2, "quinella": 2, "exacta": 2, "trio": 3, "trifecta": 3}
PHASES = {"calibration_seed": ("20210101", "20210630"), "meta_fit": ("20210701", "20210930"),
          "development": ("20211001", "20211231"), "validation": ("20220101", "20221231")}


def encoded(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False)


def integer(value, low=1, high=18):
    if isinstance(value, bool):
        return None
    try:
        number = float(value)
    except (TypeError, ValueError, OverflowError):
        return None
    return int(number) if math.isfinite(number) and number.is_integer() and low <= number <= high else None


def parse_combination(value, kind):
    """Reject ambiguous text rather than extracting arbitrary embedded digits."""
    size = SIZES[kind]
    text = str(value).strip()
    # Provider result rows preserve both "12 → 8" and "12-8" for ordered
    # bet types. The BET TYPE determines order; never sort an ordered ticket.
    # Only complete, single-separator numeric grammar is accepted.
    separators = ("→", "-") if kind in {"exacta", "trifecta"} else ("-",)
    separator = next((sep for sep in separators if re.fullmatch(
        r"\d{1,2}" + (r"\s*" + re.escape(sep) + r"\s*\d{1,2}") * (size - 1), text)), None)
    if separator is None:
        return None
    numbers = tuple(integer(part.strip()) for part in text.split(separator))
    if None in numbers or len(set(numbers)) != size:
        return None
    return numbers if kind in {"exacta", "trifecta"} else tuple(sorted(numbers))


def _payout_audit(race_id, ordered, rows, core_valid, has_ties):
    grouped, result = defaultdict(list), []
    for row in rows:
        kind = ALIASES.get(row.get("bet_type"))
        if kind:
            grouped[kind].append(row)
    place_positions = None
    for kind in BET_TYPES:
        source = grouped[kind]
        issues, parsed = [], []
        for row in source:
            combo = parse_combination(row.get("combinations"), kind)
            amount = integer(row.get("payout"), 100, 1_000_000_000)
            if str(row.get("race_id")) != race_id:
                issues.append("payout_race_id_mismatch")
            if combo is None:
                issues.append("payout_combination_malformed")
            elif not set(combo) <= set(ordered):
                issues.append("payout_runner_not_in_cohort")
            if amount is None or amount % 10:
                issues.append("payout_amount_invalid")
            parsed.append(combo)
        if not source:
            issues.append("payout_missing")
        valid_combos = [combo for combo in parsed if combo is not None]
        if len(set(valid_combos)) != len(valid_combos):
            issues.append("payout_combination_duplicate_or_conflicting")
        expected = None
        if core_valid and not has_ties:
            if kind == "win": expected = {(ordered[0],)}
            elif kind == "place":
                # This is an ex-post label check, not evidence of sales-opening
                # headcount. Never infer 2 places solely from 5-7 starters.
                if len(source) in (2, 3) and len(source) <= len(ordered):
                    expected = {(number,) for number in ordered[:len(source)]}
                else:
                    issues.append("place_paid_positions_not_two_or_three")
                if len(ordered) >= 8 and len(source) != 3:
                    issues.append("place_payout_inconsistent_with_at_least_eight_starters")
            elif kind == "wide": expected = {tuple(sorted(pair)) for pair in combinations(ordered[:3], 2)}
            elif kind == "quinella": expected = {tuple(sorted(ordered[:2]))}
            elif kind == "exacta": expected = {tuple(ordered[:2])}
            elif kind == "trio": expected = {tuple(sorted(ordered[:3]))}
            elif kind == "trifecta": expected = {tuple(ordered[:3])}
            if expected is not None and set(valid_combos) != expected:
                issues.append("settled_payout_winning_combinations_mismatch")
        else:
            issues.append("label_not_checked_dead_heat_policy" if has_ties else "label_not_checked_invalid_core")
        passed = not issues and expected is not None
        if kind == "place" and passed:
            place_positions = len(source)
        result.append({"race_id": race_id, "bet_type": kind, "stored_payout_rows": len(source),
            "label_pass": bool(passed), "expected_winning_combinations_json": encoded(sorted(expected)) if expected is not None else None,
            "stored_winning_combinations_json": encoded(valid_combos), "issues_json": encoded(sorted(set(issues))),
            "payout_is_prematch_odds": False, "used_for_predictive_cohort_selection": False})
    return result, place_positions


def audit_eligibility(cohort, race_metadata_rows, payout_rows):
    """Return race eligibility, payout-label audit and phase coverage tables.

    Inputs are already Phase-1-released rows; callers verify that hash chain.
    Metadata/payout mappings contain only the exact declared 2021/22 race IDs.
    No score/probability/market-rank column is required or inspected.
    """
    required = {"race_id", "race_date", "horse_number", "finish", "field_size", "expected_runner_count", "phase"}
    if not required <= set(cohort) or cohort.empty or cohort.columns.duplicated().any():
        raise ValueError("nonempty canonical eligibility schema required")
    frame = cohort[sorted(required)].copy()
    if (not frame.race_id.astype(str).str.fullmatch(r"202[12]\d{8}").all()
            or not frame.race_date.astype(str).str.fullmatch(r"202[12]\d{4}").all()):
        raise ValueError("only 2021/2022 cohort is permitted")
    pd.to_datetime(frame.race_date, format="%Y%m%d", errors="raise")
    scope = set(frame.race_id.astype(str))
    if set(race_metadata_rows) - scope or set(payout_rows) - scope:
        raise ValueError("source mappings include out-of-scope races")
    races, payouts = [], []
    for race_id, part in frame.groupby("race_id", sort=True):
        issues, n = [], len(part)
        date, phase = str(part.race_date.iloc[0]), str(part.phase.iloc[0])
        if part.race_date.nunique(dropna=False) != 1 or part.phase.nunique(dropna=False) != 1:
            issues.append("inconsistent_race_date_or_phase")
        if phase not in PHASES or not PHASES.get(phase, ("", ""))[0] <= date <= PHASES.get(phase, ("", ""))[1]:
            issues.append("phase_date_mismatch")
        numbers = part.horse_number.map(integer)
        finish = part.finish.map(lambda value: integer(value, high=n))
        if numbers.isna().any() or numbers.duplicated().any():
            issues.append("invalid_or_duplicate_runner_number")
        if finish.isna().any() or not finish.eq(finish.rank(method="min")).all():
            issues.append("finish_competition_rank_invalid")
        for column in ("field_size", "expected_runner_count"):
            if not part[column].map(integer).eq(n).all():
                issues.append("incomplete_race:" + column)
        headers = race_metadata_rows.get(str(race_id), [])
        header = None
        if len(headers) != 1:
            issues.append("raw_header_missing_or_duplicate")
        else:
            try:
                header = headers[0]["data"]
                if isinstance(header, str): header = json.loads(header)
                if (str(headers[0]["race_id"]) != str(race_id) or str(header.get("race_id")) != str(race_id)
                        or str(header.get("date")) != date or integer(header.get("num_horses")) != n):
                    issues.append("raw_header_cohort_conflict")
            except (TypeError, ValueError, KeyError, AttributeError):
                issues.append("raw_header_invalid")
        counts = Counter(int(x) for x in finish.dropna())
        ties = sorted(rank for rank, count in counts.items() if count > 1)
        core_valid, has_ties = not issues, bool(ties)
        ordered = [int(num) for _, num in sorted(zip(finish, numbers))] if core_valid else []
        label_rows, place_positions = _payout_audit(str(race_id), ordered, payout_rows.get(str(race_id), []), core_valid, has_ties)
        payouts.extend({**row, "race_date": date, "phase": phase} for row in label_rows)
        reasons = list(issues)
        if has_ties: reasons.append("research_policy_excludes_legal_finish_ties")
        if n < 8: reasons.append("place_sales_opening_count_unproven_for_fewer_than_eight_starters")
        races.append({"race_id": str(race_id), "race_date": date, "phase": phase, "runner_count": n,
            "core_valid": core_valid, "finish_competition_rank_valid": "finish_competition_rank_invalid" not in issues,
            "has_finish_ties": has_ties, "tie_ranks_json": encoded(ties), "top2_tie": any(rank <= 2 for rank in ties),
            "top3_tie": any(rank <= 3 for rank in ties), "sales_opening_runner_count": None,
            "sales_opening_count_verified": False, "place_paid_positions_verified": place_positions,
            "place_main_target_positions": 3 if n >= 8 else None,
            "eligible_ranking": core_valid and not has_ties and n >= 3,
            "common_phase_a_eligible": not reasons,
            "phase_a_payout_labels_complete": all(row["label_pass"] for row in label_rows if row["bet_type"] in {"win", "place", "wide", "quinella"}),
            "core_issues_json": encoded(sorted(set(issues))), "exclusion_reasons_json": encoded(sorted(set(reasons)))})
    race_frame = pd.DataFrame(races).sort_values(["race_date", "race_id"]).reset_index(drop=True)
    payout_frame = pd.DataFrame(payouts).sort_values(["race_date", "race_id", "bet_type"]).reset_index(drop=True)
    coverage = []
    for phase, part in [("all", race_frame), *list(race_frame.groupby("phase", sort=True))]:
        eligible = part.loc[part.common_phase_a_eligible]
        coverage.append({"phase": phase, "source_races": len(part), "source_runners": int(part.runner_count.sum()),
            "core_invalid_races": int((~part.core_valid).sum()), "finish_tie_races": int(part.has_finish_ties.sum()),
            "top2_tie_races": int(part.top2_tie.sum()), "top3_tie_races": int(part.top3_tie.sum()),
            "fewer_than_eight_starters_races": int(part.runner_count.lt(8).sum()),
            "common_eligible_races": len(eligible), "common_eligible_runners": int(eligible.runner_count.sum()),
            "excluded_races": len(part) - len(eligible),
            "common_eligible_complete_phase_a_payout_races": int(eligible.phase_a_payout_labels_complete.sum())})
    return race_frame, payout_frame, pd.DataFrame(coverage)
