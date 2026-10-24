"""Produce an append-only PARTIAL_PILOT appendix from saved, scoped observations.

Stdlib only. No DB, network, application imports, credentials or model access.
This report is not the root agent's final artifact manifest or protection seal.
"""
from __future__ import annotations

import argparse
from collections import Counter
import csv
from datetime import date, datetime, timezone
import hashlib
import io
import json
from pathlib import Path
import re

ROOT = Path(__file__).resolve().parents[1]
PILOT = ROOT / "reports/generated/model-experiments/netkeiba-authenticated-pilot-v1"
OUTPUT_NAMES = (
    "authenticated_netkeiba_inventory.json", "training_authenticated_probe.csv",
    "training_html_schema_v2.json", "training_distance_mapping_v2.csv",
    "old_new_parser_comparison_v2.csv", "speed_index_source_audit.json",
    "speed_index_authenticated_probe.csv", "speed_index_db_comparison.csv",
    "historical_availability_matrix.csv", "pit_availability_audit.json",
    "forward_collection_design.md", "acquisition_scale_estimate.json",
    "summary.md", "audit.md",
)
YEARS = (2016, 2018, 2019, 2020, 2021, 2022, 2023, 2024, 2025, 2026)


def require(condition, message):
    if not condition:
        raise ValueError(message)


def sha256(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def js(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, allow_nan=False)


def day(raw):
    match = re.search(r"(\d{4})[/-](\d{2})[/-](\d{2})", raw or "")
    if not match:
        return None
    try:
        return date(*map(int, match.groups())).isoformat()
    except ValueError:
        return None


def horse_id(href):
    match = re.search(r"/horse/(\d+)(?:/|$|[?#])", href or "")
    return match.group(1) if match else None


def csv_text(rows):
    require(bool(rows), "empty CSV requires an explicit schema")
    fields = list(dict.fromkeys(key for row in rows for key in row))
    handle = io.StringIO(newline="")
    writer = csv.DictWriter(handle, fieldnames=fields, lineterminator="\n")
    writer.writeheader()
    writer.writerows(rows)
    return handle.getvalue()


def build():
    inputs = {}

    def read(relative):
        path = (PILOT / relative).resolve(strict=True)
        require(path.is_relative_to(PILOT.resolve()), "input outside pilot")
        inputs[path.relative_to(PILOT).as_posix()] = sha256(path)
        return json.loads(path.read_text(encoding="utf-8"))

    def evidence(relative):
        path = (PILOT / relative).resolve(strict=True)
        require(path.is_relative_to(PILOT.resolve()), "evidence outside pilot")
        inputs[path.relative_to(PILOT).as_posix()] = sha256(path)
        return inputs[path.relative_to(PILOT).as_posix()]

    capture = read("source/202606040611-training-capture.json")
    guide = read("source/202606040611-training-guide.json")
    contract = read("current-page-mapping-contract.json")
    training = read("comparison-mapped/experimental-parser-output.json")
    training_summary = read("comparison-mapped/parser-comparison-summary.json")
    training_comparison = read("comparison-mapped/parser-comparison.json")
    speed_capture = read("source/202609030411-speed-capture.json")
    speed_guide = read("source/202609030411-speed-guide.json")
    speed_summary = read("speed-comparison/summary.json")
    speed_parsed = read("speed-comparison/parsed-display-values.json")
    speed_comparison = read("speed-comparison/cell-comparison.json")
    source_sha = evidence(capture["source_file"])
    speed_sha = evidence(speed_capture["source_file"])
    require(source_sha == capture["source_sha256"] == contract["fixture_sha256"] ==
            training_summary["fixture_sha256"], "training source binding mismatch")
    require(evidence("current-page-mapping-contract.json") == training_summary["mapping_contract_sha256"],
            "training mapping contract mismatch")
    for item in contract["evidence"]:
        require(evidence(item["source_file"]) == item["sha256"], "mapping evidence mismatch")
    require(speed_sha == speed_summary["source_projection"]["sha256"], "speed source binding mismatch")
    require(evidence("source/202609030411-speed-capture.json") ==
            speed_summary["capture_receipt"]["sha256"], "speed capture binding mismatch")
    require(len(training["records"]) == training_summary["experimental_record_count"] and
            len(training_comparison) == training_summary["slot_count"], "training counts mismatch")
    require(len(speed_parsed) == speed_summary["source_rows"] and
            len(speed_comparison) == speed_summary["compared_cells"], "speed counts mismatch")
    require(sum(bool(row["exact_match"]) for row in speed_comparison) ==
            speed_summary["exact_match_cells_including_nulls"], "speed match count mismatch")
    require(training_summary["model_input_gate"] == "NO" and
            not speed_summary["model_input_permitted"], "source model gates must remain closed")

    pages = []
    training_probe = []
    common = {"original_http_response_preserved": False, "historical_publication_time_verified": False,
              "strict_pit": False, "model_input_gate": "NO"}
    current_source = {"source_url": capture["source_url"], "source_file": capture["source_file"],
                      "source_sha256": source_sha, "observed_at": capture["observed_at"]}
    for row in training["records"]:
        training_probe.append({
            "race_id": row["race_id"], "race_date": capture.get("race_date"),
            "horse_number": row["horse_number"], "horse_name": row["horse_name"],
            "horse_id": row["horse_id"], "training_date_raw": row["training_date_raw"],
            "training_date": row["training_date"], "course_raw": row["course_raw"],
            "track_condition": row["track_condition_raw"], "rider": row["rider_raw"],
            "pace": row["pace_raw"], "grade": row["grade_raw"],
            "slots_raw_json": js([slot["raw_text"] for slot in row["slots"]]),
            "slot_count": len(row["slots"]), "distance_mapping_scope": "EXACT_CURRENT_FIXTURE_ONLY",
            "capture_kind": capture["source_kind"], "full_sanitized_table_preserved": True,
            "source_identity_verification": "source horse header and next training row; no external roster",
            **current_source, **common})
    pages.append({"feature": "training", "race_id": capture["race_id"], "race_year": 2026,
                  "race_date": capture.get("race_date"), "rows": len(training["records"]),
                  "status": "AUTHENTICATED_TABLE_OBSERVED", "source_kind": capture["source_kind"],
                  "full_sanitized_table_preserved": True, **current_source, **common})
    historical = []
    for path in sorted((PILOT / "source").glob("*-training-projection.json")):
        projection = read(path.relative_to(PILOT).as_posix())
        require(projection.get("source_kind") == "browser_DOM_field_projection_not_original_HTTP_HTML",
                "unexpected historical projection source kind")
        rows = projection["rows"]
        require(len({row["horse_number"] for row in rows}) == len(rows), "duplicate historical horse number")
        require(all(row.get("horse_name") and horse_id(row.get("horse_href")) for row in rows),
                "historical displayed horse identity missing")
        transfer = projection.get("transfer_integrity", {})
        require(transfer.get("browser_length") == transfer.get("local_length") and
                transfer.get("browser_fnv1a") == transfer.get("local_fnv1a") and
                transfer.get("browser_fnv1a"), "historical projection transfer check mismatch")
        source = {"source_url": projection["source_url"], "source_file": path.relative_to(PILOT).as_posix(),
                  "source_sha256": evidence(path.relative_to(PILOT).as_posix()),
                  "observed_at": projection["observed_at"]}
        historical.append(projection)
        for row in rows:
            training_probe.append({
                "race_id": projection["race_id"], "race_date": projection["race_date"],
                "horse_number": row["horse_number"], "horse_name": row["horse_name"],
                "horse_id": horse_id(row["horse_href"]), "training_date_raw": row["training_date"],
                "training_date": day(row["training_date"]), "course_raw": row["course"],
                "track_condition": row["condition"], "rider": row["rider"], "pace": row["pace"],
                "grade": row["grade"], "slots_raw_json": js(row["slots"]), "slot_count": len(row["slots"]),
                "distance_mapping_scope": "UNVERIFIED_HISTORICAL_PROJECTION",
                "capture_kind": projection["source_kind"], "full_sanitized_table_preserved": False,
                "source_identity_verification": projection["identity_selector"], **source, **common})
        pages.append({"feature": "training", "race_id": projection["race_id"],
                      "race_year": int(projection["race_date"][:4]), "race_date": projection["race_date"],
                      "rows": len(rows), "status": "AUTHENTICATED_DATA_OBSERVED",
                      "source_kind": projection["source_kind"], "full_sanitized_table_preserved": False,
                      "transfer_integrity_scope": "declared browser/local match; canonicalization not persisted; not independently reproduced",
                      **source, **common})
    speed_source = {"source_url": speed_capture["source_url"], "source_file": speed_capture["source_file"],
                    "source_sha256": speed_sha, "observed_at": speed_capture["observed_at"]}
    pages.append({"feature": "speed_index", "race_id": speed_capture["race_id"],
                  "race_year": int(speed_capture["race_date"][:4]), "race_date": speed_capture["race_date"],
                  "rows": len(speed_parsed), "status": speed_summary["status"],
                  "source_kind": speed_capture["source_kind"], "full_sanitized_table_preserved": False,
                  **speed_source, **common})
    mapping_rows = []
    for rule in contract["rules"]:
        by_ordinal = {item["ordinal"]: item for item in rule["slots"]}
        for ordinal in range(1, rule["slot_count"] + 1):
            item = by_ordinal.get(ordinal, {})
            samples = [row["slots"][ordinal - 1]["raw_text"] for row in training["records"]
                       if row["course_raw"] == rule["course_raw"]]
            mapping_rows.append({"course_raw": rule["course_raw"], "slot_ordinal": ordinal,
                                 "distance_furlongs": item.get("distance_furlongs"),
                                 "lap_from_furlongs": item.get("lap_from_furlongs"),
                                 "lap_to_furlongs": item.get("lap_to_furlongs"),
                                 "status": "DOCUMENTED_PADDING" if ordinal in rule["required_blank_ordinals"]
                                 else "CAPTURE_BOUND_CONTRACT", "raw_samples_json": js(samples),
                                 "mapping_basis": rule["mapping_basis"], "scope": contract["scope"],
                                 "mapping_contract_sha256": evidence("current-page-mapping-contract.json"),
                                 "guide_sha256": evidence("source/202606040611-training-guide.json"),
                                 **current_source, **common})
    old_new_rows = []
    for row in training_comparison:
        slot = row["slot"]
        old_new_rows.append({key: row[key] for key in (
            "record_ordinal", "horse_number", "horse_name", "training_date_raw", "course_raw",
            "old_match_count", "old_time_field", "old_time_value", "old_lap_field", "old_lap_value")})
        old_new_rows[-1].update({
            "race_id": capture["race_id"], "slot_ordinal": slot["ordinal"], "raw_text": slot["raw_text"],
            "experimental_cumulative_value": slot["cumulative_value"],
            "experimental_lap_value": slot["lap_value"], "distance_furlongs": slot["distance_furlongs"],
            "lap_from_furlongs": slot["lap_from_furlongs"], "lap_to_furlongs": slot["lap_to_furlongs"],
            "mapping_status": slot["mapping_status"], "findings_json": js(row["findings"]),
            **current_source, **common})
    speed_probe = []
    for row in speed_parsed:
        for field, cell in row["cell_evidence"].items():
            speed_probe.append({"race_id": row["race_id"], "horse_number": row["horse_number"],
                                "horse_name": row["horse_name"], "horse_id": row["horse_id"],
                                "field": field, "cell_class": cell["cell_class"],
                                "raw_dom_text": cell["raw_dom_text"],
                                "display_text_after_hidden_removal": cell["text_after_first_hidden_removal"],
                                "hidden_sort_span_count": cell["hidden_sort_span_count"],
                                "display_value": cell["parsed_value"],
                                "sanitized_cell_html": cell["sanitized_html"], **speed_source, **common})
    speed_db_rows = [{**row, **speed_source, **common,
                      "frozen_snapshot_path": speed_summary["frozen_snapshot"]["path"],
                      "frozen_snapshot_sha256": speed_summary["frozen_snapshot"]["sha256_before"],
                      "comparison_scope": "ONE_RACE_CURRENT_DISPLAY_VS_FROZEN_VALUES"}
                     for row in speed_comparison]
    matrix = []
    for year in YEARS:
        for feature in ("training", "speed_index"):
            samples = [page for page in pages if page["race_year"] == year and page["feature"] == feature]
            matrix.append({"year": year, "feature": feature,
                           "availability": "DATA_OBSERVED_IN_SAMPLED_PAGE_ONLY" if samples else "UNKNOWN",
                           "probe_status": "PROBED" if samples else "NOT_PROBED",
                           "sampled_pages": len(samples), "sampled_rows": sum(page["rows"] for page in samples),
                           "race_ids_json": js([page["race_id"] for page in samples]),
                           "source_urls_json": js([page["source_url"] for page in samples]),
                           "source_sha256_json": js([page["source_sha256"] for page in samples]),
                           "source_kinds_json": js([page["source_kind"] for page in samples]),
                           "year_wide_availability_verified": False,
                           "pre_race_availability": "UNPROVEN" if samples else "UNKNOWN",
                           "coverage_estimate": None, "distance_mapping_historical": "UNKNOWN",
                           "model_input_gate": "NO"})
    training_events_before = sum(bool(row["race_date"] and row["training_date"] and
                                      row["training_date"] < row["race_date"]) for row in training_probe)
    pit = {"status": "PARTIAL_PILOT", "strict_pit_records": 0, "model_input_gate": "NO",
           "race_date_known_training_rows": sum(bool(row["race_date"]) for row in training_probe),
           "training_event_dates_before_known_race_date": training_events_before,
           "event_date_before_race_is_not_historical_capture_proof": True,
           "pages": [{**page, "pre_race_capture_verified": False, "revision_history_verified": False,
                      "classification": "CURRENT_OBSERVATION_WITHOUT_PRE_RACE_PUBLICATION_PROOF"}
                     for page in pages],
           "required_evidence": ["trusted capture timestamp before an explicit race deadline",
                                 "versioned source payload hash and capture identity",
                                 "documented publication/revision policy or prospective version history",
                                 "race and horse identity validation without outcome leakage"]}
    schema = {"schema_version": "training-html-schema-v2-experimental", "status": "PARTIAL_PILOT",
              "production_ready": False, "current_fixture_sha256": source_sha,
              "current": {"selector": capture["selector"], "observed_records": len(training["records"]),
                          "horse_header": "tr.HorseList[id^=tr_] with Umaban/Horse_Info",
                          "data_row": "following HorseList with Training_Day",
                          "time_slot": "td.TrainingTimeData ul.TrainingTimeDataList > li",
                          "slot_counts": dict(Counter(str(len(row["slots"])) for row in training["records"])),
                          "slot_contents": ["raw_html", "raw_text", "nested_text_nodes", "ordinal",
                                            "cumulative_candidates", "lap_candidates"],
                          "per_slot_F_headers_present": False,
                          "mapping_source": "hash-bound same-page help plus current captured layout"},
              "historical_projections": [{"race_id": row["race_id"], "identity_selector": row["identity_selector"],
                                         "full_HTML_preserved": False, "distance_mapping_verified": False}
                                        for row in historical],
              "unsupported_formats": "Any unobserved year/course/layout; unknown stays null",
              "existing_85040_records_corrected": 0,
              "guide_sha256": evidence("source/202606040611-training-guide.json"),
              "guide_text": guide, "model_input_gate": "NO"}
    inventory = {"status": "PARTIAL_PILOT", "phase_complete": False, "pages": pages,
                 "preservation_scope": {"original_HTTP_responses": 0,
                                        "full_sanitized_training_tables": 1,
                                        "sanitized_speed_cell_projection_pages": 1,
                                        "historical_training_text_projection_pages": len(historical),
                                        "all_requested_original_HTML_captured": False,
                                        "account_credentials_cookies_or_headers_persisted": False},
                 "additional_surfaces": [
                     {"surface": "master_speed_index", "status": "HELP_OBSERVED_ONLY",
                      "numeric_values_or_entitlement_verified": False,
                      "source_url": speed_guide["source_url"],
                      "source_sha256": evidence("source/202609030411-speed-guide.json")},
                     {"surface": "training_intermediate_workouts", "status": "NOT_PROBED"},
                     {"surface": "speed_ranking_results_deviation_views", "status": "LINKS_OBSERVED_NOT_PROBED"}],
                 "scope_limitations": ["small nonrandom page sample; no year-wide availability inference",
                                       "current-page mapping does not repair 85040 stored training records",
                                       "speed equality does not prove formula or historical PIT",
                                       "no bulk acquisition or production integration"],
                 "pending_work": [
                     "Current-page target of 2-3 races per feature not met: one current training page and one speed page",
                     "Training years 2016,2018,2019,2020,2023,2024,2025 not probed",
                     "Historical speed pages for all requested past years not probed",
                     "2021/2022 training sampled once per year; no representative or annual coverage estimate",
                     "Historical course/layout mapping and old-parser comparison not performed on text-only projections",
                     "Intermediate workouts and alternative training tabs not probed",
                     "Actual Master index values, entitlement and parsing not verified",
                     "Speed ranking/results/deviation pages only seen as links",
                     "Original HTTP responses not captured; historical full table HTML not preserved",
                     "Historical publication/revision times and prospective pre-race PIT evidence absent",
                     "Provider formula, constants, rounding and complete aggregation windows not verified",
                     "Request accounting, latency, allowed bulk rate and yearly volume remain unmeasured",
                     "No research-ready feature release, bulk acquisition, 85040-row repair or production integration"
                 ], "model_input_gate": "NO"}
    speed_audit = {"status": "PARTIAL_PILOT", "source_summary": speed_summary,
                   "observed_help": speed_guide,
                   "help_source_sha256": evidence("source/202609030411-speed-guide.json"),
                   "normal_index_semantics": speed_guide["normal"],
                   "comparison_is_reconstruction": False, "provider_formula_verified": False,
                   "publication_or_revision_policy_verified": False, "RECONSTRUCTION_VALIDATED": False,
                   "master_numeric_values_verified": False, "model_input_gate": "NO",
                   "display_token_counts": dict(Counter(row["display_text_after_hidden_removal"]
                                                       for row in speed_probe if row["display_value"] is None)),
                   "asterisk_display_cells": sum("*" in row["display_text_after_hidden_removal"] for row in speed_probe),
                   "display_missing_tokens_have_identical_semantics": "NOT_ESTABLISHED",
                   "limitation": "126/126 exact cells belongs to one race and includes 12 both-missing cells"}
    conflicts = training_summary["finding_counts"].get("DISTANCE_LABEL_CONFLICT_WITH_CONTRACT", 0)
    historical_description = "、".join(f'{row["race_date"][:4]}年 {len(row["rows"])}頭'
                                     for row in historical) or "未実施"
    summary = f"""# 認証付き情報源の限定パイロット

状態は PARTIAL_PILOT。全体調査は未完了で、モデル入力は NO のままです。

現行の調教ページは13頭・65枠を保存し、坂路7頭の同じ21枠について累積距離ラベル21件・ラップ区間ラベル21件の旧パーサーとの差を確認しました。数値差はありません。対応は今回のHTMLと説明文だけに適用し、既存85,040行は補正していません。

65枠には56組の数値、坂路の配置用空欄7枠、ＣＷの欠測2枠が含まれます。距離対応済み58枠という数は数値58個を意味しません。

過去年の調教は {historical_description} のページで表示値を確認しました。保存物は構造化DOMテキスト投影で、当時の公開時刻や改訂履歴は不明です。未調査年・未調査画面は UNKNOWN とし、年間取得可能率を推定していません。

タイム指数は1レース18頭・126セルで保存DBと一致しました（整数114、双方欠損12）。正式計算式、歴史的な公開可用性、単一走の指数再構成は検証できていません。

原HTTP応答は保存していません。現行調教はサニタイズ済み表全体、指数はセルHTML投影、過年調教はテキスト投影です。アカウント情報・認証情報は保存していません。

厳密なPIT適合は0件。年別サンプル追加、過年の画面形式と距離対応、指数の集計期間・計算式・公開/改訂時刻、前日/当日の将来取得による検証が残っています。大量取得・既存DB更新・学習・H04/Track Aの更新は実行していません。

未完了項目は、現行2〜3レースの目標、2016・2018・2019・2020・2023〜2025年の調教、全対象過去年の指数、2021/2022年の標本拡大、中間調教、Master実数値、指数別画面、原HTTP/過年表HTMLの保存、計算式・改訂履歴・取得負荷の確認です。今回の資料はRESEARCH_READY判定を出しません。
"""
    require(conflicts == 21 and speed_summary["compared_cells"] == 126 and
            speed_summary["cell_relation_counts"] == {"equal_integer": 114, "both_missing": 12},
            "fixed pilot narrative no longer matches inputs")
    forward = """# 将来収集の設計案（未実行）

本資料は限定パイロット後の設計であり、監視・自動取得・大量取得を開始するものではありません。

1. 許可された対象画面・期間・頻度を確定し、初回は単一実行・低並列で小標本を取得する。取得件数の上限と停止条件を記録する。
2. 認証情報を出力せず、race_id、馬ID、取得URL、選択画面、観測時刻、応答完了時刻、利用可能なHTTP状態、パーサー版、情報源の版を記録する。今回のDOM観測にはHTTP応答時刻を遡って補わない。
3. 原応答を保存できる許可済み経路では限定ページの応答本体とSHA256を不変保存する。画面のDOMだけを取得した場合はDOMと明記し、アカウント欄・Cookie・トークン・認証ヘッダーを保存しない。
4. レースの明示的な締切前に複数時点を観測し、取得時刻・更新差分・公開/訂正状態を分離する。既存結果や将来の値を混ぜず、取得済み版だけで特徴量を作る。
5. 未知の画面構造・欠損・年/コース・馬キー不一致は隔離する。距離は当該形式の明示証拠がある場合だけ確定し、数値の大きさから推測しない。
6. ログイン切れ、CAPTCHA、403、429、アクセス拒否を検知したら停止し、回避しない。再試行回数・待機・取得予算を明示し、許可頻度が未確定の間は大量取得に進まない。
7. 前時点の値が再取得後に変わるかを記録し、事前取得によるPIT検証、独立した馬キー照合、モデル投入審査を通過してから利用可否を判断する。

現状は設計だけで、運用開始・H04/Track A・本番モデル・既存85,040行の修正を許可するものではありません。
"""
    scale = {"status": "UNMEASURED_PLANNING_ONLY", "bulk_acquisition_started": False,
             "observed_saved_page_units": len(pages), "sample_is_nonrandom": True,
             "actual_HTTP_request_count": None, "network_requests_per_page": None,
             "page_latency_seconds": None, "authenticated_failure_rate": None,
             "annual_race_inventory_count": None, "year_wide_availability_rate": None,
             "approved_bulk_rate_or_interval": None,
             "logical_page_formula": "races * feature_pages_per_race * observation_passes",
             "duration_formula": "logical_pages * measured_mean_page_seconds + approved_spacing + retry_budget",
             "illustrative_scenarios_not_network_request_estimates": [
                 {"races": n, "feature_pages_per_race": 2, "observation_passes": 2,
                  "logical_page_units": n * 4, "estimated_duration_seconds": None}
                 for n in (10, 100, 1000)],
             "counts_exclude_browser_assets_navigation_and_failed_attempts": True,
             "source_pages": [{key: page[key] for key in ("race_id", "feature", "source_url", "source_sha256")}
                              for page in pages],
             "remaining_measurements": ["transport request/response accounting", "latency by page type",
                                         "auth expiry and error rates", "representative availability by year",
                                         "allowed acquisition scope and rate"]}
    audit = """# 付録の監査範囲

この付録は保存済みのパイロットJSON/HTMLだけから作成した限定報告です。原DB・ネットワーク・アカウント情報・モデルを読み書きしません。報告時の入力ハッシュを確認し、出力は新規作成だけで既存ファイルを上書きしません。

現行調教の距離対応は同一ページの説明文と5枠の構造に根拠があります。坂路は先頭の空欄と続く4個の時計を観測して対応づけ、値の大小から推定していません。確定範囲は単一HTMLのSHA256に限定されます。

2021/2022年の投影は保存済みの検証済み投影だけを使用します。探索時の未保存投影や未検証の馬結合は入力にしていません。馬番・馬名・馬IDは表示元に結びつきますが、過年調教について外部の出走馬台帳照合を完了したという主張はしません。

過年投影の転送メタデータにはブラウザ側とローカル側の同じ長さ・FNV値が記録されていますが、ハッシュ対象の正規化手順は保存されていません。この付録は申告された一致を確認するだけで、転送ハッシュの独立再現や真正性を証明していません。

指数の126セル一致は114個の整数と12個の双方欠損です。欠損は0として計算せず、隠れたソート用数値と表示値を区別します。近5走平均は同馬場の部分集合で、最大値は説明文に記載された期間/対象の範囲だけを述べます。Master指数は説明文を見ただけです。

指数CSVには6個のアスタリスク注記と、未6個・ハイフン6個の表示トークンを保持します。両者がnullとして一致しても欠損理由が同じとは判断しません。A〜Dは表示上の評価ラベルだけを記録し、正式な順序尺度の定義は確認していません。

原HTTP応答の保存要件は満たしていません。HTML投影とテキスト投影を原応答に言い換えず、取得時点が過去の公開時点を証明するとも扱いません。未調査年は取得不能ではなくUNKNOWNです。

本スクリプトは最終成果物manifestを生成しません。既存H04/Track A/本番状態の保護ハッシュ照合、全体の封印、テスト確認は親タスクの独立確認に委ねます。全フェーズ完了を宣言しません。

## 使用した入力

"""
    audit += "\n".join(f"- {name}: {digest}" for name, digest in sorted(inputs.items())) + "\n"
    artifacts = {
        OUTPUT_NAMES[0]: inventory, OUTPUT_NAMES[1]: csv_text(training_probe), OUTPUT_NAMES[2]: schema,
        OUTPUT_NAMES[3]: csv_text(mapping_rows), OUTPUT_NAMES[4]: csv_text(old_new_rows),
        OUTPUT_NAMES[5]: speed_audit, OUTPUT_NAMES[6]: csv_text(speed_probe), OUTPUT_NAMES[7]: csv_text(speed_db_rows),
        OUTPUT_NAMES[8]: csv_text(matrix), OUTPUT_NAMES[9]: pit, OUTPUT_NAMES[10]: forward,
        OUTPUT_NAMES[11]: scale, OUTPUT_NAMES[12]: summary, OUTPUT_NAMES[13]: audit,
    }
    return artifacts, inputs, {"status": "PARTIAL_PILOT", "training_rows": len(training_probe),
                               "historical_training_pages": len(historical),
                               "speed_probe_cells": len(speed_probe), "speed_db_cells": len(speed_db_rows),
                               "distance_mapping_rules": len(mapping_rows), "old_new_slots": len(old_new_rows),
                               "historical_matrix_rows": len(matrix), "model_input_gate": "NO"}


def execute():
    output = (PILOT / "report").resolve()
    require(output.is_relative_to(PILOT.resolve()), "output must stay inside pilot")
    require(not any((output / name).exists() for name in OUTPUT_NAMES), "report outputs already exist")
    script_hash = sha256(Path(__file__))
    artifacts, inputs, summary = build()
    require(set(artifacts) == set(OUTPUT_NAMES), "report artifact set mismatch")
    for name, digest in inputs.items():
        require(sha256(PILOT / name) == digest, "input changed while building report: " + name)
    require(sha256(Path(__file__)) == script_hash, "report script changed while building")
    output.mkdir(parents=True, exist_ok=True)
    for name, value in artifacts.items():
        content = value if isinstance(value, str) else json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False) + "\n"
        with (output / name).open("x", encoding="utf-8", newline="\n") as handle:
            handle.write(content)
    summary.update(output_files=len(artifacts), report_script_sha256=script_hash,
                   generated_at=datetime.now(timezone.utc).isoformat())
    return summary


def main():
    argparse.ArgumentParser(description=__doc__).parse_args()
    print(json.dumps(execute(), ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
