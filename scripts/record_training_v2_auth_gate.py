"""Record a stopped authentication gate, not a completed parser experiment."""
from pathlib import Path
from datetime import datetime, timezone
import csv
import json
import sys

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/"scripts"))
from training_reconstruction_common import protection, invariant_projection, sha, read, write
from verify_market_segments_outputs import verify_files

OUT=ROOT/"reports/generated/model-experiments/training-data-reconstruction-v2"
V1=ROOT/"reports/generated/model-experiments/training-data-reconstruction-v1"
BLOCK="AUTHENTICATED_SOURCE_UNAVAILABLE"


def csv_write(name,fields,rows):
    with (OUT/name).open("x",encoding="utf-8",newline="") as stream:
        writer=csv.DictWriter(stream,fields);writer.writeheader();writer.writerows(rows)


def main():
    if OUT.exists(): raise ValueError("v2 destination already exists; never overwrite")
    v1=read(V1/"artifact_manifest.json")
    verify_files(V1,v1["files"]);verify_files(ROOT,v1["code_and_tests_sha256"])
    before=protection()
    stamp=datetime.now(timezone.utc).isoformat()
    OUT.mkdir(parents=True)
    source_paths=[ROOT/"python-api/scraping/constants.py",ROOT/"python-api/scraping/jobs.py",ROOT/"python-api/app_config.py"]
    inventory={"schema":"training-v2-authentication-gate-v1","recorded_at":stamp,"status":BLOCK,
        "scope":"Only authentication methods verifiably available to this execution; not a claim about all browser profiles or the user's membership",
        "connected_browser_inventory":{"browser_count":1,"type":"Codex In-app Browser","tab_count":2,
            "netkeiba_tabs":0,"chrome_or_edge_connected":False,"native_apps_enabled":False,
            "observation":"cua.getState; no navigation, login, cookie inspection, credentials or profile extraction"},
        "current_code":{"login_netkeiba_present":False,"oikiri_py_present":(ROOT/"python-api/scraping/oikiri.py").exists(),
            "scraping_jobs":"ordinary aiohttp.ClientSession, no netkeiba authenticated training route",
            "searched":"current scraping, app_config, routers, scripts and docs: login_netkeiba/is_logged_in/netkeiba login/credential key names",
            "source_sha256":{p.relative_to(ROOT).as_posix():sha(p) for p in source_paths}},
        "historical_code":{"revision":"05f6fac8db50193a8bc2aed1e6b892decf23c69b",
            "path":"python-api/scraping/constants.py","login_function_lines":[246,354],
            "formal_login_url":"https://regist.netkeiba.com/account/?pid=login",
            "configuration_key_names_only":["NETKEIBA_EMAIL","NETKEIBA_PASSWORD"],
            "operational_now":False,"restored_or_executed":False,
            "limits":"legacy redirect/cookie heuristics do not prove active membership or readable workout clocks; legacy debug logging also requires security review before any separately authorized revival"},
        "authentication_state":"NOT_VERIFIED","account_membership_checked":False,
        "secrets_read":False,"cookies_read_or_fabricated":False,"login_attempts":0,"new_source_requests":0,
        "reason":"No connected authenticated workout page and no currently wired formal authentication path; user required STOP when authentication unavailable",
        "resume_requirements":["user supplies legitimately saved authenticated workout HTML or makes an existing supported authenticated session available",
            "include original URL, race ID and actual acquisition timestamp separately; never share password/cookie/OTP",
            "preserve source, hash and prediction cutoff; backfill is not contemporaneous capture"]}
    write(OUT/"authenticated_source_inventory.json",inventory)
    write(OUT/"raw_html_manifest.json",{"status":BLOCK,"authenticated_html_count":0,"new_source_requests":0,"files":[],
        "fetched_at":None,"http_status":None,"reason":"No fetch occurred; null is not HTTP failure or a missing historical page"})
    write(OUT/"training_html_schema_audit.json",{"status":"NOT_EXECUTED_AUTH_GATE","authenticated_rows_audited":0,
        "major_courses":{c:"UNKNOWN" for c in ["栗坂","美坂","CW/栗W","美W"]},
        "prior_general_documentation":"v1 current public help is retained separately; not an authenticated clock-slot fixture",
        "old_parser_mismapping_this_phase":"NOT_CONFIRMED","old_vs_new_run":False})
    fields=["source_course","normalized_course","displayed_header","measurement_distance","html_selector",
            "parser_old_destination","old_mapping_correct","evidence_file","evidence_hash","verified","status"]
    csv_write("training_distance_mapping_verified.csv",fields,[{**dict.fromkeys(fields),"source_course":c,"verified":False,"status":BLOCK}
        for c in ["栗坂","美坂","CW/栗W","美W"]])
    csv_write("old_vs_new_parser_comparison.csv",["race_id","horse_number","field","source_value","old_value","new_value","classification","evidence_file"],[])
    write(OUT/"training_parser_v2_spec.json",{"status":"NOT_IMPLEMENTED_AUTH_GATE","validated":False,"actual_parser_created":False,
        "requirements_only_not_validated_spec":{"mapping":"explicit displayed headers only; no numeric-magnitude or ordinal-slot inference",
            "measurements":["measurement_distance","time_seconds"],"laps":["from_distance","to_distance","lap_seconds"],
            "preserve":["displayed_header_raw","displayed_value_raw","course_raw","html_selector","source_hash"],
            "do_not_modify":"legacy parser, original DB, v1, H04, Track A, Speed research, Production"}})
    csv_write("historical_html_format_audit.csv",["year","format_class","authenticated_examples","automatic_repair_allowed","reason"],
        [{"year":y,"format_class":"D_UNKNOWN","authenticated_examples":0,"automatic_repair_allowed":False,"reason":BLOCK} for y in range(2013,2027)])
    csv_write("historical_recovery_probe_v2.csv",["year","attempted","request_count","http_status","recovery","reason"],
        [{"year":y,"attempted":False,"request_count":0,"http_status":None,"recovery":"UNKNOWN","reason":BLOCK} for y in range(2019,2025)])
    quarantined=[]
    for name in ["post_race_date_anomalies.csv","unmatched_runner_records.csv"]:
        with (V1/name).open(encoding="utf-8-sig",newline="") as stream:
            for row in csv.DictReader(stream):
                quarantined.append({"training_id":row["id"],"race_id":row["race_id"],"horse_number":row["horse_number"],
                    "v1_quarantine_source":name,"source_checked":False,"corrected":False,"status":BLOCK})
    if len(quarantined)!=36 or len({r["training_id"] for r in quarantined})!=36:raise ValueError("quarantine source changed")
    csv_write("quarantine_source_check.csv",list(quarantined[0]),quarantined)
    registry=read(V1/"feature_candidate_registry.json")
    registry.update(schema="training-feature-candidates-v2-auth-gated",assessment_status="CARRIED_FORWARD_NOT_REEVALUATED_AUTH_GATE",
        v1_registry_sha256=sha(V1/"feature_candidate_registry.json"),new_ready_features=[])
    write(OUT/"feature_candidate_registry_v2.json",registry)
    gate={"status":BLOCK,"phase_completed":"authentication availability checks only",
        "TRAINING_FEATURE_RESEARCH_READY":"NO","models_fitted":0,"new_parser_validated":False,
        "automatic_repair_allowed":False,"new_source_requests":0,"strict_pit_records":0,"strict_pit_coverage_percent":0,
        "verified_historical_backfill_records":0,"feature_counts":{"READY":0,"HOLD":19,"REJECT":1},
        "historical_recovery":"UNKNOWN","quarantine_records_carried_forward":36,
        "blocking_conditions":["authenticated source unavailable","clock-bearing source samples not obtained","parser comparison not run","historical mappings not proven","research coverage and PIT unchanged"],
        "not_performed":["source page navigation","login","anonymous repeat probes","parser implementation","old/new comparison","historical probes","quarantine source repair","model fitting","commit","push"]}
    write(OUT/"training_research_gate.json",gate)
    summary="""# 調教データ再構築 v2 — 認証ゲートで停止

**AUTHENTICATED_SOURCE_UNAVAILABLE**。利用可能と確認できる正規認証経路がないため、ユーザー指定に従ってPhase1の認証確認で停止した。parser実証を完了したという意味ではない。

接続されたブラウザーはCodex In-app Browserのみで、netkeibaタブはなかった。これはそのブラウザーの保存Cookieや別ブラウザーのログイン状態・会員権限を調べた結果ではない。現行コードにはnetkeibaログイン処理が接続されていない。Git上の旧関数を復活させたり、秘密値やCookieを取り出したりしていない。

| 必須回答 | 結果 |
|---|---|
| A 認証済み時計HTML | NO、取得0ページ |
| B 栗坂 | UNKNOWN、実時計slot未検証 |
| C 美坂 | UNKNOWN、実時計slot未検証 |
| D CW/栗W | UNKNOWN、実時計slot未検証 |
| E 美W | UNKNOWN、実時計slot未検証 |
| F 旧parser誤mapping | NOT_CONFIRMED（今回の実HTML比較は未実施）。v1の固定slot問題と現行一般仕様の差は既知 |
| G 新parser | NOT_EXECUTED／未作成。VALIDATED/PARTIAL/FAILEDを実証結果として付けない |
| H 2019–2024回収 | UNKNOWN、再probe0通信 |
| I 85,040行の自動修復 | NO、許可しない |
| J STRICT_PIT | 0件・coverage0%。v1から増えていない |
| K READY特徴量 | なし。READY0/HOLD19/REJECT1を維持 |
| L TRAINING_FEATURE_RESEARCH_READY | NO |

## M 再開に必要なもの（最大3点）

1. 正規ログイン後に表示できる時計付き調教ページのHTMLを少数提供するか、正規認証済みのブラウザーをこの実行から利用可能にする。パスワード・Cookie・認証コードはチャットへ送らない。
2. HTMLごとに元URL・race_id・実際の取得日時を添える。最初は1〜2ページから確認し、栗坂・美坂・CW/栗W・美Wを各2〜3例含む最小集合へ広げる。個人のアカウント情報が含まれないことも確認する。
3. 認証済み原本の確認後にのみ、見出し・実slot・旧/新parser比較を再開する。後日取得と発走前取得を分け、過去年の一括修復・大量取得・学習は開始しない。

## 成果物の読み方

要求された成果物名は、未実施理由を明示したJSON/CSVとして用意した。比較CSVはヘッダーのみ、mapping行のverifiedは全false、HTTP statusはnull。0は今回の取得件数であり、「その年に調教データが存在しない」という意味ではない。

v1の原値、36件の隔離、候補19HOLD/1REJECTを維持。原DB・既存cache・H04・Track A・Speed研究・Production・使用中モデルは未変更。通信・ログイン・学習・commit・pushなし。
"""
    with (OUT/"summary.md").open("x",encoding="utf-8") as stream:stream.write(summary)
    audit="""# v2 audit boundary

This artifact set records a STOP at the user's authentication prerequisite. No authenticated response was obtained. No request, HTTP status, capture time, source hash or successful parser validation is invented. Null/empty evidence and NOT_EXECUTED states are intentional, not failed data acquisition observations.

Read-only checks: current source and documentation for formal authentication entry points; historical Git source for context; connected browser/tab inventory; v1 complete manifest and source hashes; Track A and prior-study protection checks. No secret values, .env contents, Cookie databases, browser profiles, password managers or authentication dialogs were inspected. Browser inventory alone cannot prove logged-out status of every possible session; the operational conclusion is that this run has no verified usable authenticated source.

The user asked to stop if authentication was unavailable. Therefore no anonymous retry, restoration of the legacy login function, experimental parser construction, current-day scraper, historical bulk work or research fitting followed. The v2 parser JSON contains requirements only, explicitly unvalidated. Historical format rows remain UNKNOWN. Quarantine rows retain their v1 IDs and unchanged status with source_checked=false. Feature candidates are carried forward rather than re-evaluated.

The report intentionally uses NOT_EXECUTED for the new parser rather than claiming one of VALIDATED/PARTIAL/FAILED as if an implementation had been tested. The carried-forward strict coverage0 and HOLD19/REJECT1 are source-bound to v1. v1's public help specification is not authenticated row-level evidence.

No new parser tests were run because no parser was built. Integrity verification hashes all v1 artifacts/code before and after, hashes the new status outputs, and checks protected state while allowing normal collector heartbeat changes. No frozen artifact is rewritten. The Computer Use safety workflow limited UI work to the connected-surface inventory; it did not supply or expand authentication access.
"""
    with (OUT/"audit.md").open("x",encoding="utf-8") as stream:stream.write(audit)
    after=protection()
    if invariant_projection(before)!=invariant_projection(after):raise ValueError("protected state changed")
    verify_files(V1,v1["files"]);verify_files(ROOT,v1["code_and_tests_sha256"])
    evidence={"status":BLOCK,"verified":True,"verification_scope":"integrity and safe stop only, not parser validation",
        "request_path":"C:/Users/yuki2/.codex/attachments/92d26301-aaf6-438f-b275-337849fe23d3/pasted-text.txt",
        "request_sha256":sha("C:/Users/yuki2/.codex/attachments/92d26301-aaf6-438f-b275-337849fe23d3/pasted-text.txt"),
        "v1_manifest_sha256":sha(V1/"artifact_manifest.json"),"v1_artifacts_verified":len(v1["files"]),
        "v1_sources_verified":len(v1["code_and_tests_sha256"]),"protected_before":before,"protected_after":after,
        "new_source_requests":0,"login_attempts":0,"models_fitted":0,"script_sha256":sha(Path(__file__))}
    write(OUT/"auth_gate_verification.json",evidence)
    files={p.relative_to(OUT).as_posix():{"sha256":sha(p),"bytes":p.stat().st_size} for p in sorted(OUT.rglob("*")) if p.is_file()}
    write(OUT/"artifact_manifest.json",{"status":BLOCK,"phase_complete":False,"research_ready":False,
        "recorded_at":stamp,"files":files,"script_sha256":sha(Path(__file__)),"v1_manifest_sha256":sha(V1/"artifact_manifest.json")})
    verify_files(OUT,files)
    print(json.dumps({"status":BLOCK,"records":len(files),"quarantine_carried_forward":36,"new_source_requests":0,
        "manifest_sha256":sha(OUT/"artifact_manifest.json"),"protected_unchanged":True}))


if __name__=="__main__":main()
