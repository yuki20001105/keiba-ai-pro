# メール確認を無効化する設定ガイド

開発環境でメール確認を不要にし、サインアップ後すぐにログインできるようにします。

## 🎯 方法1: Supabase Dashboardで設定（推奨）

### ステップ1: Auth設定を変更

1. **Supabase Dashboardを開く**
   ```
   https://supabase.com/dashboard/project/grfwkutcsavqicaimssn/settings/auth
   ```

2. **Auth Providers** セクションに移動

3. **Email Auth** を探す

4. **「Enable email confirmations」のチェックを外す**
   - デフォルトではON（メール確認必須）
   - OFFにすると即座にログイン可能

5. **Save** をクリック

---

### ステップ2: 既存ユーザーを確認済みに変更（必要な場合）

もし既にユーザーが作成されている場合、SQL Editorで実行:

```sql
-- 全ユーザーを確認済みに設定
UPDATE auth.users
SET email_confirmed_at = NOW(),
    confirmation_token = NULL
WHERE email_confirmed_at IS NULL;

-- 確認
SELECT email, email_confirmed_at FROM auth.users;
```

---

### ステップ3: テスト

1. http://localhost:3000 にアクセス
2. 新規登録
   - メールアドレス: `yuki20001105@icloud.com`
   - パスワード: お好きなパスワード
3. **すぐにログインできる！**（確認メール不要）

---

## 🎯 方法2: 環境変数で制御（コードベース）

Next.jsのAuth設定を変更する方法:

### src/lib/supabase.ts を確認

現在の設定:
```typescript
export const supabase = createClient(
  process.env.NEXT_PUBLIC_SUPABASE_URL!,
  process.env.NEXT_PUBLIC_SUPABASE_ANON_KEY!
)
```

### サインアップ処理でautoConfirmを有効化

```typescript
// src/app/auth/signup/page.tsx で
const { data, error } = await supabase.auth.signUp({
  email,
  password,
  options: {
    emailRedirectTo: `${location.origin}/auth/callback`,
    data: {
      // 追加のユーザーメタデータ
    }
  }
})
```

⚠️ **注意**: これはフロントエンド側の設定で、実際の確認はSupabase側の設定に依存します。

---

## 🎯 方法3: 開発環境用の設定ファイル

### .env.local に追加

```bash
# 開発環境ではメール確認をスキップ
NEXT_PUBLIC_SKIP_EMAIL_VERIFICATION=true
```

### Auth コンポーネントで使用

```typescript
const isDevMode = process.env.NEXT_PUBLIC_SKIP_EMAIL_VERIFICATION === 'true'

if (isDevMode) {
  // メール確認をスキップする処理
  console.log('開発環境: メール確認スキップ')
}
```

---

## 📋 推奨設定

### 開発環境
- ✅ メール確認: **無効**
- ✅ 即座にログイン可能
- ✅ テストが簡単

### 本番環境
- ✅ メール確認: **有効**
- ✅ SMTP設定を追加
- ✅ セキュリティ強化

---

## 🔧 完全な設定手順（初回のみ）

### 1. Supabaseでメール確認を無効化

```
Dashboard → Settings → Auth → Enable email confirmations → OFF
```

### 2. 新規登録

```
http://localhost:3000 → 新規登録 → 即ログイン！
```

### 3. 管理者に設定

```powershell
.\.venv\Scripts\python.exe set_admin.py yuki20001105@icloud.com
```

### 4. ブラウザをリロード

```
Ctrl+R → /home で「👑 管理者ダッシュボード」が表示
```

---

## ✅ 確認方法

### ユーザーが確認済みか確認

```sql
SELECT 
  email, 
  email_confirmed_at,
  created_at
FROM auth.users
ORDER BY created_at DESC;
```

### Pythonスクリプトで確認

```powershell
.\.venv\Scripts\python.exe set_admin.py --list
```

---

## 🚨 トラブルシューティング

### Q: 「Email not confirmed」エラーが出る

→ Supabase Dashboardで「Enable email confirmations」が **OFF** になっているか確認

### Q: 設定を変更したのに反映されない

1. ブラウザのキャッシュをクリア
2. Supabase Dashboardで設定を再保存
3. 新しいシークレットウィンドウでテスト

### Q: 既存ユーザーがログインできない

```sql
-- 既存ユーザーを確認済みに
UPDATE auth.users
SET email_confirmed_at = NOW()
WHERE email = 'yuki20001105@icloud.com';
```

---

## 📚 参考リンク

- [Supabase Auth Documentation](https://supabase.com/docs/guides/auth)
- [Email Confirmation Settings](https://supabase.com/docs/guides/auth/auth-email)

---

これで、確認メールなしで即座にログインできるようになります！
