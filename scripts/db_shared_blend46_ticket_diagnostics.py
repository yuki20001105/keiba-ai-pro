"""Fixed rank-only ticket diagnostics; never odds, probability or purchase APIs.

The caller supplies evidence that a race's payout page is complete.  This
module independently requires all six types and exactly the winning horse
combinations implied by the complete outcome.  Only then is an absent chosen
combination a confirmed losing ticket.  Unverified payouts remain unknown.
"""
from __future__ import annotations

from collections.abc import Collection
from itertools import combinations, permutations
import re

import numpy as np
import pandas as pd


SCHEMA = "db-shared-blend46-rank-ticket-diagnostics-v1"
STAKE_YEN = 100
KINDS = ("単勝", "馬連", "馬単", "ワイド", "三連複", "三連単")
RULES = {
    "単勝": "win_top1", "馬連": "quinella_top1_top2",
    "馬単": "exacta_top1_to_top2", "ワイド": "wide_top1_top2",
    "三連複": "trio_top1_top2_top3", "三連単": "trifecta_top1_to_top2_to_top3",
}
ALIASES = {**{kind: kind for kind in KINDS}, "3連複": "三連複", "3連単": "三連単"}
ORDERED = frozenset(("馬単", "三連単"))
WIDTH = {"単勝": 1, "馬連": 2, "馬単": 2, "ワイド": 2, "三連複": 3, "三連単": 3}
RULE_SOURCES = (
    "https://www.jra.go.jp/faq/pop03/1_12.html",
    "https://www.jra.go.jp/kouza/beginner/baken/",
)


def _identity(values: pd.Series, name: str) -> pd.Series:
    if values.isna().any():
        raise ValueError(f"ticket-diagnostics-missing-{name}")
    result = values.astype(str)
    if result.str.strip().eq("").any() or not result.eq(result.str.strip()).all():
        raise ValueError(f"ticket-diagnostics-invalid-{name}")
    return result


def _integer_column(values: pd.Series, name: str, low: int, high: int) -> pd.Series:
    numeric = pd.to_numeric(values, errors="coerce")
    if not (np.isfinite(numeric.to_numpy(dtype=float)).all()
            and numeric.between(low, high).all() and numeric.eq(np.floor(numeric)).all()):
        raise ValueError(f"ticket-diagnostics-invalid-{name}")
    return numeric.astype(int)


def _frame(frame: pd.DataFrame) -> pd.DataFrame:
    required = {"race_id", "race_date", "horse_number", "raw_score", "finish"}
    if not isinstance(frame, pd.DataFrame) or frame.empty or frame.columns.duplicated().any() or not required <= set(frame):
        raise ValueError("ticket-diagnostics-complete-runner-frame-required")
    # Extra fields (including odds or probabilities) are deliberately never read.
    value = frame.loc[:, sorted(required)].copy()
    value["race_id"] = _identity(value.race_id, "race-id")
    value["race_date"] = _identity(value.race_date, "race-date")
    if not value.race_date.str.fullmatch(r"\d{8}").all():
        raise ValueError("ticket-diagnostics-invalid-race-date")
    try:
        pd.to_datetime(value.race_date, format="%Y%m%d", errors="raise")
    except (ValueError, TypeError) as error:
        raise ValueError("ticket-diagnostics-invalid-race-date") from error
    value["horse_number"] = _integer_column(value.horse_number, "horse-number", 1, 18)
    value["finish"] = _integer_column(value.finish, "finish", 1, 18)
    value["raw_score"] = pd.to_numeric(value.raw_score, errors="coerce")
    if not np.isfinite(value.raw_score.to_numpy(dtype=float)).all():
        raise ValueError("ticket-diagnostics-nonfinite-raw-score")
    if value.duplicated(["race_id", "horse_number"]).any():
        raise ValueError("ticket-diagnostics-duplicate-runner")
    for _, part in value.groupby("race_id", sort=False):
        if not 3 <= len(part) <= 18 or part.race_date.nunique() != 1:
            raise ValueError("ticket-diagnostics-incomplete-race-or-conflicting-date")
        # Competition ranking allows ties but not missing finishers: 1, 1, 3.
        position = 1
        for rank, members in part.groupby("finish", sort=True):
            if int(rank) != position:
                raise ValueError("ticket-diagnostics-incomplete-or-invalid-finish-ranking")
            position += len(members)
    return value


def _verification(frame: pd.DataFrame, race_ids: set[str], verified: Collection[str] | None) -> set[str]:
    flags = None
    if "race_payout_complete" in frame:
        raw = frame.race_payout_complete
        if raw.isna().any() or not raw.map(lambda item: isinstance(item, (bool, np.bool_))).all():
            raise ValueError("ticket-diagnostics-boolean-payout-proof-required")
        view = pd.DataFrame({"race_id": frame.race_id.astype(str), "complete": raw.to_numpy(bool)})
        grouped = view.groupby("race_id").complete
        if grouped.nunique().ne(1).any():
            raise ValueError("ticket-diagnostics-conflicting-payout-proof")
        flags = set(grouped.first().loc[lambda rows: rows].index)
    if verified is None:
        if flags is None:
            raise ValueError("ticket-diagnostics-explicit-payout-completeness-proof-required")
        return flags
    if isinstance(verified, (str, bytes)) or not isinstance(verified, Collection):
        raise ValueError("ticket-diagnostics-verified-race-id-collection-required")
    identities = set()
    for identity in verified:
        if not isinstance(identity, str) or not identity or identity.strip() != identity:
            raise ValueError("ticket-diagnostics-invalid-verified-race-id")
        identities.add(identity)
    if flags is not None and (identities & race_ids) != flags:
        raise ValueError("ticket-diagnostics-payout-proof-disagreement")
    return identities & race_ids


def _parse_combination(raw, kind: str) -> tuple[int, ...]:
    if pd.isna(raw):
        raise ValueError("ticket-diagnostics-missing-payout-combination")
    text = str(raw).strip()
    # '-' in an ordered type retains written order, never sorted.  netkeiba
    # currently uses arrows for ordered and hyphens for unordered combinations.
    separator = r"(?:→|-)" if kind in ORDERED else "-"
    pattern = r"\d+" + (rf"\s*{separator}\s*\d+" * (WIDTH[kind] - 1))
    if not re.fullmatch(pattern, text):
        raise ValueError("ticket-diagnostics-invalid-payout-combination")
    values = tuple(int(number) for number in re.split(r"\s*(?:→|-)\s*", text))
    if len(set(values)) != len(values) or not all(1 <= number <= 18 for number in values):
        raise ValueError("ticket-diagnostics-invalid-payout-horse-number")
    return values if kind in ORDERED else tuple(sorted(values))


def _prefixes(part: pd.DataFrame, length: int) -> set[tuple[int, ...]]:
    """Allocate the first slots in rank order; tied horses can swap slots."""
    prefixes = {()}
    for _, group in part.groupby("finish", sort=True):
        needed = min(len(group), length - len(next(iter(prefixes))))
        candidates = list(permutations(sorted(group.horse_number.tolist()), needed))
        prefixes = {prefix + candidate for prefix in prefixes for candidate in candidates}
        if len(next(iter(prefixes))) == length:
            return prefixes
    raise ValueError("ticket-diagnostics-insufficient-confirmed-finishers")


def winning_combinations(part: pd.DataFrame) -> dict[str, set[tuple[int, ...]]]:
    """JRA's slot assignment, including boundary ties and third/third wide exclusion.

    Input is a single complete race already validated by diagnose_rank_tickets.
    No payout is calculated here: official stored dividends remain authoritative.
    """
    pair, triple = _prefixes(part, 2), _prefixes(part, 3)
    return {
        "単勝": {(int(number),) for number in part.loc[part.finish.eq(1), "horse_number"]},
        "馬連": {tuple(sorted(numbers)) for numbers in pair}, "馬単": pair,
        "ワイド": {tuple(sorted(numbers)) for prefix in triple for numbers in combinations(prefix, 2)},
        "三連複": {tuple(sorted(numbers)) for numbers in triple}, "三連単": triple,
    }


def _payouts(table: pd.DataFrame, race_ids: set[str]):
    required = {"race_id", "bet_type", "combinations", "payout"}
    if not isinstance(table, pd.DataFrame) or table.columns.duplicated().any() or not required <= set(table):
        raise ValueError("ticket-diagnostics-payout-table-required")
    source = table.loc[table.race_id.astype(str).isin(race_ids), sorted(required)].copy()
    source["race_id"] = _identity(source.race_id, "payout-race-id")
    # Full DB payout tables also contain place and bracket quinella.  They do
    # not enter these six fixed diagnostics and cannot provide their proof.
    source = source.loc[source.bet_type.isin(ALIASES)].copy()
    source["bet_type"] = source.bet_type.map(ALIASES)
    values = pd.to_numeric(source.payout, errors="coerce")
    if not (np.isfinite(values.to_numpy(float)).all() and values.ge(100).all() and values.mod(10).eq(0).all()):
        raise ValueError("ticket-diagnostics-invalid-official-payout")
    source["payout"] = values.astype(int)
    lookup, duplicate_count = {}, 0
    for row in source.itertuples(index=False):
        combo = _parse_combination(row.combinations, row.bet_type)
        key = (row.race_id, row.bet_type, combo)
        if key in lookup:
            if lookup[key] != row.payout:
                raise ValueError("ticket-diagnostics-conflicting-duplicate-payout")
            duplicate_count += 1
        else:
            lookup[key] = row.payout
    per_race = {}
    for (race_id, kind, combo), payout in lookup.items():
        per_race.setdefault(race_id, {}).setdefault(kind, {})[combo] = payout
    return per_race, duplicate_count


def _summary(part: pd.DataFrame) -> dict:
    count = len(part)
    known = part.payout_complete.eq(True)
    missing = int((~known).sum())
    good = part.loc[known]
    complete = missing == 0
    returns = good.payout_yen.to_numpy(float)
    total = float(returns.sum())
    ranked = np.sort(returns)[::-1]
    daily = good.groupby("race_date", sort=True).agg(
        stake_yen=("stake_yen", "sum"), return_yen=("payout_yen", "sum"))
    daily_profit = (daily.return_yen - daily.stake_yen).to_numpy(float)
    cumulative = np.concatenate(([0.], np.cumsum(daily_profit)))
    drawdown = np.maximum.accumulate(cumulative) - cumulative
    return {
        "races": int(part.race_id.nunique()), "race_dates": int(part.race_date.nunique()),
        "bets": count, "stake_yen": count * STAKE_YEN,
        "confirmed_bets": len(good), "missing_payout_bets": missing,
        "confirmed_return_yen": int(total),
        "return_yen": int(total) if complete else None,
        "profit_yen": int(total - count * STAKE_YEN) if complete else None,
        "roi_percent": total / count if complete and count else None,
        "hit_count": int(good.hit.sum()) if complete else None,
        "hit_rate": float(good.hit.mean()) if complete and count else None,
        "maximum_drawdown_daily_yen": float(drawdown.max()) if complete and count else None,
        "maximum_payout_yen": float(ranked[0]) if complete and count else None,
        "top1_share_of_return": float(ranked[0] / total) if complete and total else None,
        "top3_share_of_return": float(ranked[:3].sum() / total) if complete and total else None,
        "roi_excluding_largest_ticket_percent": float(ranked[1:].sum() / (count - 1)) if complete and count > 1 else None,
        "roi_excluding_top3_tickets_percent": float(ranked[3:].sum() / (count - 3)) if complete and count > 3 else None,
        "roi_evaluable": complete and count > 0,
        "status": "retrospective_fixed_rank_diagnostic" if complete else "unknown_payouts_not_losses",
    }


def diagnose_rank_tickets(
    frame: pd.DataFrame, payout_table: pd.DataFrame, *,
    expected_verified_race_ids: Collection[str] | None = None,
    return_ledger: bool = False,
) -> dict:
    """Compare six pre-fixed rank tickets without selecting a winning strategy.

    Proof is mandatory: supply expected_verified_race_ids or a consistent
    boolean race_payout_complete column on all runners.  The caller's source
    attestation is necessary but not sufficient; all six full winning sets
    must also match outcomes.  False/unverified races are retained as unknown,
    and any aggregation containing them has no ROI rather than a zero return.

    No odds, probability, amount optimization or purchase recommendation is
    accepted.  Dates may be filtered by the caller for separate SELECT/TEST
    summaries.  Intra-day order is not proven, so drawdown uses daily totals.
    """
    value = _frame(frame)
    race_ids = set(value.race_id)
    verified = _verification(frame, race_ids, expected_verified_race_ids)
    payouts, duplicate_count = _payouts(payout_table, race_ids)
    ledger, dead_heat_races = [], 0
    for race_id, part in value.groupby("race_id", sort=True):
        complete = race_id in verified
        actual = payouts.get(race_id, {})
        expected = winning_combinations(part)
        dead_heat_races += int(part.finish.duplicated().any())
        horses = set(part.horse_number)
        if any(not set(combo) <= horses for entries in actual.values() for combo in entries):
            raise ValueError("ticket-diagnostics-payout-horse-not-in-race")
        if complete:
            if set(actual) != set(KINDS):
                raise ValueError(f"ticket-diagnostics-complete-proof-missing-bet-type:{race_id}")
            for kind in KINDS:
                if set(actual[kind]) != expected[kind]:
                    raise ValueError(f"ticket-diagnostics-winning-combination-set-mismatch:{race_id}:{kind}")
        leading = part.sort_values(["raw_score", "horse_number"], ascending=[False, True], kind="stable").horse_number.tolist()[:3]
        date = str(part.race_date.iloc[0])
        for kind in KINDS:
            selection = tuple(leading[:WIDTH[kind]])
            if kind not in ORDERED:
                selection = tuple(sorted(selection))
            hit = selection in expected[kind] if complete else None
            paid = actual[kind].get(selection, 0) if complete else np.nan
            ledger.append({"race_id": race_id, "race_date": date, "year": int(date[:4]),
                "season": f"{date[:4]}-Q{(int(date[4:6]) - 1) // 3 + 1}",
                "bet_type": kind, "rule": RULES[kind],
                "horses": ("→" if kind in ORDERED else "-").join(map(str, selection)),
                "stake_yen": STAKE_YEN, "payout_yen": paid,
                "profit_yen": paid - STAKE_YEN, "hit": hit, "payout_complete": complete,
                "missing_reason": None if complete else "race_payout_completeness_unverified"})
    tickets = pd.DataFrame(ledger)
    totals, yearly, seasonal = [], [], []
    for kind in KINDS:
        portion = tickets.loc[tickets.bet_type.eq(kind)]
        totals.append({"bet_type": kind, "rule": RULES[kind], **_summary(portion)})
        for year, group in portion.groupby("year", sort=True):
            yearly.append({"year": int(year), "bet_type": kind, "rule": RULES[kind], **_summary(group)})
        for season, group in portion.groupby("season", sort=True):
            seasonal.append({"season": season, "bet_type": kind, "rule": RULES[kind], **_summary(group)})
    result = {"schema": SCHEMA, "totals": totals, "by_year": yearly, "by_season": seasonal,
        "metadata": {"races": len(race_ids), "verified_payout_races": len(verified),
            "unverified_payout_races": len(race_ids - verified), "dead_heat_races": dead_heat_races,
            "identical_duplicate_payout_rows": duplicate_count, "stake_per_ticket_yen": STAKE_YEN,
            "one_ticket_per_race_per_bet_type": True,
            "six_separate_strategies_not_one_600_yen_recommendation": True,
            "rank_selection": "raw_score descending; exact ties horse_number ascending",
            "odds_used": False, "probabilities_used": False, "expected_value_available": False,
            "historical_payout_source": "caller-provided stored dividends; not directly JRA verified by this helper",
            "payout_set_independently_verified_against_outcome": True,
            "missing_payout_not_zero_filled": True, "roi_used_for_selection": False,
            "research_only": True, "live_purchase_allowed": False, "eligible_for_ev": False,
            "drawdown_definition": "daily cumulative profit; initial peak zero; not intraday maximum",
            "payout_removal_definition": "remove largest return ticket(s) and their 100 yen stake(s)",
            "dead_heat_rule_sources": list(RULE_SOURCES)}}
    if return_ledger:
        result["ledger"] = tickets.astype(object).where(pd.notna(tickets), None).to_dict("records")
    return result
