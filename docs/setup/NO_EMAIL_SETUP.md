# メール確認なしで管理者設定する方法

確認メールが届かない場合、Supabase Dashboardから直接設定できます。

## 🎯 最も簡単な方法: Supabase Dashboardで直接設定

### ステップ1: アカウントを作成

1. サーバーを起動: `npm run up`
2. http://localhost:3000 にアクセス
3. 「新規登録」をクリック
4. メールアドレス: `yuki20001105@icloud.com`
5. パスワードを設定
6. 「登録」をクリック

**→ 確認メールは無視してOK**

---

### ステップ2: Supabase Dashboardで確認

1. **Supabase Dashboardを開く**
   ```
   https://supabase.com/dashboard/project/grfwkutcsavqicaimssn
   ```

2. **Authentication → Users** を開く

3. ユーザーが作成されているか確認
   - メールアドレス: `yuki20001105@icloud.com`
   - ステータス: "Waiting for verification" または "Unconfirmed"

---

### ステップ3: SQL Editorで一括設定

1. **SQL Editor → New Query** を開く

2. 以下のSQLを実行:

```sql
-- 1. roleカラムを追加（初回のみ）
ALTER TABLE public.profiles 
ADD COLUMN IF NOT EXISTS role TEXT DEFAULT 'user' 
CHECK (role IN ('admin', 'user'));

-- 2. 既存ユーザーをuserに設定
UPDATE public.profiles 
SET role = 'user' 
WHERE role IS NULL;

-- 3. 特定のメールアドレスを管理者に設定
UPDATE public.profiles 
SET role = 'admin' 
WHERE email = 'yuki20001105@icloud.com';

-- 4. メール確認をスキップ（auth.usersテーブル）
-- ⚠️ 注意: これはセキュリティ上の理由で本番環境では推奨されません
UPDATE auth.users
SET email_confirmed_at = NOW(),
    confirmation_token = NULL
WHERE email = 'yuki20001105@icloud.com';

-- 5. 確認
SELECT 
  u.email,
  u.email_confirmed_at,
  p.role
FROM auth.users u
LEFT JOIN public.profiles p ON u.id = p.id
WHERE u.email = 'yuki20001105@icloud.com';
```

---

### ステップ4: ログイン

1. ブラウザで http://localhost:3000 にアクセス
2. 「ログイン」をクリック
3. メールアドレス: `yuki20001105@icloud.com`
4. パスワードを入力
5. ログイン成功！

---

### ステップ5: 管理者機能を確認

1. `/home` ページで「👑 管理者ダッシュボード」が表示される
2. 以下にアクセス可能:
   - `/admin` - 管理者ダッシュボード
   - `/data-collection` - データ収集
   - `/train` - モデル学習

---

## 🔍 確認方法

PowerShellで確認:
```powershell
.\.venv\Scripts\python.exe set_admin.py --list
```

出力例:
```
👑 管理者:
  - yuki20001105@icloud.com

合計: 1 ユーザー
```

---

## 📝 代替方法: Supabase Local Emailを使う（開発用）

開発環境では、Inbucket（ローカルメールサーバー）を使うこともできますが、
上記の直接設定方法が最も簡単です。

---

## ❓ よくある質問

### Q: なぜメールが届かないの？

開発環境では、Supabaseの無料プランでメール送信に制限があります。
また、SMTPの設定が必要な場合もあります。

### Q: 本番環境でも同じ方法を使える？

いいえ。本番環境では以下を設定してください:
1. Supabase Dashboard → Settings → Auth
2. SMTP設定を追加（SendGrid, AWS SES, etc.）
3. メール確認を有効化

### Q: セキュリティは大丈夫？

開発環境では問題ありませんが、本番環境では:
- メール確認を必須にする
- 強力なパスワードポリシーを設定
- RLSポリシーを適切に設定

---

## 🚨 トラブルシューティング

### profilesテーブルにデータがない

トリガーが動作していない可能性があります:

```sql
-- プロファイル作成トリガーを確認
SELECT * FROM information_schema.triggers 
WHERE trigger_name LIKE '%profile%';

-- 手動でプロファイルを作成
INSERT INTO public.profiles (id, email, role)
SELECT id, email, 'admin'
FROM auth.users
WHERE email = 'yuki20001105@icloud.com'
ON CONFLICT (id) DO UPDATE SET role = 'admin';
```

### auth.usersに更新権限がない

`auth.users` テーブルは通常、SQL Editorから直接更新可能です。
もしエラーが出る場合は、Dashboard → Authentication → Users から
ユーザーを手動で "Confirm" してください。

---

これで確認メールなしで管理者としてログインできます！
