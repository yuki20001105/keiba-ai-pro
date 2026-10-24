"""Read-only target-cohort runner and official win-payout reconciliation."""
from __future__ import annotations

from collections import defaultdict
import hashlib
import json
import math
from pathlib import Path
import re
import sqlite3

import numpy as np
import pandas as pd


def _number(value):
    if isinstance(value, bool):
        return math.nan
    try:
        return float(value)
    except (TypeError, ValueError, OverflowError):
        return math.nan


def _integer(value, low=1, high=18):
    value = _number(value)
    return math.isfinite(value) and value == int(value) and low <= value <= high


def _dump(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


SAVED_COLUMNS = {"race_id", "horse_id", "horse_number", "race_date", "finish", "odds", "popularity", "tansho_payout"}


def _saved_frame(part, name, race_id, problems):
    """Keep corrupt rows visible, coercing only an audit copy, not source data."""
    result = part.copy()
    missing = SAVED_COLUMNS - set(result)
    if missing or result.columns.duplicated().any():
        problems.append(name + "_schema_invalid")
        result = result.loc[:, ~result.columns.duplicated()].copy()
        for column in missing:
            result[column] = np.nan
    if not result.race_id.astype(str).eq(race_id).all():
        problems.append(name + "_race_id_mismatch")
    identities = result.horse_id
    if identities.isna().any() or identities.astype(str).str.strip().eq("").any():
        problems.append(name + "_identity_missing")
    result["horse_id"] = identities.fillna("").astype(str)
    original_payout = result.tansho_payout.copy()
    for column in ("horse_number", "finish", "odds", "popularity", "tansho_payout"):
        result[column] = result[column].map(_number)
    result.attrs["invalid_saved_payout"] = bool((original_payout.notna() & ~np.isfinite(result.tansho_payout)).any())
    if not result.horse_number.map(_integer).all():
        problems.append(name + "_horse_number_invalid")
    return result


def read_scoped_source(snapshot_path, race_ids):
    """Stream only declared 2021/22 races. No source mutation or other-year rows."""
    path = Path(snapshot_path)
    ids = sorted(set(map(str, race_ids)))
    if path.is_symlink() or not path.is_file() or not ids or any(not re.fullmatch(r"202[12]\d{8}", x) for x in ids):
        raise ValueError("immutable-source-and-2021-2022-cohort-required")
    wal = Path(str(path) + "-wal")
    if wal.exists() and wal.stat().st_size:
        raise ValueError("immutable-snapshot-has-WAL")
    output = {"races": defaultdict(list), "results": defaultdict(list), "payouts": defaultdict(list)}
    digest = hashlib.sha256()
    with sqlite3.connect(path.resolve().as_uri() + "?mode=ro&immutable=1", uri=True) as conn:
        conn.execute("PRAGMA query_only=ON")
        for offset in range(0, len(ids), 300):
            chunk = ids[offset:offset + 300]
            marks = ",".join("?" for _ in chunk)
            queries = {
                "races": f"SELECT race_id,data,created_at FROM races_ultimate WHERE race_id IN ({marks}) ORDER BY race_id",
                "results": f"SELECT race_id,id,data,created_at FROM race_results_ultimate WHERE race_id IN ({marks}) ORDER BY race_id,id",
                "payouts": f"SELECT race_id,id,bet_type,combinations,payout,popularity,created_at FROM return_tables_ultimate WHERE race_id IN ({marks}) AND bet_type='単勝' ORDER BY race_id,id",
            }
            for kind, sql in queries.items():
                for row in conn.execute(sql, chunk):
                    digest.update((_dump([kind, *row]) + "\n").encode("utf-8"))
                    output[kind][str(row[0])].append(row)
    return output, {"source": str(path.resolve()), "scoped_raw_sha256": digest.hexdigest(),
                    "scope_races": len(ids), "post_2022_rows_read": False,
                    "row_counts": {k: sum(map(len, v.values())) for k, v in output.items()}}


def audit_race(race_id, cohort, oof, race_rows, raw_results, payouts):
    """Return per-race integrity and payout rows; malformed rows never disappear."""
    from experiments.dataset import _race_metadata, _runner
    problems, parsed, raw_states = [], [], []
    cohort = _saved_frame(cohort, "input", race_id, problems)
    oof = _saved_frame(oof, "oof", race_id, problems)
    meta = None
    if len(race_rows) != 1:
        problems.append("race_header_missing_or_duplicate")
    else:
        try:
            if str(race_rows[0][0]) != race_id:
                problems.append("race_header_outer_id_mismatch")
            meta, reason = _race_metadata(race_id, json.loads(race_rows[0][1]))
            if reason:
                problems.append("race_header:" + reason)
        except (ValueError, TypeError, AttributeError, IndexError):
            problems.append("race_header_invalid_json")
    invalid_rows = []
    for source_row in raw_results:
        row_id = source_row[1] if len(source_row) > 1 else None
        try:
            _rid, row_id, payload, _created = source_row
            if str(_rid) != race_id:
                invalid_rows.append({"row_id": row_id, "reason": "runner_outer_race_id_mismatch"})
            raw = json.loads(payload)
            state = next((raw[k] for k in ("finish", "finish_position")
                          if raw.get(k) is not None and str(raw[k]).strip()), None)
            both_finish = [raw[k] for k in ("finish", "finish_position")
                           if raw.get(k) is not None and str(raw[k]).strip()]
            if len(both_finish) == 2 and str(both_finish[0]) != str(both_finish[1]):
                if not (_integer(both_finish[0]) and _number(both_finish[0]) == _number(both_finish[1])):
                    invalid_rows.append({"row_id": row_id, "reason": "finish_alias_conflict"})
            raw_states.append({"row_id": row_id, "horse_number": raw.get("horse_number"), "finish": state})
            record, reason = _runner(race_id, raw, meta) if meta else (None, "race_metadata_missing")
            if reason:
                invalid_rows.append({"row_id": row_id, "reason": reason})
            else:
                parsed.append(record)
        except (ValueError, TypeError, AttributeError):
            invalid_rows.append({"row_id": row_id, "reason": "runner_json_invalid"})
    if invalid_rows:
        problems.append("invalid_raw_runner")
    cancelled = [r for r in raw_states if not _integer(r["finish"])]
    if cancelled:
        problems.append("cancelled_excluded_or_unsettled_runner")
    raw = pd.DataFrame(parsed)
    duplicate_id = duplicate_number = 0
    sets_equal = False
    compare_mismatch = []
    if not raw.empty:
        duplicate_id = int(raw.duplicated("horse_id", keep=False).sum())
        duplicate_number = int(raw.duplicated("horse_number", keep=False).sum())
        if duplicate_id or duplicate_number or raw.horse_id.eq("").any():
            problems.append("raw_duplicate_or_missing_identity")
        if not raw.finish.map(lambda x: _integer(x, high=len(raw))).all():
            problems.append("invalid_finish")
        elif not raw.finish.eq(raw.finish.rank(method="min")).all():
            problems.append("finish_competition_rank_invalid")
        if not np.isfinite(raw.time_seconds).all() or not raw.time_seconds.gt(0).all():
            problems.append("invalid_finish_time")
        if not raw.popularity.map(lambda x: _integer(x, high=len(raw))).all():
            problems.append("invalid_popularity")
        elif not raw.popularity.eq(raw.popularity.rank(method="min")).all():
            problems.append("popularity_competition_rank_invalid")
        # Official competition ranks permit ties (1,2,2,4), unlike runner IDs.
        # Tied displayed popularity must refer to equal displayed odds; equal
        # rounded odds with distinct popularity may reflect unrounded odds.
        tied_popularity = raw.loc[raw.popularity.duplicated(keep=False)]
        if not tied_popularity.empty and tied_popularity.groupby("popularity").odds.nunique(dropna=False).gt(1).any():
            problems.append("popularity_tie_odds_conflict")
        if not np.isfinite(raw.odds).all() or not raw.odds.ge(1).all():
            problems.append("invalid_odds")
        if meta and (int(meta["num_horses"]) != len(raw_results) or len(cohort) != len(raw_results)
                     or len(oof) != len(raw_results)):
            problems.append("declared_runner_count_mismatch")
        def identity(frame):
            return set(zip(frame.horse_id.astype(str), frame.horse_number.astype(float)))
        sets_equal = identity(raw) == identity(cohort) == identity(oof)
        if not sets_equal:
            problems.append("runner_identity_sets_mismatch")
        for name, part in (("input", cohort), ("oof", oof)):
            if part.duplicated("horse_id").any() or part.duplicated("horse_number").any():
                problems.append(name + "_duplicate_runner")
            if not duplicate_number and not part.duplicated("horse_number").any():
                aligned = part.set_index("horse_number").reindex(raw.horse_number)
                for field in ("finish", "odds", "popularity"):
                    if field not in aligned or not np.isclose(raw[field].to_numpy(float), aligned[field].to_numpy(float), rtol=0, atol=1e-12, equal_nan=False).all():
                        compare_mismatch.append(name + ":" + field)
                if "race_date" not in aligned or not aligned.race_date.astype(str).eq(str(meta["race_date"])).all():
                    compare_mismatch.append(name + ":race_date")
        if compare_mismatch:
            problems.append("raw_vs_saved_value_conflict")
    else:
        problems.append("raw_results_missing")
    integrity = {"race_id": race_id, "raw_runner_count": len(raw_results), "model_input_count": len(cohort),
        "oof_count": len(oof), "market_count": int(cohort.odds.notna().sum()), "result_count": len(parsed),
        "declared_runner_count": int(meta["num_horses"]) if meta else None,
        "raw_duplicate_identity_rows": duplicate_id, "raw_duplicate_number_rows": duplicate_number,
        "identity_sets_equal": sets_equal, "cancelled_excluded_or_unsettled": len(cancelled),
        "cancelled_excluded_json": _dump(cancelled), "invalid_rows_json": _dump(invalid_rows),
        "value_conflicts_json": _dump(compare_mismatch), "issues_json": _dump(sorted(set(problems))),
        "runner_pass": not problems}
    payout_issues = []
    for name, part in (("input", cohort), ("oof", oof)):
        if part.attrs.get("invalid_saved_payout"):
            payout_issues.append(name + "_payout_value_invalid")
    winners = [] if raw.empty else raw.loc[raw.finish.eq(1), "horse_number"].astype(int).tolist()
    if len(winners) != 1:
        payout_issues.append("winner_missing_or_multiple")
    payout_rows = []
    for source_row in payouts:
        if len(source_row) != 7:
            payout_issues.append("payout_row_malformed")
            continue
        _rid, row_id, _kind, combo, value, popularity, created_at = source_row
        if str(_rid) != race_id or _kind != "単勝":
            payout_issues.append("payout_race_or_bet_type_mismatch")
        number = int(str(combo).strip()) if re.fullmatch(r"\d{1,2}", str(combo).strip()) else None
        valid = _integer(value, low=100, high=1_000_000_000) and _number(value) % 10 == 0
        payout_rows.append({"row_id": row_id, "horse_number": number, "payout": value,
                            "popularity": popularity, "created_at": created_at, "valid_amount": valid})
        if number not in winners:
            payout_issues.append("payout_runner_not_winner")
        if not valid:
            payout_issues.append("invalid_or_nonpositive_payout")
    if len(payout_rows) != 1:
        payout_issues.append("payout_missing_or_duplicate")
    equal = False
    if (len(winners) == 1 and len(payout_rows) == 1 and payout_rows[0]["valid_amount"]
            and payout_rows[0]["horse_number"] == winners[0]):
        winner = winners[0]
        records = cohort.loc[cohort.horse_number.eq(winner)]
        equal = len(records) == 1 and _number(records.iloc[0].tansho_payout) == _number(payout_rows[0]["payout"])
        oof_winner = oof.loc[oof.horse_number.eq(winner)]
        equal &= len(oof_winner) == 1 and _number(oof_winner.iloc[0].tansho_payout) == _number(payout_rows[0]["payout"])
        if not equal:
            payout_issues.append("ROI_source_payout_mismatch")
        for label, part in (("input", cohort), ("oof", oof)):
            losers = part.loc[~part.horse_number.eq(winner), "tansho_payout"]
            if not (losers.isna() | losers.eq(0)).all():
                payout_issues.append(label + "_nonwinner_has_win_payout")
    payout = {"race_id": race_id, "winner_numbers_json": _dump(winners), "winner_count": len(winners),
        "dead_heat": len(winners) > 1, "official_win_payout_rows": len(payout_rows),
        "payout_duplicate": len(payout_rows) > 1, "official_payout_json": _dump(payout_rows),
        "ROI_payout_matches": bool(equal), "payout_winner_set_equal": set(winners) == {p["horse_number"] for p in payout_rows},
        "issues_json": _dump(sorted(set(payout_issues))), "payout_pass": not payout_issues}
    return integrity, payout


def audit_integrity(snapshot_path, cohort, saved_oof):
    if (not SAVED_COLUMNS <= set(cohort) or not SAVED_COLUMNS <= set(saved_oof) or cohort.empty
            or cohort.columns.duplicated().any() or saved_oof.columns.duplicated().any()):
        raise ValueError("complete-model-input-and-oof-required")
    if (not cohort.race_date.astype(str).str.fullmatch(r"202[12]\d{4}").all()
            or not saved_oof.race_date.astype(str).str.fullmatch(r"202[12]\d{4}").all()):
        raise ValueError("post-2022-cohort-forbidden")
    if cohort.race_id.isna().any() or saved_oof.race_id.isna().any():
        raise ValueError("race-id-required-to-assign-audit-records")
    source, receipt = read_scoped_source(snapshot_path, cohort.race_id)
    oof_by_race = {str(key): part for key, part in saved_oof.groupby("race_id", sort=False)}
    integrity, payout = [], []
    for rid, part in cohort.groupby("race_id", sort=True):
        left, right = audit_race(str(rid), part, oof_by_race.get(str(rid), saved_oof.iloc[:0]),
                                 source["races"][str(rid)], source["results"][str(rid)], source["payouts"][str(rid)])
        integrity.append(left)
        payout.append(right)
    runners, payouts = pd.DataFrame(integrity), pd.DataFrame(payout)
    receipt.update({"races": len(runners), "runners": len(cohort),
        "runner_failed_races": int((~runners.runner_pass).sum()), "payout_failed_races": int((~payouts.payout_pass).sum()),
        "duplicate_identity_rows": int(runners.raw_duplicate_identity_rows.sum()),
        "duplicate_number_rows": int(runners.raw_duplicate_number_rows.sum()),
        "cancelled_excluded_or_unsettled_rows": int(runners.cancelled_excluded_or_unsettled.sum()),
        "payout_duplicate_races": int(payouts.payout_duplicate.sum()), "multiple_winner_races": int(payouts.dead_heat.sum())})
    return runners, payouts, receipt
