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
- 一般ユーザーには管理者モードの切り替え操作を表示しない
- Admin ロールでも、管理者ワークスペースは最初は非表示
- 現在ログイン中のアカウントのパスワード再入力後だけ `AdminWorkspace` をマウント
- サーバー側の `/api/admin/unlock` でもAdmin権限と直近のパスワード認証を確認

### 🎯 管理者専用UI

#### 管理者ワークスペースから移動する運用ページ

| ページ | パス | 機能 | 保護状態 |
|--------|------|------|----------|
| **データ収集** | `/data-collection` | ネットケイバからレースデータをスクレイピング | 管理者モードの画面ガードとAPI認可を併用 |
| **モデル学習** | `/train` | MLモデルをトレーニング | 管理者モードの画面ガードとAPI認可を併用 |
| **本番前チェック** | `/production-readiness` | build・health・smoke・feature flagを確認 | 管理者モードの画面ガードとAPI認可を併用 |

#### ホーム画面での表示制御

`/home` ページでは、一般ユーザー向け画面と管理者ワークスペースを同じシェル内で切り替えます。

```tsx
{isAdmin && <button>管理者モード</button>}
{adminMode && isAdmin
  ? <AdminWorkspace />
  : <UserHome />}
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

#### ステップ3: `/home` で管理者モードを確認

1. Admin ロールを設定したアカウントでログイン
2. `/home` のヘッダーで「管理者モード」をクリック
3. 現在ログイン中のアカウントのパスワードを再入力
4. 同じ `/home` 内に管理者ワークスペースが表示されることを確認

管理者向けAPIは、UIの表示制御とは別にサーバー側でもAdmin権限を検証します。
上記3画面は共通の`AdminModeRouteGuard`で保護し、有効な短期grantがなければ内容をマウントせず`/home`へ戻します。

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

### 🏗️ 管理者ワークスペース（`/home` に統合済み）

管理者と一般ユーザーは共通の `/home` を使用します。一般ユーザーには管理者用の切り替え操作と内容を表示しません。Admin ロールのユーザーは、ヘッダーの「管理者モード」を選び、現在ログイン中のアカウントのパスワードを再入力すると、同じ `/home` 内で管理者ワークスペースへ切り替わります。

管理者モードは署名付きHttpOnly cookieで15分だけ同一ユーザー・同一ログインセッションに束縛します。再読み込み後も残り時間だけ復元され、ログアウト、「ユーザー画面に戻る」、権限喪失、認可エラー、または有効期限の到来で解除されます。パスワード自体をブラウザStorageへ保存しません。旧 `/admin` URL は独立した管理画面を表示せず、管理モードを解除して `/home` へリダイレクトします。

**機能**:
- データ統計の表示
- ユーザー一覧と権限管理
- データ収集への導線
- モデル管理への導線
- 本番前チェックへの導線

**実装箇所**:
```tsx
// src/app/home/page.tsx
// Admin ロールの表示判定、パスワード再確認、モード切り替えを担当

// src/components/AdminWorkspace.tsx
// 再確認に成功した場合だけ /home 内へ管理者機能をマウント

// src/app/api/admin/unlock/route.ts
// 現在の Admin 権限と直近のパスワード認証を確認し、短期cookieを発行・検証・破棄
```

### 🔒 セキュリティ

#### Row Level Security (RLS)

画面上の非表示に加え、ユーザー管理APIはAdminロールと有効な管理者モードcookieを両方検証します。`/train`と`/production-readiness`の画面はPremiumユーザーにも表示しません。一方、既存クライアントとの互換性のため、一部の運用APIは従来の`PremiumOrAdmin`認可を維持しており、これは管理者モード画面の表示権限とは別契約です。データベースをブラウザから直接参照する場合はRLSも適用します。

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
- [x] `/data-collection` を管理者モードの共通layoutで保護
- [x] `/train` と `/production-readiness` を管理者モードの共通layoutで保護
- [x] `/home` にパスワード再確認付きの管理者ワークスペースを統合
- [x] ユーザー管理機能（role変更）
- [ ] スクレイピング履歴テーブルとUI
- [ ] すべての管理者向けAPIでAdmin権限チェックが維持されていることを継続監査
- [ ] RLSポリシーで管理者データを保護

### 🎯 次のステップ

1. **環境確認**: データベースのroleカラムと管理者ユーザーを確認
2. **動作確認**: `/home` からパスワード再入力で管理者モードへ切り替える
3. **認可確認**: 管理者向けAPIとRLSのサーバー側保護を継続検証
4. **将来**: スクレイピング履歴など未実装機能を追加
