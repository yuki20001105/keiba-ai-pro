"""Seal a terms-gated source audit; never fetch pages or access credentials."""
from pathlib import Path
import csv
import json
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
from training_reconstruction_common import protection, invariant_projection, sha, read, write, now
from verify_market_segments_outputs import verify_files

OUT = ROOT / "reports/generated/model-experiments/netkeiba-authenticated-source-audit-v1"
PRIOR = ROOT / "reports/generated/model-experiments"
REQUEST = Path("C:/Users/yuki2/.codex/attachments/2a453ac9-bbf8-440a-88ea-a8f878308708/pasted-text.txt")
BLOCK = "TERMS_REVIEW_REQUIRED"
TERMS_URL = "https://www.netkeiba.com/info/kiyaku.html"
FAQ_URL = "https://support.keiba.netkeiba.com/hc/ja/articles/39720493823129"


def csv_file(name, columns, rows=()):
    with (OUT / name).open("x", encoding="utf-8", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=columns)
        writer.writeheader()
        writer.writerows(rows)


def markdown(name, text):
    with (OUT / name).open("x", encoding="utf-8") as stream:
        stream.write(text)


def prior_state():
    result = {}
    for name in ("training-data-reconstruction-v1", "training-data-reconstruction-v2", "speed-figure-pit-v1"):
        directory = PRIOR / name
        manifest = read(directory / "artifact_manifest.json")
        verify_files(directory, manifest["files"])
        verify_files(ROOT, manifest.get("code_and_tests_sha256", {}))
        result[name] = {"manifest_sha256": sha(directory / "artifact_manifest.json"), "files": len(manifest["files"])}
    return result


def main():
    if OUT.exists():
        raise ValueError("Destination exists; never overwrite a prior audit")
    before = protection()
    frozen_before = prior_state()
    stamp = now()
    OUT.mkdir(parents=True)
    write(OUT / "authenticated_netkeiba_inventory.json", {
        "recorded_at": stamp, "status": BLOCK, "TERMS_REVIEW_REQUIRED": True,
        "membership": "USER_REPORTED_ACTIVE_NOT_INDEPENDENTLY_VERIFIED",
        "authentication_state": "NOT_VERIFIED_THIS_EXECUTION",
        "connected_surfaces": {"browser_type": "Codex In-app Browser", "browsers": 1, "tabs": 2, "netkeiba_tabs": 0},
        "scope_limit": "No tab is not proof of logged-out status in all profiles; manual login is deferred pending terms review",
        "browser_actions": ["cua.getState only; no navigation or credential inspection"],
        "authenticated_pages_observed": 0, "research_page_navigations": 0, "raw_html_saved": 0,
        "public_documentation_consulted": True, "public_documentation_network_request_count": None,
        "login_attempts": 0, "credentials_read_or_saved": False,
        "reason": "Requested automatic member-page storage and AI research scope not established by membership alone; user explicitly requires stop when uncertain"})
    write(OUT / "terms_review.json", {
        "status": BLOCK, "TERMS_REVIEW_REQUIRED": True, "review_recorded_at": stamp,
        "sources": [{"url": TERMS_URL, "sections": [12, 14, 15, 16], "evidence_kind": "official PC terms read via web tool",
            "summary": "Account sharing, non-private reproduction, commercial use and service interference are restricted; applicability to this specific workflow requires review."},
            {"url": FAQ_URL, "evidence_kind": "official scraping FAQ read via web tool",
             "summary": "Disruptive scraping may trigger restrictions; users are asked to check conformity with the terms."}],
        "not_a_legal_determination": True, "blanket_scraping_ban_claimed": False,
        "official_safe_request_rate_found": False,
        "unresolved": ["small automated authenticated HTML capture and local retention",
            "individual AI research and any processing by an external AI service",
            "future bulk or scheduled use requires separate scope approval"],
        "raw_terms_html_saved": False, "terms_raw_sha256": None,
        "support_contacted": False, "user_clarification_requested": True})
    csv_file("training_authenticated_probe.csv", ["race_id", "url", "attempted", "fetched_at", "http_status", "authentication_state", "full_clock", "reason"])
    write(OUT / "training_html_schema_v2.json", {"status": "NOT_EXECUTED_TERMS_GATE", "source_rows": 0,
        "authenticated_clock_schema_verified": False, "experimental_parser_implemented": False,
        "old_parser_fixture_run": False, "prior_general_help_is_not_authenticated_row_evidence": True})
    csv_file("training_distance_mapping_v2.csv", ["course", "displayed_header", "measurement_distance", "selector", "source_hash", "verified", "status"],
        [{"course": c, "verified": False, "status": "UNKNOWN_NOT_OBSERVED"} for c in ["栗坂", "美坂", "CW/栗W", "美W"]])
    csv_file("old_new_parser_comparison_v2.csv", ["race_id", "horse_number", "field", "source_value", "old_value", "new_value", "classification", "source_hash"])
    revision = "05f6fac8db50193a8bc2aed1e6b892decf23c69b"
    source_root = PRIOR / "speed-figure-pit-v1/provenance/sources" / revision
    sources = [source_root / "python-api/scraping/speed_figure.py", source_root / "python-api/scraping/storage.py"]
    write(OUT / "speed_index_source_audit.json", {
        "status": "LOCAL_BASELINE_ONLY_AUTHENTICATED_COMPARISON_NOT_EXECUTED",
        "legacy_pipeline": "external displayed integers copied directly, not locally aggregated",
        "authenticated_source_relation": "UNKNOWN", "comparison_pairs": 0,
        "legacy_revision": revision, "source_files": {p.relative_to(ROOT).as_posix(): sha(p) for p in sources},
        "url_template_in_legacy_code": "https://race.netkeiba.com/race/speed.html?race_id={race_id}",
        "legacy_selector": "table.SpeedIndex_Table; tr.HorseList.List; at least 13 td",
        "legacy_field_mapping": {"max_index": "sk__max_index", "avg_5_index": "sk__average_index",
            "dist_max_index": "sk__max_distance_index", "course_max_index": "sk__max_course_index",
            "index_last": "sk__index1", "index_2ago": "sk__index2", "index_3ago": "sk__index3"},
        "source_lines": {"speed_figure.py": {"url": 32, "value_parser": [35, 58], "html_parser": [109, 177], "field_mapping": [166, 172]},
            "storage.py": {"save_function": [278, 337]}},
        "limitations": ["DB rows lack raw source hash and certified capture/publication time",
            "created_at is DB insertion/replacement time, not proof of contemporary availability",
            "standard time index, index deviation and Master sub-indices are not conflated",
            "today's authenticated values, historical update policy and exact formula remain unverified"],
        "indices_observed_in_authenticated_pages": [], "new_feature_values_created": 0,
        "H04_independence": "UNKNOWN", "pearson": None, "spearman": None})
    write(OUT / "future_db_comparison_candidate.json", {
        "status": "CANDIDATE_ONLY_NOT_FETCHED_NOT_SCHEDULED", "source": "frozen audit-source.sqlite read-only agent review",
        "rule": "10-18 stored rows per race; race_date DESC then race_id ASC; all runners, no result-based choice",
        "race_id": "202609030411", "race_date": "2026-06-14", "runner_count": 18,
        "horse_key_and_name_matches": 18, "fallback_race_id": "202106010201",
        "metadata_caution": "venue/surface text in snapshot is garbled; do not silently repair or use for segment validation"})
    csv_file("speed_index_authenticated_probe.csv", ["race_id", "url", "attempted", "authenticated", "past_indices_visible", "fetched_at", "reason"])
    csv_file("speed_index_db_comparison.csv", ["race_id", "horse_number", "field", "db_value", "source_value", "match", "source_hash"])
    csv_file("historical_availability_matrix.csv", ["year", "training", "speed_index", "attempted_pages", "reason"],
        [{"year": y, "training": "UNKNOWN", "speed_index": "UNKNOWN", "attempted_pages": 0, "reason": BLOCK}
         for y in [2016, 2018, 2019, 2020, 2021, 2022, 2023, 2024, 2025]])
    write(OUT / "pit_availability_audit.json", {
        "status": "NO_NEW_SOURCE_EVIDENCE", "strict_pit_training_records": 0, "strict_pit_training_coverage_percent": 0,
        "new_strict_pit_speed_records": 0, "available_before_race": "UNKNOWN", "updated_before_race": "UNKNOWN",
        "result_dependent": "UNKNOWN_BY_INDEX_TYPE", "published_historical_indices_revised_later": "UNKNOWN",
        "historical_race_date_precedes_target_is_necessary_not_sufficient": True,
        "missing_publication_or_revision_evidence_cannot_be_replaced_by_DB_created_at": True,
        "capture_classes": ["STRICT_PIT_CANDIDATE", "HISTORICAL_BACKFILL", "TIME_UNVERIFIED"],
        "unconditional_mixing_for_validation": False})
    write(OUT / "acquisition_scale_estimate.json", {
        "status": "NOT_ESTIMATED_GATE_NOT_PASSED", "bulk_start": False,
        "target_race_count": None, "expected_request_count": None, "rate_limit": None,
        "raw_storage_bytes": None, "normalized_storage_bytes": None, "Track_A_impact": "NOT_MEASURED_NO_NEW_COLLECTOR",
        "reason": "Availability, permission and authenticated byte/request measurements are not established",
        "resume_method_proposal": "immutable source receipts plus an idempotent race/page-type/update-key ledger; preserve failures",
        "retry_policy_proposal": "no authentication/block bypass; honor Retry-After and stop on 401/403/429, login wall or schema ambiguity",
        "separate_explicit_bulk_approval_required": True})
    write(OUT / "research_gate.json", {"TERMS_REVIEW_REQUIRED": True, "TRAINING_FEATURE_RESEARCH_READY": "NO",
        "SPEED_FEATURE_RESEARCH_READY": "NO", "bulk_start": False, "models_fitted": 0,
        "feature_counts_carried_forward": {"READY": 0, "HOLD": 19, "REJECT": 1},
        "quarantine_carried_forward": 36, "automatic_repair": False,
        "current_phase_complete": False, "status": BLOCK})
    markdown("forward_collection_design.md", """# 別collector候補 — 設計のみ、未実装・未起動

## 開始条件

利用条件確認と正規認証の確認を別ゲートにする。手動ログインは本人が行い、AIはID・パスワード・Cookie・tokenを要求／抽出／保存しない。許可されたページを正規ブラウザーで表示する。endpoint推測・認証回避・認証wallの匿名再試行は禁止。大規模収集は別の明示承認を必要とする。

## 原本とセキュリティ

HTTP response bytesとbrowser serialized DOMを区別し、後者をHTTP原本と呼ばない。認証情報・ユーザー識別子を含むresponse headers、HAR、ブラウザープロファイルやHTML全体を無検査で永続化しない。許可された取得方法で本文をメモリー内検査し、秘密値を含む／判別不能な場合は保存せず停止する。検査ログにマッチした値を出さない。削除加工したDOMは加工物と明記し、元HTMLの同一性を主張しない。この検査・保存経路自体も未実装・要検証。

URLは観測済み公開ページURLだけ。認証tokenを含むURLをログへ出さない。source hashは実際に保存したbytesに対して計算する。raw参照、race_id、page_type、認証状態のboolean、request_started_at、response_completed_at、DOM_observed_at、parser versionを分離する。取得日時をファイル時刻やDB登録日時で代用しない。

## Append-onlyと時点

race_id × page_type × update_sequence単位に追記し、update_1/update_2/final_pre_raceを上書きしない。発走予定・変更情報の出典と観測時刻も保持。完了取得時刻が確実に発走前、かつ入力由来が時点保証されたものだけSTRICT_PIT候補にする。start time不明・通信が発走を跨ぐ場合はTIME_UNVERIFIEDまたは期限超過。最終判定は独立監査する。

過去年の後日取得はhistorical_backfill=true。history_race_date < target_race_dateだけで後日改訂リスクは解消しない。指数の公開遅延・改訂仕様を確認するまでPIT合格にしない。target race結果後の指数を当該レースの入力へ利用しない。調教時計の基準分布にも未来を入れない。

## Track Aの保護と失敗管理

Track Aへ機能を足さず別ledger・保存領域・workerとする。Track Aの取得予定／処理中／共有pacer待ちを優先し、新collectorが先に資源を予約しない。通信は直列、ページ遷移にもpacingを設ける。ブラウザー内部のAPI・画像等は遷移数より多く、ページ数を総HTTP数として報告しない。許可された方法で全体負荷を測れなければ本格取得に進めない。

401/403/429、login wall、空表、見出し曖昧、秘密値混入、Track A競合を失敗理由別に残す。未取得とデータ不存在を分離。restart時は成功原本を再取得せず、失敗・保留の再開条件を確認する。期限超過を取り返すためにアクセスを加速しない。GPU・モデル学習は本Phaseに不要。

## 次回pilot

許可と認証確認後、まず直近1ページで時計が見えることと安全な原本保存を実証。その後、依頼範囲の直近2〜3レース・過去少数へ広げ、主要4courseを含む最小集合に留める。旧DB照合は事前指定した1レース全18頭から行う。年別probeは初期検証PASS後だけ。ページ・HTTP件数・容量・所要時間・失敗数を実測してから規模見積りを作る。
""")
    markdown("summary.md", f"""# 認証済みnetkeiba情報源監査 — 利用条件確認待ち

**TERMS_REVIEW_REQUIRED = true**。正規会員契約の完了はユーザー申告として受け取り、契約無効や取得禁止と断定していない。今回の自動保存・研究利用に適用される許可範囲を確定できず、ユーザー指定の停止条件に従った。会員ページの新規取得0、比較0、学習0。

[公式規約]({TERMS_URL})は私的利用外の複製等・営業目的の利用・サービス妨害等を制限している。[公式スクレイピングFAQ]({FAQ_URL})は支障を生む取得への制限と規約適合確認を案内しており、この研究への一律許可や安全な固定アクセス間隔を示すものではない。これは法的可否の断定ではなく、本依頼のゲート判定。

接続中ブラウザーにはnetkeibaタブがなく、認証状態も未確認。別ブラウザーの会員状態やCookieは調べていない。利用条件を確認する前にログイン・会員ページ保存を進めていない。

| 最終回答 | 結果 |
|---|---|
| A 認証後の調教時計取得 | NO（本実行は未取得） |
| B 過去時計取得 | 未判定（UNKNOWN）。取得不能のNOではない |
| C 距離mapping確定 | NO。主要4courseの実slotは未検証 |
| D 旧parser誤mapping | NOT_CONFIRMED（今回の実HTML比較は未実施） |
| E TRAINING_FEATURE_RESEARCH_READY | NO |
| F 認証画面で取得した指数一覧 | なし（未取得）。旧コード上の7列とは区別 |
| G 現DBと現在の認証画面の関係 | UNKNOWN。旧生成経路はnetkeiba表示値の直接保存 |
| H 過去指数取得 | 未判定（UNKNOWN）。取得不能のNOではない |
| I PIT特徴量への変換 | NO（現時点で実証できない） |
| J H04速度特徴との独立性 | UNKNOWN。相関計算なし |
| K SPEED_FEATURE_RESEARCH_READY | NO |
| L 大量backfill開始 | NO。利用条件・認証・原本・時点・parserが未検証 |

旧保存処理は/race/speed.htmlの7表示値を直接保存し、自前集計ではない。ただし各既存行の原HTMLがなく、現在の画面との一致は未証明。比較候補は保存済み最新日のrace_id昇順で選んだ202609030411（18頭）。結果を見て選んでいない。まだ取得・照合していない。

## M 次の作業（最大3点）

1. 少数の会員HTMLの自動保存・個人AI研究利用・外部AI処理の許可範囲を、適用契約または運営の案内で確認する。問い合わせは代行送信していない。
2. 条件確認後、ご本人の手動ログインで利用可能なページを提示する。ID・パスワード・Cookie・tokenをチャットへ送らない。
3. まず少数原本から見出し・時計・指数とDBを照合し、PASS後に年別probeへ進む。大量取得・学習は別ゲート。

既存DB・cache・H04・Track A・Speed既存研究・使用中モデルは変更なし。調教READY0/HOLD19/REJECT1、隔離36件を維持。commit/pushなし。要求された比較CSVはヘッダーのみで、未測定値を0や不一致に置き換えていない。forward設計は候補であり収集サービスを実装・起動していない。
""")
    markdown("audit.md", """# Audit boundary

Executed: full user request read; official public terms and scraping FAQ reviewed; connected-surface inventory only; read-only legacy Speed provenance and deterministic frozen-DB candidate review; independent prior-manifest verification; stopped-state artifacts and forward design created in a new directory. No member-page requests, login automation, credentials/cookie/profile reads, source-value exports, new parser execution, model fits, production writes, commits or pushes.

TERMS_REVIEW_REQUIRED is the user's uncertainty gate, not a blanket assertion that scraping is prohibited. Membership is user-reported, not contradicted. The absence of a netkeiba tab is not proof of absence of a browser login. Authentication remains NOT_VERIFIED, not a definitive new account/access failure. A manual-login handoff is deferred until terms scope is clarified.

Public-documentation browsing made requests through the web tool; do not describe this phase as zero network requests overall. Zero research page navigation/probe/raw acquisition is distinct. Source document fetch completion times and raw response hashes were not available; review timestamps are not capture timestamps. Only URLs, section references and paraphrases are recorded, not full terms HTML.

Historic availability remains UNKNOWN rather than NONE. Comparison tables have no rows: no matches, mismatches or correlations are invented. No new index names are claimed as observed in member content. Legacy seven-field producer evidence is not row-level authenticated equivalence. Candidate selection uses only keys, dates and counts, not outcomes. The old surface/venue text issue remains explicitly uncorrected.

Training candidate status and strict-PIT0 are carried forward from frozen v1, not a new all-record audit. Existing20candidates and36quarantines were not modified. Forward capture design distinguishes raw bytes, serialized DOM and sanitized derivatives; no token-bearing full HTML/HAR/header export is implemented. PIT requires availability/revision evidence as well as history date.

The output verifier checks all named files and hashes and frozen prior artifacts before/after; normal Track A heartbeat changes are excluded from invariant equality. This is integrity verification of a stopped phase, not parser validation or a successful completed source experiment. The Computer Use skill was used for safe read-only surface discovery only.
""")
    after = protection()
    frozen_after = prior_state()
    if invariant_projection(before) != invariant_projection(after) or frozen_before != frozen_after:
        raise ValueError("Protected state changed; do not seal as passed")
    write(OUT / "integrity_verification.json", {"verified": True, "scope": "stopped audit integrity only",
        "protected_before": before, "protected_after": after, "frozen_prior": frozen_after,
        "request_sha256": sha(REQUEST), "script_sha256": sha(Path(__file__)),
        "authenticated_pages_acquired": 0, "comparison_pairs": 0, "models_fitted": 0})
    files = {p.name: {"sha256": sha(p), "bytes": p.stat().st_size} for p in sorted(OUT.iterdir()) if p.is_file()}
    write(OUT / "artifact_manifest.json", {"schema": "authenticated-source-gated-audit-v1", "status": BLOCK,
        "phase_complete": False, "recorded_at": stamp, "files": files,
        "request_sha256": sha(REQUEST), "script_sha256": sha(Path(__file__))})
    verify_files(OUT, files)
    for name in ["training_authenticated_probe.csv", "old_new_parser_comparison_v2.csv", "speed_index_authenticated_probe.csv", "speed_index_db_comparison.csv"]:
        with (OUT / name).open(encoding="utf-8", newline="") as stream:
            if list(csv.DictReader(stream)):
                raise ValueError("Unexecuted comparison must be empty")
    print(json.dumps({"status": BLOCK, "artifacts": len(files), "protected_unchanged": True,
        "authenticated_pages": 0, "comparison_pairs": 0, "manifest_sha256": sha(OUT / "artifact_manifest.json")}))


if __name__ == "__main__":
    main()
