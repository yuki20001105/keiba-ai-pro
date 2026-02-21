# 🚀 デプロイガイド

## Phase 2: デプロイ設定

### 1. Vercel (Next.js フロントエンド)

#### セットアップ手順:
```bash
# 1. Vercelにログイン
npm i -g vercel
vercel login

# 2. プロジェクトをデプロイ
vercel

# 3. 本番デプロイ
vercel --prod
```

#### 環境変数設定:
Vercelダッシュボードで設定:
- `NEXT_PUBLIC_SUPABASE_URL`: Supabase Project URL
- `NEXT_PUBLIC_SUPABASE_ANON_KEY`: Supabase Anon Key
- `NEXT_PUBLIC_API_URL`: FastAPI のデプロイURL（後で設定）

---

### 2. Railway (FastAPI バックエンド)

#### セットアップ手順:
```bash
# 1. Railway CLIインストール
npm i -g @railway/cli

# 2. ログイン
railway login

# 3. プロジェクト初期化
railway init

# 4. デプロイ
railway up
```

#### 環境変数設定:
Railway ダッシュボードで設定:
- `PYTHONPATH`: `/app`
- `PORT`: `8000` (自動設定される)
- データベース関連の環境変数（必要に応じて）

#### デプロイ後:
1. Railway から生成されたURLをコピー
2. Vercel の `NEXT_PUBLIC_API_URL` に設定
3. Vercel を再デプロイ

---

### 3. FastAPI CORS設定

`python-api/main.py` のCORS設定を更新:
```python
# 本番用
origins = [
    "https://your-app.vercel.app",  # Vercel URL
    "http://localhost:3000",        # 開発環境
]
```

---

## Phase 3: PWAアイコン作成

### 必要なアイコンサイズ:
- 192x192px
- 512x512px

### 簡易作成方法:
1. https://realfavicongenerator.net/ を使用
2. ロゴ画像をアップロード
3. PWAアイコンを生成
4. ダウンロードして `public/` に配置

---

## チェックリスト

### デプロイ前:
- [ ] `.env.production` に本番環境変数を設定
- [ ] PWAアイコン作成・配置
- [ ] CORS設定確認

### Vercel デプロイ:
- [ ] プロジェクト作成
- [ ] 環境変数設定
- [ ] デプロイ完了
- [ ] カスタムドメイン設定（オプション）

### Railway デプロイ:
- [ ] プロジェクト作成
- [ ] requirements.txt 確認
- [ ] デプロイ完了
- [ ] URLをVercelに設定

### 動作確認:
- [ ] フロントエンド表示確認
- [ ] API接続確認
- [ ] 認証機能確認
- [ ] PWAインストール確認（スマホ）

---

## トラブルシューティング

### CORS エラー:
```python
# main.py
app.add_middleware(
    CORSMiddleware,
    allow_origins=["https://your-app.vercel.app"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)
```

### ビルドエラー:
```bash
# 依存関係を最新化
npm install
pip install -r requirements.txt
```

---

## コスト試算

### 無料プラン:
- Vercel: 無料
- Railway: $5/月（クレジットカード登録必要）
- Supabase: 無料（500MB DB、50,000 月間アクティブユーザー）

合計: 約 $5/月 (約750円)
