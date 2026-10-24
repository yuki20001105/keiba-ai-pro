"""No HTTP. Preserve the executed probe, inspect all raw anonymous responses."""
import csv
from collections import Counter
from datetime import datetime, timezone
import json
import re
import shutil
import sqlite3
import unicodedata
from training_reconstruction_common import ROOT, OUT, SNAPSHOT, sha, read, write, now
from bs4 import BeautifulSoup


def normalized(value):
    return unicodedata.normalize("NFKC",str(value)).strip()


def main():
    plan = read(OUT/"experiment_plan.json")
    rows = list(csv.DictReader((OUT/"historical_recovery_probe.csv").open(encoding="utf-8")))
    code = ROOT/"scripts/probe_training_reconstruction.py"
    dst = OUT/"provenance/executed_probe_training_reconstruction.py"
    with code.open("rb") as src, dst.open("xb") as target:
        shutil.copyfileobj(src,target)
    enriched=[]
    with sqlite3.connect(SNAPSHOT.resolve().as_uri()+"?mode=ro",uri=True) as db:
        db.execute("PRAGMA query_only=ON")
        for row in rows:
            if row["attempted"] != "True":
                continue
            file = OUT/"probe_raw"/(row["race_id"]+".html")
            if sha(file) != row["body_sha256"]:
                raise ValueError("raw response hash mismatch")
            body = file.read_bytes()
            # All inspected responses declare UTF-8; use BS4 encoding detection.
            soup = BeautifulSoup(body,"lxml")
            text = soup.get_text(" ",strip=True)
            table=soup.select_one("table.OikiriTable")
            horses=[]
            for tr in table.select("tr.HorseList") if table else []:
                n=tr.select_one("td.Umaban"); h=tr.select_one(".Horse_Name")
                if n and h and n.get_text(strip=True).isdigit():
                    horses.append((int(n.get_text(strip=True)),normalized(h.get_text(" ",strip=True))))
            actual={(int(n),normalized(h)) for n,h in db.execute("SELECT horse_number,horse_name FROM race_results_ultimate WHERE race_id=?",(row["race_id"],)) if n is not None and h}
            rank = re.search(r"['\"]memberRank['\"]\s*:\s*['\"]([^'\"]+)['\"]", body.decode("utf-8",errors="replace"))
            phrases=[word for word in ("調教タイム", "プレミアムサービス", "登録済みの方はこちらからログイン") if word in text]
            blocked=any(word in text.lower() for word in ("アクセス制限", "アクセスが制限されています", "verify you are human", "just a moment", "ただいまアクセスが集中しております"))
            enriched.append({"race_id":row["race_id"],"year":int(row["year"]),"source_hash":row["body_sha256"],
                "request_started_at":row["retrieved_at"],"response_received_at":None,
                "timing_caveat":"original probe retrieved_at field was request start, not response completion; no exact response-completion timestamp reconstructed",
                "source_url":row["url"],"http_status":int(row["http_status"]),"member_rank":rank.group(1) if rank else None,
                "table_present":table is not None,"runner_headers":len(horses),"runner_headers_matching_snapshot":sum(h in actual for h in horses),
                "header_keys_unique":len(horses)==len(set(horses)),"snapshot_runner_count":len(actual),
                "grade_cells":len(table.select(".Rank_A,.Rank_B,.Rank_C,.Rank_D")) if table else 0,
                "critic_cells":len(table.select(".Training_Critic")) if table else 0,
                "comment_cells":len(table.select(".TrainingReview_Cell")) if table else 0,
                "training_date_cells":len(table.select(".Training_Day")) if table else 0,
                "pace_cells":len(table.select(".TrainingLoad")) if table else 0,
                "clock_lists":len(table.select(".TrainingTimeDataList")) if table else 0,
                "page_access_restriction_detected":blocked,"membership_related_phrases":phrases,
                "clock_distance_headers_present":False,"strict_pit_usable_records":0,"event_valid_backfill_records":0,
                "content_class":"PUBLIC_RUNNER_EVALUATION_METADATA_ONLY_NO_WORKOUT_DATE_OR_CLOCK",
                "not_model_input":True})
    if any(r["page_access_restriction_detected"] for r in enriched):
        raise ValueError("retroactive access block detected; investigate before final report")
    sizes=[int(r["response_bytes"]) for r in rows if r["attempted"]=="True"]
    elapsed=[float(r["elapsed_seconds"]) for r in rows if r["elapsed_seconds"]]
    counts=plan["uncollected_JRA_races_by_year"]; n=sum(counts.values())
    # Scenarios, not extrapolated acquisition predictions: no member response size known.
    write(OUT/"recovery_estimate.json",{"missing_JRA_races_by_year":counts,"candidate_page_requests":n,
        "candidate_scope":"2019–2024 existing JRA race metadata without any stored workout, not the whole real-world calendar",
        "study_cohort_missing_races_2021_2022":6109-13,
        "coverage_of_full_real_world_schedule_not_verified":True,
        "one_get_per_race_assumption":True,"login_requests_retry_pagination_excluded":True,
        "paced_3s_no_wait_lower_bound_hours":n*3/3600,
        "paced_3s_6_12s_effective_scenarios_hours":{str(s):n*s/3600 for s in (3,6,12)},
        "anonymous_response_bytes_min":min(sizes),"anonymous_response_bytes_max":max(sizes),
        "anonymous_response_mean_bytes":sum(sizes)/len(sizes),"anonymous_latency_mean_seconds":sum(elapsed)/len(elapsed),
        "storage_if_responses_same_as_anonymous_bytes":n*sum(sizes)/len(sizes),
        "storage_if_100_500KiB_per_page_GiB":{str(k):n*k*1024/2**30 for k in (100,500)},
        "membership_response_size_and_success_rate":"UNKNOWN",
        "bulk_acquisition":"NOT_AUTHORIZED_OR_STARTED; estimate only; collector priority may extend wall time"})
    write(OUT/"probe_receipt_review.json",{"reviewed_at":now(),"new_network_requests":0,
        "executed_source_sha256":sha(dst),"responses":enriched,
        "total_runner_headers":sum(r["runner_headers"] for r in enriched),
        "total_runner_headers_matching_snapshot":sum(r["runner_headers_matching_snapshot"] for r in enriched),
        "total_grade_cells":sum(r["grade_cells"] for r in enriched),
        "HISTORICAL_TRAINING_RECOVERABLE":"UNKNOWN",
        "public_metadata_accessibility":"YES 18/18 anonymous pages; does not establish workout numeric access, event dates or historical publication version",
        "2021_2022_coverage_improvement":"UNKNOWN; zero usable workout rows added",
        "original_source_html_count":0,"new_anonymous_page_count":len(enriched),
        "STRICT_PIT_rows":0,"verified_event_valid_backfill_rows":0,
        "grade_or_comment_admission":False})
    print(json.dumps({"responses":len(enriched),"request_bytes":sum(sizes),"missing_races":n,"raw_values_available":0}))


if __name__=="__main__": main()
