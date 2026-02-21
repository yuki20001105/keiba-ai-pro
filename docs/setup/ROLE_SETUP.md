# ユーザーロール設定手順

## 概要
このアプリには2種類のユーザーロールがあります:
- **admin**: 開発者（データ収集・モデル作成可能）
- **user**: 一般ユーザー（閲覧・予測のみ）

## 必要な設定

### 1. Supabaseデータベースの更新

Supabaseの管理画面 (https://supabase.com) にアクセスし、以下のSQLを実行してください:

```sql
-- profiles テーブルに role カラムを追加
ALTER TABLE profiles ADD COLUMN role TEXT DEFAULT 'user' CHECK (role IN ('admin', 'user'));

-- 開発者ユーザーに admin ロールを付与
-- ⚠️ あなたのメールアドレスに置き換えてください
UPDATE profiles SET role = 'admin' WHERE email = 'your-email@example.com';

-- 確認: 全ユーザーのロールを表示
SELECT id, email, role, created_at FROM profiles;
```

### 2. ロールの確認

正しく設定されたか確認するため、以下のクエリを実行:

```sql
SELECT email, role FROM profiles WHERE role = 'admin';
```

あなたのメールアドレスが表示されればOKです。

## 機能の制限

### 管理者のみアクセス可能
- `/data-collection` - データ収集ページ
- `/train` - モデル学習ページ

### 全ユーザーアクセス可能
- `/home` - ホーム画面
- `/dashboard` - ダッシュボード
- `/predict-batch` - 予測実行
- `/results` - 結果閲覧

## 動作確認

1. 管理者ユーザーでログイン
   - ホーム画面に「データ取得」と「モデル学習」のカードが表示される
   - これらのページにアクセスできる

2. 一般ユーザーでログイン
   - ホーム画面に「データ取得」と「モデル学習」のカードが表示されない
   - これらのページに直接アクセスしようとすると `/home` にリダイレクトされる

## トラブルシューティング

### Q: 管理者なのに管理者機能が表示されない
A: 以下を確認してください:
1. Supabaseで `UPDATE profiles SET role = 'admin'` を実行したか
2. ブラウザをリロード（Ctrl+R または Cmd+R）
3. キャッシュをクリアして再ログイン

### Q: 一般ユーザーを追加したい
A: 新しいユーザーは自動的に `role = 'user'` で作成されます。特別な設定は不要です。

### Q: 管理者を追加したい
A: Supabaseで以下を実行:
```sql
UPDATE profiles SET role = 'admin' WHERE email = '追加したいユーザーのメールアドレス';
```
