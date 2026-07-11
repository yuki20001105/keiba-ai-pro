# Frontend Execution Completion Roadmap

## 1. 最終目的地

フロントエンドからスクレイピング運用を一気通貫で完結できる状態を最終目的地とする。

到達条件（ユーザーフロー）:
1. Data Collection 画面で Dry-run を実行し、見積と安全条件を確認できる。
2. 同画面から実行（execute）を開始できる。
3. ジョブ進捗を UI で監視できる（進行中/完了/失敗の識別）。
4. 正常完了時に完了サマリと fetch summary 履歴を確認できる。
5. 取得済みデータ統計・最近レース一覧に反映される。
6. missingness audit / P0 品質確認に遷移し、品質状態を確認できる。
7. 必要時に修復計画（Refresh Plan / P0 Repair Plan / targeted refetch / live validation）へ接続できる。

対象範囲:
- Data Collection（/data-collection）
- Refresh Plan（/data-collection/refresh-plan）
- P0 Repair Plan（/data-collection/p0-repair-plan）
- fetch summary history
- quality audit / P0 quality check（将来 UI 統合）

## 2. 正常完了の定義

### 2.1 UI 上の完了条件
- 実行ジョブが `completed` で表示される。
- 完了サマリ（期間、件数、経過秒）が表示される。
- fetch summary 履歴に対象ジョブが表示され、主要指標を確認できる。
- エラー時は 0 件表示にフォールバックせず、明示エラー表示になる。

### 2.2 backend job 上の完了条件
- `/api/scrape/status/{job_id}` が `status=completed` を返す。
- `result.fetch_summary` が保存され、`/api/scrape/history` で参照可能。
- 失敗時は `status=error` と `error` が返り、UI が適切に表示。

### 2.3 データ品質上の完了条件
- execute 完了後、最低限のデータ整合（保存件数・対象期間の反映）が確認できる。
- missingness audit で P0 異常の有無を把握できる。
- P0 異常がある場合は、原因分類（cache-missing / source-empty / alternate-page-required 等）を分離して扱える。

### 2.4 失敗時の扱い
- 失敗時は「実行失敗」と原因メッセージを UI で表示。
- 実行リトライ前に Dry-run で再見積を行う。
- 直接修復実行に進まず、まず計画（Refresh/P0/targeted/live validation）を確認する。

## 3. フェーズ分け

- Phase 1: Dry-run UI 完成
- Phase 2: 小規模 execute 正常完了
- Phase 3: job progress UI 改善
- Phase 4: 完了後 quality audit summary
- Phase 5: P0 quality dashboard
- Phase 6: P0 live validation UI
- Phase 7: 承認付き repair execution scaffold

## 4. 各フェーズ詳細

## Phase 1: Dry-run UI 完成

目的:
- Dry-run の可視性・可読性・誤解防止を完成させる。

実装内容:
- loading state（見積もり生成中、経過秒、0件誤表示防止）を維持。
- summary cards をカテゴリ別表示（DB existing / cache / resume / new fetch）。
- 古い履歴の未定義値は `-` 表示に統一。

対象ファイル:
- `src/app/data-collection/page.tsx`
- `e2e/data-collection-dry-run.spec.ts`

API route:
- `POST /api/scrape`
- `GET /api/scrape/status/{jobId}`
- `GET /api/scrape/history`

UI 表示項目:
- Dry-run summary cards
- rate-limit / retry-backoff / circuit-breaker
- dry-run 履歴（est req, db existing, new fetch, already covered）

安全条件:
- Dry-run は実 HTTP アクセスなし（見積のみ）。
- DB 更新なし。

E2E / smoke:
- `e2e/data-collection-dry-run.spec.ts`
- `scripts/run_keiba_smoke_suite.py`

完了条件:
- Dry-run 中と完了後の表示遷移が安定し、0件誤表示が再発しない。

コミットメッセージ案:
- `feat: improve scrape dry-run UX and summary cards`

## Phase 2: 小規模 execute 正常完了

目的:
- フロントエンドから execute を開始し、正常完了まで追える最小運用を確立。

実装内容:
- 小規模期間（短期）で execute 実行を標準運用化。
- 完了サマリ・履歴反映・取得済みデータ更新を同一画面で確認。

対象ファイル:
- `src/app/data-collection/page.tsx`
- `src/hooks/useBatchScrape.ts`
- `src/app/api/scrape/route.ts`

API route:
- `POST /api/scrape`
- `GET /api/scrape/status/{jobId}`
- `GET /api/scrape/history`
- `GET /api/data-stats`
- `GET /api/races/recent`

UI 表示項目:
- execute 進捗バー
- 完了トースト
- execute 履歴

安全条件:
- INV-07（最低 1.0 秒間隔）維持。
- 強制再取得時も no-downgrade ポリシーを崩さない。

E2E / smoke:
- data-collection execute 系 E2E
- smoke suite の execute 関連ステップ

完了条件:
- 小規模 execute が UI 起点で成功し、履歴・統計まで反映確認できる。

コミットメッセージ案:
- `feat: stabilize small-window scrape execute from frontend`

## Phase 3: job progress UI 改善

目的:
- 長時間実行でも進捗・状態・異常を運用者が迷わず把握できるようにする。

実装内容:
- queued/running/completed/error の状態表示を明確化。
- 進捗メッセージの分類（処理中日付、保存件数、再試行など）を整理。
- タイムアウト/再試行時の UI 文言を標準化。

対象ファイル:
- `src/app/data-collection/page.tsx`
- `src/hooks/useBatchScrape.ts`
- 必要に応じて `src/hooks/useJobPoller.ts`

API route:
- `GET /api/scrape/status/{jobId}`

UI 表示項目:
- 状態バッジ
- 進捗メッセージ
- エラー分類表示

安全条件:
- ポーリングの過剰化を防ぐ（既存頻度維持）。

E2E / smoke:
- ジョブ状態遷移 E2E
- smoke suite（status/history）

完了条件:
- 進捗監視だけで実行の成否と次アクションが判断できる。

コミットメッセージ案:
- `feat: improve scrape job progress visibility`

## Phase 4: 完了後 quality audit summary

目的:
- execute 完了後に品質確認へ自然接続する。

実装内容:
- execute 完了後、quality summary への導線を表示。
- missingness audit 結果の要約（P0 件数、重要分類）を表示。

対象ファイル:
- `src/app/data-collection/page.tsx`
- 新規 quality summary API route（必要時）

API route:
- 既存 script 出力参照 API（追加候補）
- `GET /api/scrape/history`

UI 表示項目:
- quality summary card
- audit への遷移リンク

安全条件:
- quality 表示は read-only。

E2E / smoke:
- 完了後 quality summary 表示 E2E
- missingness 関連 smoke

完了条件:
- execute 完了後に品質状況を UI で即確認できる。

コミットメッセージ案:
- `feat: add post-execution quality audit summary`

## Phase 5: P0 quality dashboard

目的:
- P0 異常の原因別可視化を UI で提供する。

実装内容:
- P0 件数を action/reason/classification 別に表示。
- source-empty-result-cells の分離表示を維持。
- cache-missing / alternate-page-required / source-result-missing の差を明示。

対象ファイル:
- `src/app/data-collection/*`（新規 dashboard ページ候補）
- `src/app/api/scrape/p0-repair-plan/route.ts`（read-only 維持）

API route:
- `POST/GET /api/scrape/p0-repair-plan`
- 必要に応じて診断結果参照 route

UI 表示項目:
- P0 サマリ
- 分類別内訳
- 推奨次アクション

安全条件:
- 実修復トリガは未解放。

E2E / smoke:
- p0-repair-plan UI E2E
- p0 診断系 smoke

完了条件:
- P0 問題を「何が」「どれだけ」「どう対処するか」で把握できる。

コミットメッセージ案:
- `feat: add p0 quality dashboard view`

## Phase 6: P0 live validation UI

目的:
- targeted refetch / live validation を UI で実行可能な read-only 検証フローにする。

実装内容:
- targeted refetch plan のプレビュー UI。
- live validation の小規模実行 UI。
- source-empty 診断表示 UI。

対象ファイル:
- 新規ページ（例: `/data-collection/p0-live-validation`）
- 関連 API route（read-only）

API route:
- targeted refetch plan route（追加候補）
- live validation route（追加候補）

UI 表示項目:
- plan 候補 URL
- validation 結果
- 分類別診断

安全条件:
- read-only / 小規模上限を強制。
- 本番 DB 更新は不可。

E2E / smoke:
- live validation UI E2E
- targeted/live smoke

完了条件:
- P0 修復前の検証が UI で完結し、実修復と分離される。

コミットメッセージ案:
- `feat: add p0 live validation UI flow`

## Phase 7: 承認付き repair execution scaffold

目的:
- 将来の実修復に向けた安全ガード付き実行基盤だけを先行整備する。

実装内容:
- 承認トークン/確認ダイアログ/対象件数上限の scaffold。
- 実行前チェックリスト（分類済み・no-downgrade・対象限定）導入。
- 初期段階は `not-implemented` / staging guard で運用。

対象ファイル:
- `src/app/api/scrape/refresh-plan/route.ts`
- `src/app/api/scrape/p0-repair-plan/route.ts`
- repair 実行 UI（将来）

API route:
- `PUT /api/scrape/refresh-plan`（将来解放）
- `PUT /api/scrape/p0-repair-plan`（将来解放）

UI 表示項目:
- 承認ステップ
- 実行対象プレビュー
- ガード違反時の拒否理由

安全条件:
- 段階解放（staging -> limited -> production）。
- 実行ログ監査必須。

E2E / smoke:
- guard 条件 E2E
- write guard smoke

完了条件:
- 承認なし実行が不可能で、誤実行リスクを抑えた土台がある。

コミットメッセージ案:
- `feat: add approved repair execution scaffold`

## 5. やってはいけないこと

1. 全件 refetch をデフォルトで走らせること。
2. UI から DB 直接更新を行うこと。
3. no-downgrade なしで既存値を上書きすること。
4. P0 原因未分類のまま実修復へ進むこと。
5. `source-empty-result-cells` を refetch 対象に混ぜること。

## 6. 優先順位

- P0: 実行完了を UI で確認できること。
- P1: 品質監査を UI で確認できること。
- P2: P0 修復計画を UI で確認できること。
- P3: 承認付き修復実行。

## 7. 完了までのマイルストーン指標

- M1: Dry-run 指標の解釈ミス（0件誤認）をゼロ化。
- M2: 小規模 execute の成功率を安定運用可能な水準に到達。
- M3: execute 完了後に quality summary まで 1 画面導線で到達。
- M4: P0 問題を UI 上で分類・計画・検証まで完了。
- M5: 承認付き実修復の scaffold を安全条件付きで準備完了。
