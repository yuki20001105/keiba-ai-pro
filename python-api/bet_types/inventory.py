"""Read-only, scoped storage audit. Payouts are never substituted for odds.

No model outputs, future result values, network requests, or production writes
are needed. Cache inspection reads URL metadata, not cached response bodies.
"""
from __future__ import annotations

from collections import Counter, defaultdict
from contextlib import contextmanager
from datetime import datetime, timezone
import hashlib
import json
import math
from pathlib import Path
import re
import sqlite3
from urllib.parse import urlsplit

import pandas as pd

BET_TYPES = ("tansho", "fukusho", "wide", "umaren", "umatan", "sanrenpuku", "sanrentan")
LABELS = dict(zip(BET_TYPES, ("単勝", "複勝", "ワイド", "馬連", "馬単", "三連複", "三連単")))
ALIASES = {**{key: key for key in BET_TYPES}, **{label: key for key, label in LABELS.items()},
           "3連複": "sanrenpuku", "3連単": "sanrentan"}
ORDERED = {"umatan", "sanrentan"}
WIDTH = {"tansho": 1, "fukusho": 1, "wide": 2, "umaren": 2,
         "umatan": 2, "sanrenpuku": 3, "sanrentan": 3}


def sha256(path):
    digest = hashlib.sha256()
    with Path(path).open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


@contextmanager
def readonly(path):
    db = sqlite3.connect(Path(path).resolve().as_uri() + "?mode=ro", uri=True, timeout=10)
    try:
        db.execute("PRAGMA query_only=ON")
        db.execute("BEGIN")
        yield db
    finally:
        db.rollback()
        db.close()


def expected_combinations(bet_type, field_size):
    if bet_type not in WIDTH or isinstance(field_size, bool) or int(field_size) != field_size:
        raise ValueError("invalid bet type or runner count")
    n, k = int(field_size), WIDTH[bet_type]
    if not 2 <= n <= 18 or n < k:
        return 0
    return math.perm(n, k) if bet_type in ORDERED else math.comb(n, k)


def combination(value, bet_type, runners):
    text = str(value).strip()
    # Deliberately do not salvage arbitrary text, refunds, or malformed IDs.
    if not re.fullmatch(r"\d{1,2}(?:\s*(?:-|→|>)\s*\d{1,2})*", text):
        raise ValueError("unrecognized combination")
    result = tuple(int(x) for x in re.findall(r"\d+", text))
    if len(result) != WIDTH[bet_type] or len(set(result)) != len(result) or not set(result) <= set(runners):
        raise ValueError("combination does not match runners")
    return result if bet_type in ORDERED else tuple(sorted(result))


def timing_class(*, historical=False, final=False, received_at=None, post_time=None,
                 source_time=None, direct_observation=False):
    """A/B require actual pre-race evidence, not a later DB creation timestamp."""
    if historical or final:
        return "C"
    def aware(value):
        try:
            parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
            return parsed if parsed.tzinfo is not None else None
        except (TypeError, ValueError):
            return None
    received, post, source = map(aware, (received_at, post_time, source_time))
    if not direct_observation or received is None or post is None or received >= post:
        return "D"
    if source_time is None:
        return "B"
    return "A" if source is not None and source <= received < post else "D"


def _chunks(values, size=400):
    for start in range(0, len(values), size):
        yield values[start:start + size]


def _query_ids(db, select, ids):
    for chunk in _chunks(ids):
        yield from db.execute(select + " (" + ",".join("?" for _ in chunk) + ")", chunk)


def _cohort(frame):
    frame = frame.copy()
    frame["race_id"] = frame.race_id.astype(str)
    frame["race_date"] = frame.race_date.astype(str)
    if frame.empty or not frame.race_date.str.fullmatch(r"202[12]\d{4}").all():
        raise ValueError("inventory cohort must contain only 2021/2022")
    if frame.duplicated(["race_id", "horse_number"]).any():
        raise ValueError("duplicate runner key")
    for _, group in frame.groupby("race_id", sort=False):
        n = len(group)
        if group.race_date.nunique() != 1 or group.expected_runner_count.nunique() != 1 or int(group.expected_runner_count.iloc[0]) != n:
            raise ValueError("incomplete runner cohort")
        if not group.horse_number.map(lambda x: math.isfinite(x) and int(x) == x and 1 <= x <= 18).all():
            raise ValueError("invalid runner number")
    return frame


def inspect_database(snapshot, cohort):
    """Select exact cohort keys; global queries are schema/count/date metadata only."""
    frame = _cohort(cohort)
    ids = sorted(frame.race_id.unique())
    with readonly(snapshot) as db:
        tables = [r[0] for r in db.execute("SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'")]
        schemas = {t: [r[1] for r in db.execute('PRAGMA table_info("' + t.replace('"', '""') + '")')] for t in tables}
        legacy_counts = {t: db.execute('SELECT COUNT(*) FROM "' + t + '"').fetchone()[0]
                         for t in ("races", "entries", "results", "payouts", "sandbox_netkeiba_race_payouts", "sandbox_netkeiba_race_results") if t in tables}
        global_payouts = [dict(zip(("source_bet_type", "rows", "races", "race_id_year_min", "race_id_year_max"), r)) for r in db.execute(
            "SELECT bet_type,COUNT(*),COUNT(DISTINCT race_id),MIN(substr(race_id,1,4)),MAX(substr(race_id,1,4)) FROM return_tables_ultimate GROUP BY bet_type")]
        payouts = list(_query_ids(db, "SELECT race_id,bet_type,combinations,payout,created_at FROM return_tables_ultimate WHERE race_id IN", ids))
        # Only current-row horse_number/odds are projected. Do not deserialize
        # old unsafe prev/prev2 data, which can contain post-target history.
        odds = list(_query_ids(db, "SELECT race_id,json_extract(data,'$.horse_number'),json_extract(data,'$.odds'),created_at FROM race_results_ultimate WHERE race_id IN", ids))
        observations = list(_query_ids(db, "SELECT race_id,race_date,lifecycle,observed_at,source_status,market_payload_json IS NOT NULL FROM scrape_race_observations WHERE race_id IN", ids))
        global_obs = db.execute("SELECT COUNT(*),SUM(market_payload_json IS NOT NULL),MIN(observed_at),MAX(observed_at) FROM scrape_race_observations").fetchone()
    evidence = {"snapshot_path": str(Path(snapshot).resolve()), "snapshot_size": Path(snapshot).stat().st_size,
                "snapshot_mtime_ns": Path(snapshot).stat().st_mtime_ns, "schemas": schemas, "legacy_table_rows": legacy_counts,
                "payout_source_metadata_all_years": global_payouts,
                "source_observations_all_years_metadata_only": dict(zip(("rows", "nonempty_market_rows", "observed_at_min", "observed_at_max"), global_obs)),
                "cohort_observations": {"rows": len(observations), "nonempty_market_rows": sum(r[-1] for r in observations),
                                       "observed_at_min": min((r[3] for r in observations), default=None),
                                       "observed_at_max": max((r[3] for r in observations), default=None)},
                "queries": {"body_scope": "race_id IN fixed 2021/2022 cohort, chunk=400",
                            "odds_projection": "json_extract(data,'$.horse_number'), json_extract(data,'$.odds')",
                            "market_payload_bodies_read": False, "future_outcome_values_read": False},
                "payout_created_at_min": min((r[-1] for r in payouts), default=None),
                "payout_created_at_max": max((r[-1] for r in payouts), default=None)}
    return build_coverage(frame, payouts, odds), evidence


def build_coverage(cohort, payout_rows, odds_rows):
    frame = _cohort(cohort)
    payouts, odds = defaultdict(list), defaultdict(list)
    for race, label, text, amount, *_ in payout_rows:
        if label in ALIASES:
            payouts[(str(race), ALIASES[label])].append((text, amount))
    for race, horse, value, *_ in odds_rows:
        odds[str(race)].append((horse, value))
    records = []
    for race, group in frame.groupby("race_id", sort=True):
        runners = set(group.horse_number.astype(int))
        clean_odds = dict(zip(group.horse_number.astype(int), group.odds.astype(float)))
        valid_odds, invalid_odds, duplicates, mismatches = {}, 0, 0, 0
        for horse, value in odds[race]:
            try:
                h, v = int(horse), float(value)
                if h != float(horse) or h not in runners or not math.isfinite(v) or v < 1:
                    raise ValueError()
                if h in valid_odds:
                    duplicates += 1
                valid_odds[h] = v
                mismatches += int(v != clean_odds[h])
            except (ValueError, TypeError, OverflowError):
                invalid_odds += 1
        full_win = set(valid_odds) == runners and invalid_odds == duplicates == mismatches == 0
        for bet in BET_TYPES:
            valid, bad, duplicate, conflict = {}, 0, 0, 0
            for text, amount in payouts[(race, bet)]:
                try:
                    key = combination(text, bet, runners)
                    if isinstance(amount, bool) or not math.isfinite(float(amount)) or int(amount) != float(amount) or int(amount) <= 0:
                        raise ValueError("invalid payout")
                    if key in valid:
                        duplicate += 1
                        conflict += int(valid[key] != int(amount))
                    valid[key] = int(amount)
                except (ValueError, TypeError, OverflowError):
                    bad += 1
            n_odds = len(valid_odds) if bet == "tansho" else 0
            full = bool(full_win and bet == "tansho")
            records.append({"race_id": race, "race_date": group.race_date.iloc[0], "year": int(group.race_date.iloc[0][:4]),
                            "bet_type": bet, "field_size": len(runners), "expected_all_combination_odds": expected_combinations(bet, len(runners)),
                            "payout_rows": len(payouts[(race, bet)]), "valid_payout_combinations": len(valid),
                            "payout_present": bool(valid), "invalid_payout_rows": bad, "duplicate_payout_rows": duplicate,
                            "conflicting_payout_rows": conflict,
                            "payout_combination_membership_valid": bool(valid) and bad == duplicate == conflict == 0,
                            "payout_completeness_verified": False,
                            "all_combination_odds_present": full, "valid_odds_combinations": n_odds,
                            "odds_timing_class": "C" if n_odds else "NO_ODDS", "prerace_odds_verified": False,
                            "invalid_odds_rows": invalid_odds if bet == "tansho" else 0,
                            "duplicate_odds_rows": duplicates if bet == "tansho" else 0,
                            "odds_clean_source_mismatches": mismatches if bet == "tansho" else 0,
                            "historical_ev_data_eligible": full, "prospective_ev_data_eligible": False,
                            "nonwin_ev_roi_permitted": False})
    return pd.DataFrame.from_records(records)


def summarize_coverage(coverage):
    output = []
    for bet in BET_TYPES:
        selected = coverage[coverage.bet_type == bet]
        output.append({"bet_type": bet, "label": LABELS[bet], "cohort_races": len(selected),
                       "date_min": selected.race_date.min(), "date_max": selected.race_date.max(),
                       "payout_rows": int(selected.payout_rows.sum()), "payout_races": int(selected.payout_present.sum()),
                       "valid_payout_combinations": int(selected.valid_payout_combinations.sum()),
                       "invalid_payout_rows": int(selected.invalid_payout_rows.sum()),
                       "duplicate_payout_rows": int(selected.duplicate_payout_rows.sum()),
                       "conflicting_payout_rows": int(selected.conflicting_payout_rows.sum()),
                       "payout_completeness_verified": False,
                       "all_combination_odds_races": int(selected.all_combination_odds_present.sum()),
                       "all_combination_odds_count": int(selected.valid_odds_combinations.sum()),
                       "expected_all_combination_odds_count": int(selected.expected_all_combination_odds.sum()),
                       "odds_timing_class": "C" if selected.valid_odds_combinations.sum() else "NO_ODDS",
                       "A_races": 0, "B_races": 0, "D_observed_races": 0,
                       "no_odds_races": int((selected.valid_odds_combinations == 0).sum()),
                       "C_complete_races": int(selected.all_combination_odds_present.sum()),
                       "prerace_verified_races": 0,
                       "usable_historical_odds_races_2021": int(selected.loc[selected.year == 2021, "all_combination_odds_present"].sum()),
                       "usable_historical_odds_races_2022": int(selected.loc[selected.year == 2022, "all_combination_odds_present"].sum()),
                       "historical_ev_data_eligible": bool(selected.all_combination_odds_present.any()),
                       "prospective_ev_data_eligible": False, "nonwin_ev_roi_permitted": False,
                       "analysis_scope": "historical win odds only; no new profit claim" if bet == "tansho" else "prediction accuracy only; EV and ROI prohibited"})
    return pd.DataFrame(output)


def inspect_cache(cache_path, parsed_path, cohort_ids):
    """Metadata-only cache audit; unknown candidate endpoints block a zero claim."""
    result = {"http_cache_path": str(Path(cache_path).resolve()), "http_response_bodies_read": 0,
              "cohort_odds_url_candidates": [], "metadata_rows": 0}
    by_route, odds_years = Counter(), Counter()
    digest = hashlib.sha256()
    cohort_ids = set(cohort_ids)
    with readonly(cache_path) as db:
        for url, status, fetched, expires in db.execute("SELECT normalized_url,status,fetched_at,expires_at FROM http_cache INDEXED BY idx_http_cache_freshness"):
            result["metadata_rows"] += 1
            digest.update(json.dumps([url, status, fetched, expires], ensure_ascii=True, separators=(",", ":")).encode() + b"\n")
            parsed = urlsplit(url)
            route = re.sub(r"\d+", "{n}", parsed.path)
            by_route[(parsed.netloc, route)] += 1
            if "odds" in url.lower() or re.search(r"[?&]type=b\d", url):
                ids = re.findall(r"(?:race_id=|/race/)(\d{12})(?!\d)", url)
                year = ids[0][:4] if ids else "unknown"
                odds_years[(year, status)] += 1
                if not ids or set(ids) & cohort_ids:
                    result["cohort_odds_url_candidates"].append({"url": url, "http_status": status, "fetched_at": fetched,
                                                                  "body_not_read": True, "requires_scoped_followup": True})
        result["resume_rows"] = db.execute("SELECT COUNT(*) FROM fetch_resume").fetchone()[0]
    result.update({"metadata_sha256": digest.hexdigest(), "url_route_counts": [{"host": h, "path_pattern": p, "rows": n} for (h, p), n in sorted(by_route.items())],
                   "odds_endpoint_metadata_all_years": [{"year": y, "http_status": s, "rows": n} for (y, s), n in sorted(odds_years.items())]})
    with readonly(parsed_path) as db:
        result["parsed_cache_path"] = str(Path(parsed_path).resolve())
        result["parsed_kinds"] = [dict(zip(("kind", "version", "rows"), r)) for r in db.execute("SELECT kind,version,COUNT(*) FROM parsed_snapshots GROUP BY kind,version")]
        result["parsed_payload_bodies_read"] = 0
    return result
