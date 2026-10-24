"""Read-only full-snapshot acquisition coverage audit (no application imports).

Only writes acquisition_coverage.json under an existing completed snapshot audit
directory. HTTP/DB/cache mutation, scraping, and repair execution are forbidden.
Recorded inventories are not assumed to be exhaustive meeting schedules.
"""
from __future__ import annotations

import argparse
from collections import Counter, defaultdict
from contextlib import closing
from datetime import datetime, timedelta, timezone
import hashlib
import json
from pathlib import Path
import re
import sqlite3
from urllib.parse import parse_qs, urlsplit


ROOT = Path(__file__).resolve().parents[1]
OFFICIAL_ANNUAL = {
    "2013": 3454, "2014": 3451, "2015": 3454, "2016": 3454,
    "2017": 3455, "2018": 3454, "2019": 3452, "2020": 3456,
    "2021": 3456, "2022": 3456, "2023": 3456, "2024": 3454, "2025": 3455,
}
NONFINISH = ("中止", "取消", "除外", "失格", "競走中止", "出走取消", "競走除外", "中", "取", "除", "失")


def digest_file(path):
    h = hashlib.sha256()
    with path.open("rb") as f:
        for part in iter(lambda: f.read(8 * 1024 * 1024), b""):
            h.update(part)
    return h.hexdigest()


def ro(path, *, immutable=False):
    conn = sqlite3.connect(path.resolve().as_uri() + "?mode=ro" + ("&immutable=1" if immutable else ""), uri=True)
    conn.execute("PRAGMA query_only=ON")
    conn.row_factory = sqlite3.Row
    conn.execute("BEGIN")
    return conn


def day(value):
    value = str(value or "").strip().replace("-", "").replace("/", "")
    if not re.fullmatch(r"\d{8}", value):
        return None
    try:
        return value if datetime.strptime(value, "%Y%m%d").strftime("%Y%m%d") == value else None
    except ValueError:
        return None


def scope(race_id):
    if not isinstance(race_id, str) or not re.fullmatch(r"\d{12}", race_id):
        return "invalid_id"
    return "jra" if 1 <= int(race_id[4:6]) <= 10 else "non_jra"


def positive(value):
    try:
        return float(value) > 0
    except (TypeError, ValueError):
        return False


def settled(value):
    return positive(value) or any(t in str(value or "").strip() for t in NONFINISH)


def safe_json(value, default):
    try:
        return json.loads(value)
    except (TypeError, ValueError):
        return default


def grouped_counts(items, keys):
    c = Counter(tuple(item.get(key) for key in keys) for item in items)
    return [dict(zip(keys, key), count=n) for key, n in sorted(c.items(), key=lambda pair: str(pair[0]))]


def audit(audit_dir, *, read_cache=True):
    receipt_path = audit_dir / "snapshot_receipt.json"
    if not receipt_path.is_file():
        raise RuntimeError("snapshot_receipt_required_before_any_database_read")
    receipt = json.loads(receipt_path.read_text(encoding="utf-8"))
    db = audit_dir / "snapshot.sqlite3"
    if Path(receipt["snapshot_path"]).resolve() != db.resolve() or receipt["quick_check"] != "ok":
        raise RuntimeError("completed_snapshot_receipt_invalid")
    snapshot_hash = digest_file(db)
    if snapshot_hash != receipt["snapshot_sha256"] or db.stat().st_size != receipt["snapshot_bytes"]:
        raise RuntimeError("snapshot_receipt_hash_mismatch")
    now = datetime.now(timezone.utc)
    today = now.astimezone(timezone(timedelta(hours=9))).strftime("%Y%m%d")
    yesterday = (now.astimezone(timezone(timedelta(hours=9))) - timedelta(days=1)).strftime("%Y%m%d")
    races, stats, invalid_races, invalid_results = {}, {}, [], []
    orphan_results, identity_issues = [], []
    with closing(ro(db, immutable=True)) as conn:
        for row in conn.execute("SELECT race_id,data FROM races_ultimate"):
            rid = row["race_id"]
            data = safe_json(row["data"], None)
            if not isinstance(data, dict):
                invalid_races.append(rid)
                data = {}
            date = day(data.get("date"))
            races[rid] = {"race_id": rid, "race_date": date, "scope": scope(rid),
                          "declared_runners": data.get("num_horses"), "venue": data.get("venue"),
                          "post_time": data.get("post_time"), "raw_race_id": data.get("race_id")}
            if not date or data.get("race_id") != rid or (date and rid[:4] != date[:4]):
                identity_issues.append({"race_id": rid, "race_date": date, "raw_race_id": data.get("race_id"),
                                        "date_invalid": date is None, "race_id_year_date_mismatch": bool(date and rid[:4] != date[:4])})
        # SQLite extracts only the fields needed for this independent audit;
        # no raw result payload is copied into the report.
        fields = ("race_id", "horse_id", "horse_number", "finish_position", "finish_time")
        expr = ",".join("json_extract(data,'$." + key + "')" for key in fields)
        for row in conn.execute("SELECT id,race_id,json_valid(data) valid,CASE WHEN json_valid(data) THEN json_array(" + expr + ") END attrs FROM race_results_ultimate"):
            rid = row["race_id"]
            st = stats.setdefault(rid, {"rows": 0, "settled_rows": 0, "numeric_finish_rows": 0,
                "domain_nonfinish_rows": 0, "unsettled_rows": 0, "active_finish_time_missing": 0,
                "horse_ids": Counter(), "horse_numbers": Counter(), "missing_horse_id": 0,
                "missing_horse_number": 0, "invalid_json": 0, "raw_race_id_mismatch": 0})
            st["rows"] += 1
            if not row["valid"]:
                st["invalid_json"] += 1
                invalid_results.append(row["id"])
                values = [None] * 5
            else:
                values = json.loads(row["attrs"])
            raw_rid, hid, number, finish, finish_time = values
            st["raw_race_id_mismatch"] += int(raw_rid != rid)
            if raw_rid != rid and len(identity_issues) < 100000:
                identity_issues.append({"result_row_id": row["id"], "race_id": rid, "raw_race_id": raw_rid})
            for key, value, missing in (("horse_ids", hid, "missing_horse_id"), ("horse_numbers", number, "missing_horse_number")):
                text = str(value or "").strip()
                if text:
                    st[key][text] += 1
                else:
                    st[missing] += 1
            is_settled = settled(finish)
            st["settled_rows"] += int(is_settled)
            st["numeric_finish_rows"] += int(positive(finish))
            st["domain_nonfinish_rows"] += int(is_settled and not positive(finish))
            st["unsettled_rows"] += int(not is_settled)
            st["active_finish_time_missing"] += int(positive(finish) and not str(finish_time or "").strip())

        for rid, st in stats.items():
            if rid not in races:
                orphan_results.append({"race_id": rid, "rows": st["rows"]})
            st["duplicate_horse_id_extra_rows"] = sum(n - 1 for n in st["horse_ids"].values() if n > 1)
            st["duplicate_horse_number_extra_rows"] = sum(n - 1 for n in st["horse_numbers"].values() if n > 1)
            del st["horse_ids"], st["horse_numbers"]
            st["raw_result_settled"] = bool(st["rows"] and not st["unsettled_rows"] and not st["invalid_json"])

        ledgers = []
        for table, ledger_scope in (("scrape_date_completeness", "all"), ("scrape_scoped_date_completeness", "jra")):
            for row in conn.execute("SELECT * FROM " + table):
                item = dict(row)
                item["ledger_scope"] = ledger_scope
                item["year"] = str(item["race_date"])[:4]
                item["expected_ids"] = safe_json(item["expected_race_ids_json"], [])
                item["missing_ids"] = safe_json(item["missing_race_ids_json"], [])
                item["complete_ids"] = safe_json(item["complete_race_ids_json"], [])
                expected = set(item["expected_ids"])
                item["expected_count_mismatch"] = len(expected) != item["expected_race_count"]
                item["raw_absent_ids"] = sorted(expected - races.keys())
                item["raw_unsettled_ids"] = sorted(rid for rid in expected if rid in races and not stats.get(rid, {}).get("raw_result_settled"))
                item["raw_wrong_date_ids"] = sorted(rid for rid in expected if rid in races and races[rid]["race_date"] != item["race_date"])
                ledgers.append(item)

        states = [dict(r) for r in conn.execute("SELECT * FROM scrape_race_acquisition_state")]
        repairs = [dict(r) for r in conn.execute("SELECT * FROM scrape_repair_queue")]
        scraped_dates = [dict(r) for r in conn.execute("SELECT * FROM scraped_dates")]
        inventories = [dict(r) for r in conn.execute("SELECT * FROM scrape_finalized_race_inventories")]
        payout_races = {row[0] for row in conn.execute("SELECT DISTINCT race_id FROM return_tables_ultimate")}
        payout_orphans = [row[0] for row in conn.execute("SELECT DISTINCT p.race_id FROM return_tables_ultimate p LEFT JOIN races_ultimate r ON r.race_id=p.race_id WHERE r.race_id IS NULL")]
        # Complete, unique horse IDs within every race also rule out exact
        # duplicated JSON rows, without sorting gigabytes of raw payloads.
        if all(not st["duplicate_horse_id_extra_rows"] and not st["missing_horse_id"] and not st["invalid_json"] for st in stats.values()):
            exact_duplicate_results = 0
        else:
            exact_duplicate_results = conn.execute("SELECT COALESCE(SUM(n-1),0) FROM (SELECT COUNT(*) n FROM race_results_ultimate GROUP BY race_id,data HAVING COUNT(*)>1)").fetchone()[0]

    date_races, meeting_numbers = defaultdict(set), defaultdict(set)
    for rid, info in races.items():
        if info["race_date"]:
            date_races[(info["scope"], info["race_date"])].add(rid)
            if info["scope"] == "jra":
                meeting_numbers[(info["race_date"], rid[:10])].add(int(rid[-2:]))
    states_by_id = {r["race_id"]: r for r in states}
    # A lifecycle state is not independently trusted as complete raw data.
    raw_issues, declarations, raw_lifecycle_rows = [], [], []
    for rid, info in races.items():
        st = stats.get(rid, {})
        n = st.get("rows", 0)
        declared = info["declared_runners"]
        state = states_by_id.get(rid, {})
        issues = [key for key in ("missing_horse_id", "missing_horse_number", "invalid_json",
                  "raw_race_id_mismatch", "duplicate_horse_id_extra_rows", "duplicate_horse_number_extra_rows",
                  "active_finish_time_missing") if st.get(key)]
        if not n:
            issues.append("no_runner_rows")
        if not st.get("raw_result_settled"):
            issues.append("raw_results_not_settled")
        if issues:
            temporal_status = ("historical_results_incomplete" if info["race_date"] and info["race_date"] < today
                               else "today_publication_state_not_historic_error" if info["race_date"] == today
                               else "future_or_date_unknown")
            raw_issues.append(dict(info, issues=issues, result_statistics=st, ledger_lifecycle=state.get("lifecycle"),
                                   quality_status=state.get("quality_status"), excluded=state.get("excluded_from_standard"),
                                   temporal_status=temporal_status))
        if isinstance(declared, (int, float)) and declared > 0 and n != declared:
            declarations.append(dict(info, saved_runner_rows=n,
                numeric_finish_rows=st.get("numeric_finish_rows", 0), domain_nonfinish_rows=st.get("domain_nonfinish_rows", 0),
                declaration_equals_after_domain_exceptions=declared == st.get("numeric_finish_rows"),
                quality_status=state.get("quality_status"),
                resolution="UNDETERMINED: actual runner loss versus incorrect declared metadata requires source evidence.",
                caveat="Mismatch is a review signal; declared starters may exclude cancellations. Never invent or drop runners."))
        raw_lifecycle_rows.append({"scope": info["scope"], "year": (info["race_date"] or "UNKNOWN")[:4],
                                  "raw_state": "settled" if st.get("raw_result_settled") else "partial_result" if st.get("settled_rows") else "entry_or_unpublished",
                                  "lifecycle_ledger": state.get("lifecycle", "not_audited")})

    ledger_details, ledger_summary = [], []
    for item in ledgers:
        saved_date_ids = set().union(*(date_races.get((s, item["race_date"]), set()) for s in ("jra", "non_jra", "invalid_id"))) if item["ledger_scope"] == "all" else date_races.get(("jra", item["race_date"]), set())
        item["saved_ids_not_in_expected"] = sorted(saved_date_ids - set(item["expected_ids"]))
        item["no_race_contradicted_by_saved_races"] = bool(item["expected_race_count"] == 0 and item["status"] == "complete" and saved_date_ids)
        item["no_race_expired_6h"] = bool(item["expected_race_count"] == 0 and item["status"] == "complete" and str(item["updated_at"]) < (now-timedelta(hours=6)).isoformat().replace("+00:00", "Z"))
        if item["raw_absent_ids"] or item["raw_unsettled_ids"] or item["raw_wrong_date_ids"] or item["no_race_contradicted_by_saved_races"] or item["saved_ids_not_in_expected"] or item["expected_count_mismatch"] or item["status"] != "complete":
            ledger_details.append({key: item[key] for key in ("ledger_scope", "race_date", "status", "expected_race_count", "missing_ids", "raw_absent_ids", "raw_unsettled_ids", "raw_wrong_date_ids", "saved_ids_not_in_expected", "no_race_contradicted_by_saved_races", "expected_count_mismatch")})
    for year in sorted({str(info["race_date"] or "UNKNOWN")[:4] for info in races.values()} | {i["year"] for i in ledgers}):
        for sc in ("all", "jra"):
            ls = [i for i in ledgers if i["year"] == year and i["ledger_scope"] == sc]
            actual = {d for (s,d), ids in date_races.items() if d[:4] == year and (sc == "all" or s == "jra")}
            positive_ledger_dates = {i["race_date"] for i in ls if i["expected_race_count"] > 0}
            ledger_summary.append({"year": year, "scope": sc, "saved_racing_dates": len(actual),
                "all_ledger_dates": len(ls), "positive_expected_ledger_dates": len(positive_ledger_dates),
                "saved_dates_with_positive_inventory": len(actual & positive_ledger_dates),
                "saved_dates_without_positive_inventory": len(actual - positive_ledger_dates),
                "saved_date_positive_inventory_pct": round(100*len(actual & positive_ledger_dates)/len(actual), 3) if actual else None,
                "status_counts": dict(Counter(i["status"] for i in ls)),
                "no_race_ledger_dates": sum(i["expected_race_count"] == 0 and i["status"] == "complete" for i in ls),
                "stale_no_race_dates": sum(i["no_race_expired_6h"] for i in ls),
                "no_race_contradictions": sum(i["no_race_contradicted_by_saved_races"] for i in ls),
                "expected_races": sum(i["expected_race_count"] for i in ls),
                "ledger_missing_ids": sum(len(i["missing_ids"]) for i in ls),
                "raw_absent_expected_ids": sum(len(i["raw_absent_ids"]) for i in ls),
                "raw_unsettled_expected_ids": sum(len(i["raw_unsettled_ids"]) for i in ls),
                "raw_wrong_date_expected_ids": sum(len(i["raw_wrong_date_ids"]) for i in ls)})

    inventory_details = []
    for item in inventories:
        payload = safe_json(item["payload_json"], {})
        ids = set(payload.get("race_ids", []))
        digest = hashlib.sha256(json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
        inventory_details.append({"race_date": item["race_date"], "scope": item["scope"],
            "source": payload.get("source"), "expected_races": len(ids), "payload_digest_matches": digest == item["payload_sha256"],
            "expired": item["expires_at"] <= now.timestamp(), "raw_absent_ids": sorted(ids - races.keys()),
            "raw_unsettled_ids": sorted(rid for rid in ids if rid in races and not stats.get(rid, {}).get("raw_result_settled")),
            "validation_caveat": "Original source evidence retained; not re-admitted by mutable cache/parser/database digest gates in this audit."})

    cache_info = {"read": False, "network": False, "calendar_is_jra_only": True}
    calendar_months, calendar_days, missing_day_source_probes = [], set(), []
    if read_cache:
        cache_path = ROOT / "keiba/data/fetch_cache.db"
        if cache_path.is_file():
            with closing(ro(cache_path)) as cc:
                for item in cc.execute("SELECT normalized_url,status,fetched_at,expires_at,body FROM http_cache WHERE normalized_url LIKE 'https://race.netkeiba.com/top/calendar.html?%'"):
                    url = item["normalized_url"]
                    query = parse_qs(urlsplit(url).query)
                    year, month = query.get("year", [""])[0], query.get("month", [""])[0]
                    key = year + month.zfill(2)
                    body = bytes(item["body"] or b"")
                    found = sorted({d.decode("ascii") for d in re.findall(rb"kaisai_date=(\d{8})", body) if d.decode("ascii").startswith(key) and day(d.decode("ascii"))})
                    if item["status"] == 200:
                        calendar_days.update(found)
                    calendar_months.append({"month": key, "status": item["status"], "fetched_at": item["fetched_at"],
                        "expired": item["expires_at"] <= now.timestamp(), "body_sha256": hashlib.sha256(body).hexdigest(),
                        "declared_jra_dates": found, "saved_jra_dates_absent": [d for d in found if not date_races.get(("jra", d))],
                        "warning": "Date presence is not an exhaustive race-ID inventory or per-venue meeting proof."})
                # Only inspect already-saved cache metadata. A calendar day
                # alone does not justify assuming two venues or 24 races.
                for target_day in sorted(d for d in calendar_days if d < today and not date_races.get(("jra", d))):
                    source_urls = [dict(row) for row in cc.execute(
                        "SELECT normalized_url,status,fetched_at,expires_at FROM http_cache INDEXED BY idx_http_cache_freshness WHERE normalized_url LIKE ?",
                        ("%" + target_day + "%",))]
                    missing_day_source_probes.append({"race_date":target_day,"cache_metadata_matches":source_urls})
            parsed_path = ROOT / "keiba/data/parsed_fetch_cache.db"
            if parsed_path.is_file():
                with closing(ro(parsed_path)) as pc:
                    for probe in missing_day_source_probes:
                        rows = pc.execute("SELECT payload FROM parsed_snapshots WHERE kind='month-race-index' AND cache_key=?",(probe["race_date"][:6],)).fetchall()
                        ids = sorted({rid for row in rows for rid in safe_json(row[0],{}).get(probe["race_date"],[])})
                        probe["saved_parsed_month_index_race_ids"] = ids
                        probe["missing_race_count"] = len(ids) if ids else None
                        probe["missing_race_count_verified"] = False
                        probe["verification"] = "Only metadata/cache evidence inspected; no network or inferred 24/36 race total."
            cache_info.update(read=True, source=str(cache_path), point_in_time="separate_read_only_transaction_not_main_snapshot", calendar_months=calendar_months,
                              missing_past_day_source_probes=missing_day_source_probes)
    annual = []
    for year, official in OFFICIAL_ANNUAL.items():
        saved = {rid for rid, info in races.items() if info["scope"] == "jra" and rid[:4] == year}
        actual_date_count = sum(bool(info["scope"] == "jra" and info["race_date"] and info["race_date"][:4] == year) for info in races.values())
        annual.append({"year": year, "official_jra_races": official, "saved_jra_race_ids_by_id_year": len(saved),
            "saved_jra_by_actual_json_date_year": actual_date_count, "net_count_shortfall": official-len(saved),
            "count_ratio_pct_not_exact_coverage": round(100*len(saved)/official,4),
            "strict_coverage": "UNVERIFIED: aggregate count cannot prove ID/date correctness, extras, cancelled meetings, or unseen race IDs."})

    for item in repairs:
        provenance = safe_json(item["provenance_json"], {})
        rid = item["entity_id"] if item["entity_type"] == "race" else None
        date = races.get(rid, {}).get("race_date") if rid else day(item["entity_id"]) if item["entity_type"] == "date" else day(provenance.get("race_date"))
        item.update(race_date=date, year=(date or "UNKNOWN")[:4], scope=scope(rid) if rid else provenance.get("race_scope", "unscoped"))
    state_details = []
    for item in states:
        info = races.get(item["race_id"], {})
        item["year"] = (day(item["race_date"]) or "UNKNOWN")[:4]
        item["scope"] = scope(item["race_id"])
        if item["quality_status"] == "complete" and not stats.get(item["race_id"], {}).get("raw_result_settled"):
            state_details.append({"race_id": item["race_id"], "race_date": item["race_date"], "scope": item["scope"],
                "quality_status": item["quality_status"], "raw_result_statistics": stats.get(item["race_id"]),
                "raw_race_absent": item["race_id"] not in races})
    no_race_contradictions = []
    for item in scraped_dates:
        observed = set().union(*(date_races.get((s, item["date"]), set()) for s in ("jra", "non_jra", "invalid_id")))
        if item["no_race"] and observed:
            no_race_contradictions.append(dict(item, saved_races=len(observed), saved_ids=sorted(observed)))

    jra_expected = {rid for i in ledgers if i["ledger_scope"] == "jra" and "20160101" <= i["race_date"] <= "20260930" for rid in i["expected_ids"]}
    jra_unstored_expected = sorted(jra_expected - races.keys())
    historical_jra_unstored = [rid for rid in jra_unstored_expected if "2016" <= rid[:4] <= "2025"]
    jra_expected_year_rows = []
    for year in sorted({rid[:4] for rid in jra_expected}):
        expected_ids = {rid for rid in jra_expected if rid[:4] == year}
        unstored = sorted(expected_ids-races.keys())
        jra_expected_year_rows.append({"year":year,"ledger_expected_race_ids":len(expected_ids),"raw_saved_expected_race_ids":len(expected_ids & races.keys()),
            "raw_absent_expected_race_ids":unstored,"raw_absent_expected_count":len(unstored),
            "official_annual_count":OFFICIAL_ANNUAL.get(year),
            "annual_expected_count_matches_official":len(expected_ids)==OFFICIAL_ANNUAL[year] if year in OFFICIAL_ANNUAL else None,
            "caveat":"Explicit unsaved IDs relative to recorded source list. Aggregate official count agreement is not independent proof of each ID."})

    def period_summary(start, end, sc):
        selected = [info for info in races.values() if info["race_date"] and start <= info["race_date"] <= end and (sc == "all" or info["scope"] == sc)]
        ids = {r["race_id"] for r in selected}
        return {"from": start, "to": end, "scope": sc, "saved_races": len(ids),
            "runner_rows": sum(stats.get(rid, {}).get("rows", 0) for rid in ids),
            "saved_dates": len({r["race_date"] for r in selected}),
            "raw_settled_races": sum(bool(stats.get(rid, {}).get("raw_result_settled")) for rid in ids),
            "raw_unsettled_or_no_runner_races": sorted(rid for rid in ids if not stats.get(rid, {}).get("raw_result_settled")),
            "raw_settled_without_payout_rows": sum(bool(stats.get(rid, {}).get("raw_result_settled")) and rid not in payout_races for rid in ids),
            "dates": [{"date": d, "saved_races": sum(i["race_date"] == d for i in selected),
                       "raw_settled_races": sum(i["race_date"] == d and bool(stats.get(i["race_id"], {}).get("raw_result_settled")) for i in selected)}
                      for d in sorted({i["race_date"] for i in selected})] if start >= "20260914" else None}

    out = {
        "schema": "current-acquisition-coverage-audit-v1", "observed_at_utc": now.isoformat(),
        "snapshot": {"path": str(db), "sha256": snapshot_hash, "receipt_sha256": digest_file(receipt_path), "hash_verified": True},
        "execution": {"read_only_database": True, "immutable_snapshot": True, "application_imports": False,
                      "network": False, "repair_execution": False, "database_mutations": False},
        "denominator_caveats": ["Full scan of stored races/results, not proof of every actual meeting being acquired.",
            "Zero missing IDs in a ledger applies only to that ledger's declared IDs/dates; unseen meetings/races remain unverified.",
            "Race dates use JSON actual date; race-ID prefix is only compared separately for year mismatch.",
            "JRA calendar cannot prove all-venue coverage. Existing meeting 1..12 gaps are review candidates, not proof of a cancelled/nonexistent race.",
            "Today's not-yet-published results are not past-result errors; historic entry-only rows require separate follow-up.",
            "Complete acquisition quality means stored settled mandatory fields, not every pedigree/odds/other optional field present."],
        "saved_totals": {"races": len(races), "runner_rows": sum(s["rows"] for s in stats.values()),
            "race_scopes": dict(Counter(i["scope"] for i in races.values())), "invalid_race_json": invalid_races,
            "invalid_result_json_count": len(invalid_results), "invalid_result_json_row_ids": invalid_results,
            "raw_result_lifecycle": grouped_counts(raw_lifecycle_rows, ["year", "scope", "raw_state"]),
            "orphan_result_races": orphan_results, "orphan_payout_race_ids": payout_orphans,
            "exact_duplicate_result_extra_rows": exact_duplicate_results,
            "duplicate_horse_id_extra_rows": sum(st["duplicate_horse_id_extra_rows"] for st in stats.values()),
            "duplicate_horse_number_extra_rows": sum(st["duplicate_horse_number_extra_rows"] for st in stats.values())},
        "official_annual_count_benchmark": {"source_url": "https://www.jra.go.jp/company/about/outline/growth/kyoso/",
            "verification": "Primary page verified by coordinating parent agent on 2026-10-17; no network by this audit.",
            "rows": annual, "no_2026_annual_comparison": True},
        "actual_date_year_scope_summary": [{"year": year, "scope": sc,
            "races": sum(i["scope"] == sc and (i["race_date"] or "UNKNOWN")[:4] == year for i in races.values()),
            "runner_rows": sum(stats.get(rid,{}).get("rows",0) for rid,i in races.items() if i["scope"] == sc and (i["race_date"] or "UNKNOWN")[:4] == year)}
            for year in sorted({(i["race_date"] or "UNKNOWN")[:4] for i in races.values()}) for sc in ("jra","non_jra","invalid_id")],
        "periods": [period_summary(start, end, sc) for start,end in (("20160101","20260930"),("20260914",yesterday),(today,today)) for sc in ("jra","non_jra","all")],
        "date_inventory_denominators": ledger_summary,
        "date_inventory_discrepancies": ledger_details,
        "inventories": {"counts": grouped_counts(inventory_details,["scope","expired","payload_digest_matches"]), "rows": inventory_details},
        "cached_calendar_evidence": cache_info,
        "declared_runner_count_mismatches": declarations,
        "raw_race_result_issues": raw_issues,
        "race_result_identity_issues": identity_issues,
        "jra_existing_meeting_sequence_review_candidates": [{"race_date": d, "meeting_prefix": prefix,
            "saved_numbers": sorted(numbers), "unobserved_1_to_12": sorted(set(range(1,13))-numbers),
            "proof_status": "unverified_schedule_not_automatically_missing"}
            for (d,prefix),numbers in sorted(meeting_numbers.items()) if set(range(1,13))-numbers],
        "legacy_no_race_saved_data_contradictions": no_race_contradictions,
        "quality_states": {"counts": grouped_counts(states,["year","scope","lifecycle","quality_status","excluded_from_standard"]),
            "complete_but_raw_unsettled_or_missing": state_details,
            "races_without_any_state": sum(rid not in states_by_id for rid in races),
            "jra_races_without_any_state": sum(i["scope"] == "jra" and rid not in states_by_id for rid,i in races.items()),
            "excluded": [{k:r[k] for k in ("race_id","race_date","quality_status","last_error","exclusion_reason")} for r in states if r["excluded_from_standard"]]},
        "repair_queue": {"counts": grouped_counts(repairs,["year","scope","entity_type","repair_kind","status"]),
            "pending_race_and_date_rows": [{k:r.get(k) for k in ("entity_type","entity_id","race_date","scope","repair_kind","status","attempt_count","max_attempts","last_error")} for r in repairs if r["status"] != "completed" and r["entity_type"] in {"race","date"}],
            "terminal_error_counts": dict(Counter(r["last_error"] for r in repairs if r["status"] == "quarantined"))},
        "existing_safe_recovery_paths": [
            {"path": "python-api/scraping/jobs.py:_run_scrape_job_async", "behavior": "race_scope=jra; force_rescrape is repair mode. Resolve scoped list/calendar; hydrate stored quality; fetch only missing/incomplete details. No blanket overwrite of complete history.", "guard": "Run only with user start; preserve excluded/quarantined source-terminal IDs and failed-date status."},
            {"path": "python-api/scraping/acquisition_scope.py:run_scoped_date_repair_audit", "behavior": "Independent JRA expected-ID/saved/complete/missing ledger. Does not promote JRA completeness into all-venue ledger.", "guard": "A complete ledger is scoped to discovered expected IDs, not proof of unobserved meetings."},
            {"path": "python-api/scraping/jobs.py:_build_race_dates_from_calendar", "behavior": "Calendar failures fall back to date lookups; positive calendar/saved dates invalidate no_race; recent/future empty responses defer; no-race TTL defaults six hours.", "guard": "Calendar is JRA-only. Independently validate local schedule for any all-venue claim."},
            {"path": "python-api/scraping/result_reuse.py", "behavior": "Reparse valid historical HTML before spending a repair HTTP request.", "guard": "Do not treat cached entry-only/empty source as a finalized result or invent zero payout."},
            {"path": "python-api/scraping/quality.py:queue_repair", "behavior": "Distinct pending/completed/quarantined repair kinds and max-attempt budget; explicit terminal reasons remain quarantined.", "guard": "Do not reset terminal-source quarantines or automatically equate processed dates with quality_complete."},
        ],
        "source_evidence": [{"path": str(ROOT/path), "sha256": digest_file(ROOT/path)} for path in (
            "python-api/scraping/quality.py", "python-api/scraping/acquisition_scope.py", "python-api/scraping/jobs.py",
            "python-api/scraping/race_inventory.py", "python-api/scraping/race_list.py", "python-api/scraping/result_reuse.py")],
        "audit_script_sha256": digest_file(Path(__file__)),
    }
    out["jra_expected_id_audit_201601_202609"] = {"scope":"jra", "date_basis":"recorded scoped ledger race_date, not ID-derived calendar date",
        "rows":jra_expected_year_rows,"expected_race_ids":len(jra_expected),"raw_absent_expected_race_ids":jra_unstored_expected,
        "raw_absent_expected_count":len(jra_unstored_expected),"raw_absent_expected_2016_2025_count":len(historical_jra_unstored),
        "independently_source_verified_each_missing_id":False}
    out["summary"] = {"saved_races":len(races),"runner_rows":sum(s["rows"] for s in stats.values()),
        "jra_2016_2025_official_count":sum(OFFICIAL_ANNUAL[str(y)] for y in range(2016,2026)),
        "jra_2016_2025_saved_id_count":sum(i["scope"]=="jra" and "2016"<=rid[:4]<= "2025" for rid,i in races.items()),
        "jra_2016_2025_missing_ledger_expected_ids":len(historical_jra_unstored),
        "jra_2016_202609_missing_ledger_expected_ids":len(jra_unstored_expected),
        "jra_2016_202609_raw_results_incomplete":sum(i["scope"]=="jra" and i["race_date"] is not None and "20160101"<=i["race_date"]<="20260930" and not stats.get(rid,{}).get("raw_result_settled") for rid,i in races.items()),
        "jra_historical_entry_or_partial_after_20260913":sum(i["scope"]=="jra" and i["race_date"] is not None and "20260914"<=i["race_date"]<=yesterday and not stats.get(rid,{}).get("raw_result_settled") for rid,i in races.items()),
        "jra_declared_runner_mismatch_review_races":sum(i["scope"]=="jra" for i in declarations),
        "non_jra_declared_runner_mismatch_review_races":sum(i["scope"]=="non_jra" for i in declarations),
        "jra_declared_runner_mismatch_with_quality_complete":sum(i["scope"]=="jra" and i["quality_status"]=="complete" for i in declarations),
        "race_date_ledger_no_race_saved_data_contradictions":sum(i["no_race_contradicted_by_saved_races"] for i in ledgers),
        "legacy_scraped_dates_no_race_saved_data_contradictions":len(no_race_contradictions),
        "past_calendar_dates_without_any_saved_jra_race":sorted(d for d in calendar_days if d<today and not date_races.get(("jra",d))),
        "pending_repairs_by_kind":dict(Counter(i["repair_kind"] for i in repairs if i["status"]=="pending")),
        "source_terminal_or_attempt_quarantines":sum(i["status"]=="quarantined" for i in repairs),
        "exact_duplicate_runner_rows":exact_duplicate_results,"orphan_result_races":len(orphan_results),
        "strict_all_race_coverage_status":"UNVERIFIED_AND_KNOWN_GAPS: not 100 percent"}
    out["existing_safe_recovery_paths"].append({"path":"python-api/routers/scrape.py:rescrape_incomplete / repair_race",
        "behavior":"Legacy repair endpoints inspect a first runner or merely non-empty horses and return success=True without an independent final date-quality audit.",
        "guard":"Do not use these responses as proof of repaired completeness. Prefer normal scoped server-batch quality-audited flow; no endpoint executed by this audit."})
    out["source_evidence"].append({"path":str(ROOT/"python-api/routers/scrape.py"),"sha256":digest_file(ROOT/"python-api/routers/scrape.py")})
    output = audit_dir / "acquisition_coverage.json"
    output.write_text(json.dumps(out,ensure_ascii=False,indent=2,allow_nan=False)+"\n",encoding="utf-8")
    return output,out


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--audit-dir", type=Path, required=True)
    parser.add_argument("--no-cache", action="store_true")
    args = parser.parse_args()
    output, result = audit(args.audit_dir.resolve(), read_cache=not args.no_cache)
    print(json.dumps({"output":str(output),"races":result["saved_totals"]["races"],
        "runner_rows":result["saved_totals"]["runner_rows"],"ledger_discrepancy_dates":len(result["date_inventory_discrepancies"]),
        "declared_runner_mismatches":len(result["declared_runner_count_mismatches"]),
        "raw_result_issue_races":len(result["raw_race_result_issues"]),"network":False},ensure_ascii=False))
