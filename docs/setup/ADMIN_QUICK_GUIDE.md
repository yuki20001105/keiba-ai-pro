# 管理者設定クイックガイド

## 🎯 3つの簡単な方法

### ⚡ 方法1: VS Code Task（推奨）

1. **Ctrl+Shift+P** を押す
2. 「**Tasks: Run Task**」を入力して選択
3. 「**Set Admin (Prompt Email)**」を選択
4. 自分のメールアドレスを入力

完了！ブラウザをリロードして `/home` を確認してください。

---

### 💻 方法2: ターミナルコマンド

```powershell
# 管理者に設定
python set_admin.py your-email@example.com

# 全ユーザーを表示
python set_admin.py --list
```

---

### 🗄️ 方法3: Supabase SQL Editor

1. [Supabase Dashboard](https://supabase.com/dashboard/) を開く
2. SQL Editor → New Query
3. 以下を実行:

```sql
-- roleカラムを追加（初回のみ）
ALTER TABLE public.profiles 
ADD COLUMN IF NOT EXISTS role TEXT DEFAULT 'user' 
CHECK (role IN ('admin', 'user'));

-- 既存ユーザーをuserに設定
UPDATE public.profiles 
SET role = 'user' 
WHERE role IS NULL;

-- 自分を管理者に昇格
UPDATE public.profiles 
SET role = 'admin' 
WHERE email = 'your-email@example.com';

-- 確認
SELECT email, role FROM public.profiles;
```

---

## 📋 よくある質問

### Q: 管理者になったか確認する方法は？

```powershell
python set_admin.py --list
```

または、ブラウザで`/home`を開いて、データ取得、モデル作成、予測実行、成績確認、ユーザー管理の5機能が表示されるか確認します。Admin専用機能を開く際に現在のパスワードで本人確認します。

---

### Q: 管理者権限で何ができるの？

- **共通ホーム** (`/home`): Adminロールでは5機能を自動表示し、管理操作の入口で本人確認
- **📊 データ収集** (`/data-collection`): ネットケイバからスクレイピング
- **🧠 モデル作成** (`/train`): AIモデルの学習設定と作成
- **👥 ユーザー管理** (`/user-management`): 登録ユーザーをread-onlyで確認

---

### Q: 複数の管理者を設定できる？

はい、何度でも実行可能です:

```powershell
python set_admin.py admin1@example.com
python set_admin.py admin2@example.com
```

---

### Q: 管理者から一般ユーザーに戻す方法は？

Supabase SQL Editorで:

```sql
UPDATE public.profiles 
SET role = 'user' 
WHERE email = 'downgrade-user@example.com';
```

画面からのAdmin／Userロール変更は現在提供していません。

---

## 🚨 トラブルシューティング

### エラー: "ユーザーが見つかりません"

→ メールアドレスが正しいか確認。登録済みユーザーを表示:

```powershell
python set_admin.py --list
```

---

### エラー: "roleカラムが存在しません"

→ Supabaseでroleカラムを追加:

```sql
ALTER TABLE public.profiles 
ADD COLUMN role TEXT DEFAULT 'user' 
CHECK (role IN ('admin', 'user'));
```

---

### 管理者向け5機能が表示されない

1. ブラウザをハードリロード: **Ctrl+Shift+R**
2. ログアウト → 再ログイン
3. `profiles.role`が`admin`であることを確認
4. `profiles.role`の更新後に再ログイン

---

## 📚 関連ドキュメント

- [完全セットアップガイド](./ADMIN_SETUP.md)
- [Supabaseスキーマ](../../supabase/setup_admin.sql)
- [共通ホーム実装](../../src/app/home/page.tsx)
- [ユーザー管理実装](../../src/app/user-management/page.tsx)
