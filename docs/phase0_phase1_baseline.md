# Phase 0/1 Baseline (正本・最小版)

作成日: 2026-07-11

## 1. リリース境界（現行）

### Core（本線）
- 認証/権限（Free/Premium/Admin）
- Data Collection Dry-run/Execute/履歴/統計
- 学習・モデル一覧・予測
- 購入履歴/結果入力/統計
- Production Readiness（read-only checks）

### Controlled Operations（承認付き運用）
- P0 repair plan / refresh plan / targeted refetch plan
- 再学習承認/実行
- active model 切替
- sandbox/staging write-readback

### High-risk / Optional（Coreと分離）
- IPAT自動投票
- 実課金（Stripe）
- 外部送信（Notion）
- OCR自動補正の本番自動化

## 2. 実行基盤（Node/Python/DB/環境）

- Node: 20 系（CI）
- Python: 3.11（CI）
- Frontend: Next.js 16
- Backend: FastAPI
- 主DB: SQLite（keiba/data/keiba_ultimate.db）
- 認証/購入記録: Supabase

CIで固定する重要環境変数（外部依存防止）:
- NEXT_PUBLIC_SUPABASE_URL=http://127.0.0.1:54321
- NEXT_PUBLIC_SUPABASE_ANON_KEY=ci-dummy-anon-key
- SUPABASE_URL=http://127.0.0.1:54321
- SUPABASE_SERVICE_KEY=ci-dummy-service-key
- SUPABASE_SERVICE_ROLE_KEY=ci-dummy-service-role-key
- NETKEIBA_RACE_WRITE_ENABLED=false
- ALLOW_STAGING_WRITE=false
- SCHEDULER_ENABLED=false

## 3. 起動方法（正規）

ローカル:
- Next.js: npm run dev
- FastAPI: python-api/.venv/Scripts/python.exe python-api/main.py

CI:
- Frontend L1: lint / tsc / vitest / build
- Python L1/L2: compile / import / pytest（feature consistency + contract）
- Contract smoke(aux) + deterministic contract gate
- Playwright public/fixture smoke
- Docker build check

## 4. Staging fixture 方針

- 実外部（netkeiba/Supabase本番）に依存しない fixture-first を基本とする。
- Contract gate は fixture JSON で pass/mismatch/contract-error を決定的に検証する。
- 実外部疎通は別フェーズ（staging real E2E）で段階導入する。

## 5. Test user 方針（Free/Premium/Admin）

- CI: 実ユーザー不使用（モック/fixtureのみ）
- Staging: free/premium/admin の固定テストユーザーを準備
- 権限検証は UI guard と backend guard を分離して確認

## 6. Next API route と FastAPI router 登録差分（現時点）

確認基準:
- Next API: src/app/api/**/route.ts
- FastAPI: python-api/main.py の app.include_router(...)

既知差分（要管理）:
- python-api/routers/export.py は存在するが main.py に include されていない。
- 一部 Next API は FastAPI proxy ではなく、Node 側で script spawn を実行（例: refresh-plan / p0-repair-plan）。

運用ルール:
- 差分は「意図あり/意図なし」を明示し、意図なし差分を Phase 2+ で解消する。
- route inventory は CI/監査で継続更新する。

## 7. Phase 1 の判定メモ

- L1/L2 の自動化は CI workflow に追加済み。
- ただし GitHub Actions 実行結果（Docker build 含む）を確認するまでは「ローカル完了・CI実証待ち」。
