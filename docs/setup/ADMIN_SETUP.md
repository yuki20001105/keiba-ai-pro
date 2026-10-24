# 管理者設定ガイド

## 📋 現状の管理者機能

### 🔐 管理者権限の仕組み

#### 1. データベース構造

`profiles.role` には `admin` または `user` を設定します。環境にroleカラムがない場合だけ、次の例を参考に追加してください。

```sql
ALTER TABLE public.profiles 
ADD COLUMN role TEXT DEFAULT 'user' CHECK (role IN ('admin', 'user'));

-- 既存ユーザーのroleをuserに設定
UPDATE public.profiles SET role = 'user' WHERE role IS NULL;

-- 特定のユーザーを管理者に昇格
UPDATE public.profiles 
SET role = 'admin' 
WHERE email = 'your-admin-email@example.com';
```

#### 2. フロントエンドの管理者判定

**AuthContext** (`src/contexts/AuthContext.tsx`):
- 認証済みユーザーの `profiles.role` を取得
- `isAdmin = (role === 'admin')` で判定
- デフォルトは `'user'`

**統合ホーム** (`src/app/home/page.tsx`):
- 管理者／ユーザー画面の切り替え操作は表示しない
- Adminロールでは5機能を自動表示
- 一般ユーザーには「予測実行」と「成績確認」だけを表示
- 管理ページは`AdminActionRouteGuard`による本人確認とサーバーAPIのAdmin認可で保護

### 🎯 管理者専用UI

#### 共通ホームから移動する管理ページ

| ページ | パス | 機能 | 保護状態 |
|--------|------|------|----------|
| **データ取得** | `/data-collection` | ネットケイバからレースデータを取得 | 操作境界の本人確認とAPI認可を併用 |
| **モデル作成** | `/train` | MLモデルの学習設定と作成状態を確認 | 操作境界の本人確認とAPI認可を併用 |
| **ユーザー管理** | `/user-management` | 登録ユーザーをread-onlyで確認 | 操作境界の本人確認とAPI認可を併用 |

#### ホーム画面での表示制御

`/home` ページは全ユーザーで共通です。ログイン済みのロールから表示項目を自動判定し、画面モードは切り替えません。

```tsx
{FLOW_STEPS
  .filter(step => !step.adminOnly || isAdmin)
  .map(step => <FunctionLink key={step.href} step={step} />)}
```

### 🚀 セットアップ手順

#### ステップ1: データベースのroleカラムを確認

1. Supabase Dashboard を開く
2. SQL Editor → New Query
3. `profiles.role` が存在しない環境に限り、以下のSQLを実行:

```sql
-- roleカラムを追加
ALTER TABLE public.profiles 
ADD COLUMN role TEXT DEFAULT 'user' CHECK (role IN ('admin', 'user'));

-- 既存ユーザーをuserに設定
UPDATE public.profiles SET role = 'user' WHERE role IS NULL;
```

#### ステップ2: 管理者ユーザーを設定

```sql
-- 自分のメールアドレスを管理者に昇格
UPDATE public.profiles 
SET role = 'admin' 
WHERE email = 'your-email@example.com';

-- 確認
SELECT id, email, role FROM public.profiles;
```

#### ステップ3: `/home` で管理者向け機能を確認

1. Admin ロールを設定したアカウントでログイン
2. `/home` に5機能が自動表示されることを確認
3. データ取得、モデル作成、ユーザー管理のいずれかを開き、現在のパスワードで本人確認
4. 確認後に各管理機能を開けることを確認

管理者向けAPIは、UIの表示制御とは別にサーバー側でもAdmin権限を検証します。
上記3画面は共通の`AdminActionRouteGuard`で保護し、Adminロールでなければ内容をマウントせず`/home`へ戻します。有効な確認grantがなければ、目的の管理機能を表示する前に本人確認画面を表示します。

この本人確認はページ表示前のUI pre-checkです。ユーザー一覧APIは短期grantも検証しますが、その他の操作APIは既存のロール認可・環境フラグ・承認状態を正本とします。

### 📊 管理者専用機能の概要

#### 1. データ収集 (`/data-collection`)

**機能**:
- カレンダーで日付を選択
- ネットケイバから以下をスクレイピング:
  - レース情報 (race_id, レース名, 距離, 馬場状態)
  - 出走馬データ (馬名, 騎手, オッズ)
  - レース結果 (着順, 払戻金)
- Supabaseに自動保存

**使用API**:
- `POST /api/netkeiba/scrape`
- `POST /api/netkeiba/race-list`

**必要な理由**:
- データ収集は時間がかかる（数分～数十分）
- スクレイピング頻度を管理者が制御
- 一般ユーザーには負荷が高い

#### 2. モデル学習 (`/train`)

**機能**:
- 5種類のMLモデルから選択:
  1. Logistic Regression
  2. Random Forest
  3. Gradient Boosting
  4. LightGBM Standard
  5. LightGBM + Optuna (推奨)
- Ultimate版特徴量（90+）のON/OFF
- Optunaハイパーパラメータ最適化（100試行）
- AUC, LogLoss, 学習データ数の表示
- モデルを `data/models/` に保存

**使用API**:
- `POST http://localhost:8000/api/train`

**必要な理由**:
- 学習に5分～30分かかる
- 計算リソースが必要
- モデルは全ユーザーで共有
- 頻繁な再学習は不要

### 🏗️ 共通ホームとユーザー管理

管理者と一般ユーザーは共通の`/home`を使用します。画面モードの切り替えはありません。Adminロールには5機能、一般ユーザーには予測実行と成績確認を自動表示します。パスワード確認はAdmin専用機能を開く操作境界でのみ行います。

旧`/admin` URLは独立した管理画面を表示せず、`/home`へリダイレクトします。

**機能**:
- 01 データ取得
- 02 モデル作成
- 03 予測実行
- 04 成績確認
- 05 ユーザー管理（閲覧専用）

**実装箇所**:
```tsx
// src/app/home/page.tsx
// Admin ロールの表示判定と5機能の共通導線を担当

// src/app/user-management/page.tsx
// Admin限定のread-onlyユーザー一覧

// src/components/AdminActionRouteGuard.tsx
// Admin限定ページの本人確認とクライアント側ガード
```

### 🔒 セキュリティ

#### Row Level Security (RLS)

画面上の非表示に加え、ユーザー一覧APIはAdminロールと有効な短期grantをサーバー側で検証します。`/train`と`/user-management`はPremiumだけでは表示しません。一方、既存クライアントとの互換性のため、一部の運用APIは従来の`PremiumOrAdmin`認可を維持しており、これは管理画面の表示権限とは別契約です。データベースをブラウザから直接参照する場合はRLSも適用します。

**推奨: RLSポリシー**:
```sql
-- 管理者のみがスクレイピング履歴を閲覧
CREATE POLICY "Admins can view scraping logs"
  ON scraping_logs FOR SELECT
  USING (
    EXISTS (
      SELECT 1 FROM profiles
      WHERE profiles.id = auth.uid()
      AND profiles.role = 'admin'
    )
  );
```

### 📝 TODO: 継続確認が必要な項目

- [ ] Supabase schemaのroleカラムと管理者アカウント設定を環境ごとに確認
- [x] `/data-collection`、`/train`、`/user-management`を操作境界の共通layoutで保護
- [x] `/home` を5機能の共通導線へ統合
- [x] ユーザー管理をread-only表示へ限定
- [ ] スクレイピング履歴テーブルとUI
- [ ] すべての管理者向けAPIでAdmin権限チェックが維持されていることを継続監査
- [ ] RLSポリシーで管理者データを保護

### 🎯 次のステップ

1. **環境確認**: データベースのroleカラムと管理者ユーザーを確認
2. **動作確認**: Adminで`/home`の5機能と操作時本人確認、一般ユーザーで2機能が表示されることを確認
3. **認可確認**: 管理者向けAPIとRLSのサーバー側保護を継続検証
4. **将来**: スクレイピング履歴など未実装機能を追加
