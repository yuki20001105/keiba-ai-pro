"""Fixed, bounded, label-free historical workout probes; never bulk fetch or fit."""
from __future__ import annotations
import argparse
import asyncio
import csv
from datetime import datetime, timezone
import hashlib
import json
import re
import sqlite3
import time
from email.utils import parsedate_to_datetime

from training_reconstruction_common import ROOT, OUT, SNAPSHOT, TRACK, sha, read, write, now, protection
from bs4 import BeautifulSoup
import aiohttp
import psutil
from market_edge.compute import CollectorMonitor
from scraping.request_pacing import RequestPacer, AccessPaused

YEARS = (2019, 2020, 2021, 2022, 2023, 2024)
POLICY = {"max_requests": 18, "per_year": 3, "minimum_start_interval_seconds": 3,
          "timeout_seconds": 20, "max_body_bytes": 2097152, "retries": 0,
          "redirects": False, "authentication": "anonymous; no credential discovery or login; no access-control bypass",
          "stop_on": ["403", "429", "challenge", "collector unhealthy", "collector capture window", "RAM reserve <4 GiB"],
          "old_raw_first": True, "bulk_fetch": False, "models_fitted": 0,
          "coordination": "existing cross-process RequestPacer lease; no cache/primary DB writes; no block resets"}


def select_probes(connection):
    connection.row_factory = sqlite3.Row
    existing = {str(r[0]) for r in connection.execute("SELECT DISTINCT race_id FROM training_data")}
    rows = connection.execute("""SELECT race_id,race_date,num_horses FROM races_ultimate
        WHERE length(race_id)=12 AND substr(race_id,5,2) BETWEEN '01' AND '10'
        AND length(race_date)=8 AND substr(race_id,1,4)=substr(race_date,1,4)
        ORDER BY race_date,race_id""").fetchall()
    selected, counts = [], {}
    for year in YEARS:
        candidates = [dict(r) for r in rows if str(r["race_date"]).startswith(str(year)) and r["race_id"] not in existing]
        counts[str(year)] = len(candidates)
        if len(candidates) < 3:
            raise ValueError("insufficient eligible metadata candidates")
        for quantile in (.2, .5, .8):
            row = candidates[int((len(candidates) - 1) * quantile)]
            datetime.strptime(row["race_date"], "%Y%m%d")
            selected.append({**row, "year": year, "selection_quantile": quantile,
                             "url": "https://race.netkeiba.com/race/oikiri.html?race_id=" + row["race_id"]})
    if len({r["race_id"] for r in selected}) != 18:
        raise ValueError("duplicate sample race")
    return selected, counts


def inspect_body(body, race_id):
    # Preserve raw bytes. Decode by declared charset, else publisher legacy EUC-JP.
    header = body[:5000].decode("ascii", errors="ignore")
    charset = re.search(r'charset\s*=\s*["\']?([A-Za-z0-9_-]+)', header, re.I)
    encoding = charset.group(1) if charset else "euc-jp"
    try:
        text = body.decode(encoding, errors="replace")
    except LookupError:
        encoding, text = "euc-jp", body.decode("euc-jp", errors="replace")
    soup = BeautifulSoup(text, "lxml")
    table = soup.select_one("table.OikiriTable")
    clock_lists = table.select("ul.TrainingTimeDataList") if table else []
    dates = [x.get_text(" ", strip=True) for x in table.select(".Training_Day")] if table else []
    clocks = [[x.get_text(" ", strip=True) for x in ul.select("li")] for ul in clock_lists]
    lower = text.lower()
    challenge = any(s in lower for s in ("cf-chl-", "just a moment", "attention required", "captcha", "verify you are human", "access denied",
        "アクセス制限", "アクセスが制限されています", "アクセス制限がかかっています", "ただいまアクセスが集中しております"))
    memberships = any(s in text for s in ("プレミアム", "ログイン", "会員登録"))
    numeric_clocks = sum(bool(re.search(r"\d+\.\d+", clock)) for row in clocks for clock in row)
    # Generic login link on a page is not proof of a paywall.
    status = "DATA_PRESENT" if dates and numeric_clocks else "NO_OBSERVABLE_WORKOUT_VALUES"
    if challenge:
        status = "ACCESS_CHALLENGE"
    workout_rows = []
    if table:
        for tr in table.select("tr.HorseList"):
            day = tr.select_one(".Training_Day")
            if not day:
                continue
            tds = tr.find_all("td", recursive=False)
            idx = next((i for i, td in enumerate(tds) if td is day), -1)
            workout_rows.append({"training_date_raw": day.get_text(" ", strip=True),
                "course_raw": tds[idx+1].get_text(" ", strip=True) if idx >= 0 and len(tds)>idx+1 else None,
                "clock_slots_raw": [li.get_text(" ", strip=True) for li in tr.select("ul.TrainingTimeDataList li")],
                "grade_raw": [g.get_text(" ", strip=True) for g in tr.select(".Rank_A,.Rank_B,.Rank_C,.Rank_D")],
                "pace_raw": [g.get_text(" ", strip=True) for g in tr.select(".TrainingLoad")]})
    return {"race_id": race_id, "encoding": encoding, "replacement_characters": text.count("\ufffd"),
            "title": soup.title.get_text(" ", strip=True) if soup.title else None,
            "table_present": table is not None, "horse_header_count": len(table.select('tr[id^="tr_"]')) if table else 0,
            "training_date_count": len(dates), "clock_list_count": len(clocks), "numeric_clock_count": numeric_clocks,
            "membership_link_or_text_present": memberships, "challenge": challenge, "content_status": status,
            "html_headers": [h.get_text(" ", strip=True) for h in table.select("th")] if table else [],
            "workout_rows": workout_rows,
            "comment_cells": len(table.select(".TrainingReview_Cell")) if table else 0,
            "distance_mapping_verified": False, "pit_class": "INVALID_OR_UNKNOWN", "historical_backfill": True}


def retry_after(headers):
    value = headers.get("Retry-After") or headers.get("retry-after")
    if not value:
        return 0.0
    try:
        return max(0.0, float(value)) if str(value).strip().isdigit() else max(0.0,parsedate_to_datetime(value).timestamp()-time.time())
    except (ValueError,TypeError,OverflowError):
        return 0.0


def initialize():
    before = protection()
    with sqlite3.connect(SNAPSHOT.resolve().as_uri()+"?mode=ro", uri=True) as conn:
        conn.execute("PRAGMA query_only=ON")
        selected, counts = select_probes(conn)
    plan = {"schema": "training-reconstruction-audit-plan-v1", "frozen_at": now(),
            "input_snapshot_sha256": sha(SNAPSHOT), "protected_before": before,
            "policy": POLICY, "sample_selection": "3 fixed chronological quantiles .2/.5/.8 per JRA race year; no existing training rows; identity/date metadata only; no outcomes read",
            "probe_races": selected, "uncollected_JRA_races_by_year": counts,
            "no_inference_from_numeric_magnitude": True, "unknown_measurement_distance": None,
            "strict_research_class": "STRICT_PIT only; historical backfill requires a new independent preregistration",
            "feature_candidates": "20 requested candidates frozen; existing constant is_last_training rejected; other19 HOLD until source/distance/PIT/coverage gates",
            "comment_input": "prohibited; separate NLP only after provenance verified",
            "no_model_fits": True, "no_bulk_acquisition": True, "no_commit_push": True,
            "official_sources_reviewed": ["https://support.keiba.netkeiba.com/hc/ja/articles/18932664402713", "https://support.keiba.netkeiba.com/hc/ja/articles/18932701884697"],
            "publisher_docs_limit": "current general documentation proves no historical row's publication version; global2006–2021 reference colors will never be used as PIT features"}
    write(OUT/"experiment_plan.json", plan)
    print(json.dumps({"plan_frozen": True, "sha256": sha(OUT/"experiment_plan.json"), "sample": selected}, ensure_ascii=False))


def admission(monitor):
    state = monitor.snapshot()
    if not state["healthy"]:
        raise ValueError("collector unhealthy: "+str(state["reasons"]))
    instant = time.time()
    if any(w["start"]-120 <= instant <= w["end"]+60 for w in state["capture_windows"]):
        raise ValueError("collector capture window")
    if psutil.virtual_memory().available < 4*1024**3:
        raise ValueError("RAM reserve below4GiB")
    return {k:state[k] for k in ("healthy", "observed_at", "pid", "protocol_sha256")}


async def probe():
    plan = read(OUT/"experiment_plan.json")
    if plan["policy"] != POLICY or sha(SNAPSHOT) != plan["input_snapshot_sha256"]:
        raise ValueError("frozen plan/source changed")
    with sqlite3.connect(SNAPSHOT.resolve().as_uri()+"?mode=ro", uri=True) as conn:
        conn.execute("PRAGMA query_only=ON")
        selected, counts = select_probes(conn)
    validate_samples(plan, selected, counts)
    if not (OUT/"training_source_inventory.json").is_file():
        raise ValueError("complete and persist local source search before network")
    from verify_market_segments_outputs import verify_files
    local_manifest=read(OUT/"provenance/training-parser-source-manifest.json")
    verify_files(OUT,local_manifest["files"])
    rawroot = OUT/"probe_raw"
    rawroot.mkdir(exist_ok=False)
    rows, inspect_results = [], []
    monitor = CollectorMonitor(TRACK)
    pacer = RequestPacer(ROOT/"keiba/data/request_pacing.db")
    stop = None
    # No cookie/credential loading, no session impersonation, no user-agent rotation.
    async with aiohttp.ClientSession(cookie_jar=aiohttp.DummyCookieJar(),
        timeout=aiohttp.ClientTimeout(total=POLICY["timeout_seconds"]),
        headers={"User-Agent": "KeibaResearchProvenanceAudit/1.0", "Accept": "text/html"}, trust_env=False) as session:
        for item in plan["probe_races"]:
            row = {"year":item["year"], "race_id":item["race_id"], "race_date":item["race_date"],
                "url":item["url"], "http_status":None, "attempted":False, "response_bytes":0,
                "body_sha256":None, "retrieved_at":None, "elapsed_seconds":None,
                "request_started_at":None,"response_received_at":None,
                "content_status":"NOT_ATTEMPTED", "table_present":False, "horse_header_count":0,
                "expected_runners":item["num_horses"], "horse_coverage_percent":None,
                "training_date_count":0, "numeric_clock_count":0, "stop_reason":stop,
                "historical_backfill":False, "usable_training_records":0}
            if stop:
                rows.append(row)
                continue
            owner = None
            try:
                admission(monitor)
                owner, _waited = await asyncio.wait_for(pacer.acquire("netkeiba.com",3),timeout=30)
                admission(monitor)
                started = time.monotonic()
                row.update(attempted=True,request_started_at=now())
                async with session.get(item["url"],allow_redirects=False) as response:
                    parts, size = [], 0
                    async for chunk in response.content.iter_chunked(65536):
                        size += len(chunk)
                        if size > POLICY["max_body_bytes"]:
                            raise ValueError("response_body_limit")
                        parts.append(chunk)
                    body = b"".join(parts)
                    row.update(response_received_at=now())
                    row["retrieved_at"]=row["response_received_at"]
                    row.update(http_status=response.status, response_bytes=len(body),elapsed_seconds=time.monotonic()-started)
                    digest = hashlib.sha256(body).hexdigest()
                    row["body_sha256"] = digest
                    with (rawroot/(item["race_id"]+".html")).open("xb") as stream:
                        stream.write(body)
                    result = inspect_body(body,item["race_id"])
                    result.update(source_hash=digest, retrieved_at=row["retrieved_at"],http_status=response.status,source_url=item["url"])
                    inspect_results.append(result)
                    for key in ("content_status","table_present","horse_header_count","training_date_count","numeric_clock_count"):
                        row[key] = result[key]
                    if item["num_horses"] and result["horse_header_count"]:
                        row["horse_coverage_percent"] = 100*result["horse_header_count"]/int(item["num_horses"])
                    row["historical_backfill"] = True
                    # Header coverage is not numeric data coverage, let alone verified PIT coverage.
                    if response.status in (403,429) or result["challenge"]:
                        stop = "HTTP_"+str(response.status) if response.status in (403,429) else "ACCESS_CHALLENGE"
                        row["content_status"] = stop
                        if response.status == 429:
                            pacer.observe("netkeiba.com",kind="training-audit",status=429,retry_after=retry_after(response.headers))
                    elif response.status != 200:
                        row["content_status"] = "HTTP_"+str(response.status)
                        if response.status >= 500:
                            stop = "SERVER_ERROR_STOP"
            except (ValueError,AccessPaused,aiohttp.ClientError,asyncio.TimeoutError) as exc:
                stop = type(exc).__name__+":"+str(exc)
                row["content_status"] = "NOT_CONFIRMED"
                row["stop_reason"] = stop
            finally:
                if owner:
                    pacer.release("netkeiba.com",owner)
            rows.append(row)
            print(json.dumps({k:row[k] for k in ("year","race_id","http_status","content_status","response_bytes")}),flush=True)
    pacer.close()
    with (OUT/"historical_recovery_probe.csv").open("x",encoding="utf-8",newline="") as stream:
        writer = csv.DictWriter(stream,fieldnames=list(rows[0]))
        writer.writeheader(); writer.writerows(rows)
    write(OUT/"probe_content_audit.json",{"responses":inspect_results,"no_source_values_joined_to_old_rows":True})
    present = sum(r["content_status"]=="DATA_PRESENT" for r in rows)
    write(OUT/"probe_result.json",{"completed_at":now(),"plan_sha256":sha(OUT/"experiment_plan.json"),
        "local_search_sha256":sha(OUT/"training_source_inventory.json"),"requests_attempted":sum(r["attempted"] for r in rows),
        "raw_responses_saved":len(inspect_results),"responses_with_workout_values":present,
        "HISTORICAL_TRAINING_RECOVERABLE":"UNKNOWN",
        "target_bound_event_valid_workout_rows":0,
        "recovery_gate":"Observable workout values alone are insufficient: require manual source race/horse/date binding and strictly earlier workout dates before promoting to YES/PARTIAL. No automatic promotion.",
        "interpretation":"Anonymous responses are a lower-bound accessibility probe; absence of observable workouts is not proof that historical data does not exist. No authenticated membership tested.",
        "strict_pit_rows_added":0,"normalized_backfill_rows_added":0,"stop_reason":stop,
        "bulk_acquisition_started":False,"authentication_attempted":False,"models_fitted":0})


def validate_samples(plan, selected, counts):
    if plan["probe_races"] != selected or plan["uncollected_JRA_races_by_year"] != counts:
        raise ValueError("probe samples differ from frozen label-free selection")
    if len(selected) != POLICY["max_requests"] or len({r["race_id"] for r in selected}) != len(selected):
        raise ValueError("probe request bound violated")
    for row in selected:
        if not re.fullmatch(r"[0-9]{12}",row["race_id"]) or row["url"] != "https://race.netkeiba.com/race/oikiri.html?race_id="+row["race_id"]:
            raise ValueError("probe endpoint must be exact approved race URL")


if __name__=="__main__":
    parser=argparse.ArgumentParser(); parser.add_argument("action",choices=("initialize","probe")); args=parser.parse_args()
    if args.action=="initialize": initialize()
    else: asyncio.run(probe())
