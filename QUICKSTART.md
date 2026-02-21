# 競馬AI Pro - クイックスタートガイド

## 🚀 セットアップ（初回のみ）

### 1. 環境変数の設定
```bash
# .env.local を作成（.env.local.example を参考に）
NEXT_PUBLIC_SUPABASE_URL=your_supabase_url
NEXT_PUBLIC_SUPABASE_ANON_KEY=your_anon_key
SUPABASE_SERVICE_ROLE_KEY=your_service_role_key
```

### 2. 依存関係のインストール
```bash
# Node.js依存関係
npm install

# Python依存関係（FastAPI用）
cd python-api
python -m venv .venv
.\.venv\Scripts\activate  # Windows
pip install -r requirements.txt
cd ..

# ML用Python環境
cd keiba
python -m venv .venv
.\.venv\Scripts\activate  # Windows
pip install -r requirements.txt
cd ..
```

---

## 🎮 サーバーの起動・停止

### VS Codeタスク（推奨）
- **F7**: 両サーバーを起動
- **Shift+F7**: 両サーバーを停止

### PowerShellコマンド
```powershell
# 起動
npm run up

# 停止
npm run down
```

### 個別起動
```bash
# Next.js（ポート3000）
npm run dev

# FastAPI（ポート8000）
cd python-api
.\.venv\Scripts\python.exe main.py
```

---

## 👤 管理者設定

### 初回ログイン
1. http://localhost:3000 にアクセス
2. ログインボタンをクリック
3. 既存アカウントでログイン: `yuki20001105@icloud.com`

### 管理者権限を付与
```powershell
# ユーザーを管理者に
.\.venv\Scripts\python.exe set_admin.py your@email.com

# ユーザー一覧を確認
.\.venv\Scripts\python.exe set_admin.py --list
```

---

## 📱 主要ページ

- **トップ**: http://localhost:3000
- **ログイン**: http://localhost:3000/auth/login
- **ホーム**: http://localhost:3000/home
- **管理者ダッシュボード**: http://localhost:3000/admin（管理者のみ）
- **データ収集**: http://localhost:3000/data-collection（管理者のみ）
- **モデル訓練**: http://localhost:3000/train（管理者のみ）
- **予測**: http://localhost:3000/predict-batch
- **FastAPI Docs**: http://localhost:8000/docs

---

## 🛠️ トラブルシューティング

### サーバーが起動しない
```powershell
# ポートを確認
netstat -ano | findstr :3000
netstat -ano | findstr :8000

# プロセスを強制終了
taskkill /PID <プロセスID> /F
```

### データベースエラー
```powershell
# Supabase接続確認
.\.venv\Scripts\python.exe -c "from supabase import create_client; import os; from dotenv import load_dotenv; load_dotenv('.env.local'); print('OK' if os.getenv('NEXT_PUBLIC_SUPABASE_URL') else 'NG')"
```

---

## 📚 詳細ドキュメント

- **セットアップ**: `docs/setup/`
- **機能説明**: `docs/features/`
- **開発ガイド**: `docs/development/`

---

## 🎯 開発フロー

1. **データ収集**: `/data-collection` でnetkeibaからデータ取得
2. **モデル訓練**: `/train` で機械学習モデルを訓練
3. **予測**: `/predict-batch` で予測を実行
4. **収支管理**: `/dashboard` で結果を確認
