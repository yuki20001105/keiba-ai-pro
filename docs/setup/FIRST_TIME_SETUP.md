# 🚀 初回セットアップガイド

現在、データベースにユーザーが登録されていません。以下の手順で進めてください。

## 📋 ステップ1: アカウント作成

1. **サーバーを起動**
   ```powershell
   npm run up
   ```

2. **ブラウザで新規登録**
   - http://localhost:3000 を開く
   - 「新規登録」をクリック
   - メールアドレス: `yuki20001105@icloud.com`
   - パスワードを設定
   - 「登録」をクリック

3. **確認メールをチェック**
   - Supabaseから確認メールが届く
   - メール内のリンクをクリックして認証

4. **ログイン**
   - 認証後、自動的にログイン
   - `/home` にリダイレクトされます

## 📋 ステップ2: 管理者権限を設定

アカウント作成後、以下のいずれかの方法で管理者に設定:

### ⚡ 方法1: VS Code Task

```
Ctrl+Shift+P → "Tasks: Run Task" → "Set Admin (Prompt Email)"
メールアドレス入力: yuki20001105@icloud.com
```

### 💻 方法2: PowerShell

```powershell
.\.venv\Scripts\python.exe set_admin.py yuki20001105@icloud.com
```

### 🗄️ 方法3: Supabase SQL Editor

1. https://supabase.com/dashboard/project/grfwkutcsavqicaimssn を開く
2. SQL Editor → New Query
3. 以下を実行:

```sql
-- roleカラムを追加（初回のみ）
ALTER TABLE public.profiles 
ADD COLUMN IF NOT EXISTS role TEXT DEFAULT 'user' 
CHECK (role IN ('admin', 'user'));

-- 管理者に設定
UPDATE public.profiles 
SET role = 'admin' 
WHERE email = 'yuki20001105@icloud.com';

-- 確認
SELECT email, role FROM public.profiles;
```

## 📋 ステップ3: 管理者機能にアクセス

1. **ブラウザをリロード** (Ctrl+R)
2. `/home` で「👑 管理者ダッシュボード」が表示される
3. 以下にアクセス可能:
   - `/admin` - 管理者ダッシュボード
   - `/data-collection` - データ収集
   - `/train` - モデル学習

---

## 🔍 確認コマンド

```powershell
# ユーザー一覧を表示
.\.venv\Scripts\python.exe set_admin.py --list
```

---

## 🚨 トラブルシューティング

### Q: 確認メールが届かない

1. 迷惑メールフォルダを確認
2. Supabase Dashboardで確認:
   - Authentication → Users
   - ユーザーのステータスが "Confirmed" になっているか確認

### Q: profilesテーブルにユーザーが作成されない

Supabaseのスキーマを確認:
```sql
SELECT * FROM auth.users;  -- 認証ユーザー
SELECT * FROM public.profiles;  -- プロファイル
```

もし `profiles` にデータがない場合、トリガーが機能していない可能性があります。

---

## 📚 次のステップ

管理者設定が完了したら:

1. **データ収集**: `/data-collection` でレースデータを取得
2. **モデル学習**: `/train` でAIモデルをトレーニング
3. **予測実行**: `/predict-batch` でレース予測

詳細は [README.md](../../README.md) を参照してください。
