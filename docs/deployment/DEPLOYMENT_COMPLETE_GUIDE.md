# Vercel + Railway デプロイガイド

> **Phase 3L-A safety notice (authoritative):** This historical guide is not an authorization to deploy. Production is currently **NOT_READY** and L3 is not reached. Do not run `vercel --prod`, `railway up`, apply a migration, promote a deployment, or change provider settings until the external approvals and Phase 3L-B evidence in `../phase3l_staging_readiness_gate.md` are complete. Phase 3L-A made zero external changes.

## 現行の環境変数正本

- Next.js server routes use `ML_API_URL` for general FastAPI calls and `SCRAPE_API_URL` for scrape/profiling calls.
- `NEXT_PUBLIC_API_URL` is a compatibility fallback only and is not a replacement for either server-only variable.
- `NEXT_PUBLIC_ML_API_URL` and `NEXT_PUBLIC_SCRAPING_API_URL` shown in older instructions are not consumed by the current implementation and must not be used.
- Preview/Staging/Production must each explicitly set both canonical variables to an approved HTTPS origin. A deployed localhost fallback is a failed deployment check.
- Secrets belong in the provider secret store. Never paste a real service-role key, internal secret, webhook secret, token or DSN into this guide, a PR, CI log or artifact.

The repository template `.env.production.template` is the canonical variable-name checklist. Its false-valued write/scheduler/saga switches must remain false during readiness inspection.

## 📋 概要
KEIBA AI PROを本番環境にデプロイする完全ガイド

---

## 🎯 Phase 4.1: Vercel デプロイ（Next.js）

### ステップ1: Vercel CLIインストール
```powershell
npm install -g vercel
```

### ステップ2: Vercelにログイン
```powershell
vercel login
```

### ステップ3: プロジェクト設定
```powershell
cd C:\Users\yuki2\Documents\ws\keiba-ai-pro
vercel
```

**質問に答える:**
- Set up and deploy? → `Y`
- Which scope? → `yuki20001105's projects`
- Link to existing project? → `Y`
- Project name: `keiba-ai-pro`
- Override settings? → `N`

### ステップ4: 環境変数設定
Vercel Dashboardで設定:
```
NEXT_PUBLIC_SUPABASE_URL=https://grfwkutcsavqicaimssn.supabase.co
NEXT_PUBLIC_SUPABASE_ANON_KEY=eyJhbGci...
SUPABASE_SERVICE_ROLE_KEY=eyJhbGci...
```

### ステップ5: 本番デプロイ
```powershell
vercel --prod
```

**デプロイURL:** `https://keiba-ai-pro.vercel.app`

---

## 🚂 Phase 4.2: Railway デプロイ（Python API）

### ステップ1: Railway CLIインストール
```powershell
npm install -g @railway/cli
```

### ステップ2: Railwayにログイン
```powershell
railway login
```

### ステップ3: プロジェクト作成
```powershell
cd C:\Users\yuki2\Documents\ws\keiba-ai-pro
railway init
```

**質問に答える:**
- Project name: `keiba-ai-pro-api`
- Environment: `production`

### ステップ4: 環境変数設定
```powershell
railway variables set PYTHONPATH=/app
railway variables set PORT=8000
```

### ステップ5: requirements.txt準備
python-api/requirements.txtが存在することを確認

### ステップ6: Procfile作成
```
web: cd python-api && uvicorn main:app --host 0.0.0.0 --port $PORT
```

### ステップ7: デプロイ
```powershell
railway up
```

### ステップ8: ドメイン設定
```powershell
railway domain
```

**デプロイURL:** `https://keiba-ai-pro-api.railway.app`

---

## 🔗 Phase 4.3: サービス連携

### Next.jsから Python APIを呼び出す設定

#### .env.productionファイル作成:
```env
APP_ENV=production
NEXT_PUBLIC_SUPABASE_URL=https://your-project.supabase.co
NEXT_PUBLIC_SUPABASE_ANON_KEY=replace-in-platform-environment
SUPABASE_SERVICE_ROLE_KEY=
ML_API_URL=https://api.example.com
SCRAPE_API_URL=https://api.example.com
NEXT_PUBLIC_API_URL=https://api.example.com
NETKEIBA_RACE_WRITE_ENABLED=false
ALLOW_STAGING_WRITE=false
PRED_LIMIT_ALLOW_FAIL_OPEN=false
SCHEDULER_ENABLED=false
PHASE3J_SAGA_RUNTIME_MODE=disabled
PHASE3J_REMOTE_EFFECTS_ENABLED=false
PHASE3J_WORKER_DISPATCH_ENABLED=false
PHASE3J_EXECUTION_UNLOCK_ENABLED=false
```

Staging uses `APP_ENV=staging` with a staging-only backend and Supabase project, while every write/unlock flag remains false until separately approved. Do not reuse production credentials in Staging.

### 外部設定の手動前提

Before any staging deployment or evidence collection, an authorized operator must independently configure and verify:

1. Vercel Production Branch=`main` and a distinct Staging Environment; `develop`/feature candidates must not create Production deployments.
2. A GitHub `staging` Environment with required reviewers and deployment-branch restrictions.
3. Repository rulesets/branch protection with all release-blocking checks and reviewed bypass policy.
4. An isolated staging backend and staging Supabase project with staging-only credentials.
5. Metadata-only confirmation that `ML_API_URL` and `SCRAPE_API_URL` exist in the correct scopes. Do not export their values into evidence.

These items are not completed by editing this file. See `../phase3l_staging_readiness_gate.md` for Phase 3L-B exit criteria.

---

## ✅ Phase 4.4: デプロイ確認

### Vercel（Next.js）確認:
```powershell
$response = Invoke-RestMethod -Uri "https://keiba-ai-pro.vercel.app"
Write-Host "✅ Next.js デプロイ成功" -ForegroundColor Green
```

### Railway（Python API）確認:
```powershell
$response = Invoke-RestMethod -Uri "https://keiba-ai-pro-api.railway.app/"
Write-Host "✅ Python API デプロイ成功" -ForegroundColor Green
Write-Host "Status: $($response.status)"
```

---

## 🔧 Phase 4.5: トラブルシューティング

### Vercelビルドエラー
```powershell
# ローカルでビルド確認
npm run build

# エラーがある場合は修正してから再デプロイ
vercel --prod
```

### Railwayデプロイエラー
```powershell
# ログ確認
railway logs

# 環境変数確認
railway variables
```

---

## 📊 Phase 4.6: 監視設定

### Vercel Analytics有効化:
1. Vercel Dashboard → Settings → Analytics
2. Enable Analytics

### Railway Metrics:
1. Railway Dashboard → Metrics
2. CPU, Memory, Network使用率を確認

---

## 🎉 完了チェックリスト

- [ ] Vercel CLIインストール
- [ ] Vercelにログイン
- [ ] Next.jsをVercelにデプロイ
- [ ] Vercel環境変数設定
- [ ] Railway CLIインストール
- [ ] Railwayにログイン
- [ ] Python APIをRailwayにデプロイ
- [ ] Railway環境変数設定
- [ ] .env.production作成
- [ ] Vercelデプロイ確認
- [ ] Railwayデプロイ確認
- [ ] サービス連携テスト
- [ ] Analytics有効化

---

## 🌐 本番URL

**フロントエンド:** https://keiba-ai-pro.vercel.app  
**API:** https://keiba-ai-pro-api.railway.app  
**Supabase:** https://grfwkutcsavqicaimssn.supabase.co

---

## 📞 サポート

問題が発生した場合:
- Vercel: https://vercel.com/support
- Railway: https://railway.app/help
- Supabase: https://supabase.com/support
