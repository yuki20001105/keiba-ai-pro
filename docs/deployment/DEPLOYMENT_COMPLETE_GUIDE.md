# Vercel + Railway デプロイガイド

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
NEXT_PUBLIC_SUPABASE_URL=https://grfwkutcsavqicaimssn.supabase.co
NEXT_PUBLIC_SUPABASE_ANON_KEY=eyJhbGci...
SUPABASE_SERVICE_ROLE_KEY=eyJhbGci...
NEXT_PUBLIC_ML_API_URL=https://keiba-ai-pro-api.railway.app
NEXT_PUBLIC_SCRAPING_API_URL=https://keiba-ai-pro-api.railway.app
```

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
