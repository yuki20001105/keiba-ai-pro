---
name: sysop
description: 'Sysop（システムオプ）スキル — 認証・インフラ・デプロイ・スケジューラ・テスト・git担当。Use when: ログイン・認証・Supabaseの設定に関する問題 / スケジューラの設定を変更したい / Railway/Renderへのデプロイを行いたい / git ブランチ・コミット・リリースを行いたい / E2Eテスト / Playwright テストを実行・修正したい / 管理者ページを改修したい / 環境変数・.env の設定 / ポート管理・サーバー起動・停止。Keywords: 認証, auth, Supabase, JWT, ログイン, admin, スケジューラ, scheduler, デプロイ, deploy, Railway, Render, git, コミット, ブランチ, テスト, Playwright, E2E, 環境変数, .env, サーバー起動, ポート'
---

# Sysop（システムオプ）— 認証・インフラ・デプロイ・テスト

認証基盤・スケジューラ・デプロイ・git・テストなど、システム全体の土台を担当。

---

## 担当ページ・ファイル

| 種別 | パス | 役割 |
|---|---|---|
| UI | `src/app/admin/page.tsx` | 管理者ダッシュボード（ユーザー管理） |
| UI | `src/app/login/page.tsx` | ログイン・認証 |
| UI | `src/app/home/page.tsx` | 認証後ハブ（API稼働確認） |
| Auth | `python-api/deps/auth.py` | JWT検証・require_premium |
| Auth | `python-api/supabase_client.py` | Supabaseクライアント |
| Auth | `src/lib/auth-fetch.ts` | JWT付きfetchラッパー |
| Infra | `python-api/scheduler.py` | APScheduler定時ジョブ |
| Infra | `python-api/main.py` | FastAPIエントリポイント |
| Infra | `python-api/middleware/` | CORS・レート制限 |
| Deploy | `railway.json`, `render.yaml`, `Procfile` | デプロイ設定 |
| Deploy | `Dockerfile`, `docker-compose.yml` | コンテナ設定 |
| Test | `e2e/` | Playwright E2Eテスト |
| Test | `src/__tests__/` | Vitest フロントエンドテスト |
| Test | `python-api/tests/` | pytest バックエンドテスト |

---

## サーバー起動・停止

```powershell
# FastAPI 起動（デバッグ）
# VS Code: "Start FastAPI" タスクを使用

# Next.js 起動
# VS Code: "Start Next.js" タスクを使用

# ポート解放
# VS Code: "Kill Port 8000" / "Kill Port 3000" タスクを使用

# 手動起動
python-api\.venv\Scripts\python.exe python-api/main.py
npm run dev
```

⚠️ `uvicorn.run(reload=False)` のため、コード変更後は**必ず再起動**が必要。

---

## 認証フロー

```
ブラウザ
  ↓ Supabase Auth でログイン
  → JWT（access_token）取得
  ↓
src/lib/auth-fetch.ts
  → Authorization: Bearer {token} ヘッダー付与
  ↓
Next.js API Route
  → FastAPI へ Bearer トークン転送
  ↓
python-api/deps/auth.py
  → Supabase で JWT 検証
  → profiles テーブルで subscription_tier 確認
```

---

## subscription_tier と権限

| tier | アクセス可能な機能 |
|---|---|
| `free` | 基本機能のみ |
| `premium` | 予測履歴・高度な統計（require_premium 必要） |
| `admin` | 管理者ページ |

### premium 設定手順

```powershell
cd "c:\Users\yuki2\Documents\ws\keiba-ai-pro"
$env:SUPABASE_URL="https://grfwkutcsavqicaimssn.supabase.co"
$env:SUPABASE_SERVICE_KEY="<service_key>"
& ".venv\Scripts\python.exe" -c "
import os, sys; sys.path.insert(0, 'python-api')
from supabase_client import get_client
client = get_client()
res = client.table('profiles').update({'subscription_tier': 'premium', 'role': 'admin'}).eq('id', '<user_id>').execute()
print('OK:', res.data)
"
```

---

## スケジューラ（APScheduler）

```
毎朝 6:00 JST  → _job_scrape_yesterday()  前日分の結果を確定取得
9:00〜22:00, 2時間おき → _job_scrape_today()  当日分レース取得
```

```python
# python-api/scheduler.py
_ENABLED = os.environ.get("SCHEDULER_ENABLED", "true").lower() not in ("false", "0", "no")
# 無効化: .env に SCHEDULER_ENABLED=false
```

---

## 環境変数（.env）

```env
# Supabase
SUPABASE_URL=https://grfwkutcsavqicaimssn.supabase.co
SUPABASE_SERVICE_KEY=sb_secret_...
SUPABASE_ANON_KEY=eyJ...

# JWT
JWT_SECRET_KEY=64e8cc...

# FastAPI
PORT=8000
SCHEDULER_ENABLED=true

# Next.js
NEXT_PUBLIC_SUPABASE_URL=...
NEXT_PUBLIC_SUPABASE_ANON_KEY=...
NEXT_PUBLIC_APP_URL=http://localhost:3000
NEXT_PUBLIC_API_URL=http://localhost:8000
ML_API_URL=http://127.0.0.1:8000
```

---

## git ワークフロー

詳細は `git-workflow` スキルを参照。概要:

```
develop  ← 日々の開発
  ↓
main     ← 動作確認済み安定版
  ↓
release  ← 本番リリース（v2, v3, ... タグ）
```

```powershell
# 通常のコミット
git add -A
git commit -m "feat: 機能追加の説明"
git push origin develop
```

---

## テスト実行

```powershell
# フロントエンド (Vitest)
npm test

# バックエンド (pytest)
keiba\Scripts\python.exe -m pytest keiba/keiba_ai/tests/ -v --tb=short

# E2E (Playwright) — サーバー起動後に実行
npx playwright test e2e/real-workflow.spec.ts --reporter=list --timeout 90000
```

詳細は `e2e-integration-test` スキルを参照。

---

## デプロイ設定

| プラットフォーム | 設定ファイル | ブランチ |
|---|---|---|
| Railway | `railway.json` | `release` |
| Render | `render.yaml` | `release` |
| Docker | `docker-compose.yml` | 任意 |

```json
// railway.json
{
  "build": { "builder": "nixpacks" },
  "deploy": { "startCommand": "python python-api/main.py" }
}
```

---

## よくあるトラブル

### FastAPI が 401 を返す
```
確認: Supabase JWT が有効か / profiles テーブルに行があるか
対処: ブラウザで再ログイン → 新しい access_token を取得
```

### スケジューラが動かない
```
確認: SCHEDULER_ENABLED=true になっているか
確認: APScheduler がインストールされているか
     python-api\.venv\Scripts\pip.exe show apscheduler
```

### Railway / Render のデプロイ失敗
```
確認: nixpacks.toml で Python バージョン指定
確認: requirements.txt が最新か
確認: 環境変数がプラットフォーム側に設定されているか
```
