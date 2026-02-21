# 管理者設定ガイド

## 📋 現状の管理者機能

### 🔐 管理者権限の仕組み

#### 1. データベース構造（修正必要）

**現状のprofilesテーブル**:
```sql
CREATE TABLE public.profiles (
  id UUID REFERENCES auth.users ON DELETE CASCADE PRIMARY KEY,
  email TEXT UNIQUE NOT NULL,
  full_name TEXT,
  subscription_tier TEXT DEFAULT 'free',
  -- ⚠️ role カラムが存在しない
  ...
);
```

**必要な修正**:
```sql
-- roleカラムを追加
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

**useUserRole フック** (`src/hooks/useUserRole.ts`):
- Supabaseから `profiles.role` を取得
- `isAdmin = (role === 'admin')` で判定
- デフォルトは `'user'`

**AdminOnly コンポーネント** (`src/components/AdminOnly.tsx`):
- 管理者以外は `/home` にリダイレクト
- ページ全体を保護

### 🎯 管理者専用UI

#### 既存の管理者専用ページ

| ページ | パス | 機能 | 保護状態 |
|--------|------|------|----------|
| **データ収集** | `/data-collection` | ネットケイバからレースデータをスクレイピング | ⚠️ 保護必要 |
| **モデル学習** | `/train` | 5種類のMLモデルをトレーニング | ⚠️ 保護必要 |

#### ホーム画面での表示制御

`/home` ページ:
```tsx
{isAdmin && (
  <Link href="/data-collection">
    <div>📊 データ取得</div>
  </Link>
)}

{isAdmin && (
  <Link href="/train">
    <div>🧠 モデル学習</div>
  </Link>
)}
```

### 🚀 セットアップ手順

#### ステップ1: データベースにroleカラムを追加

1. Supabase Dashboard を開く
2. SQL Editor → New Query
3. 以下のSQLを実行:

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

#### ステップ3: 管理者専用ページを保護

`/data-collection/page.tsx` と `/train/page.tsx` を修正:

```tsx
import { AdminOnly } from '@/components/AdminOnly'

export default function DataCollectionPage() {
  return (
    <AdminOnly>
      {/* 既存のコンテンツ */}
    </AdminOnly>
  )
}
```

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

### 🏗️ 推奨: 管理者専用ダッシュボード

#### 新規作成: `/admin` ページ

**機能**:
- データ統計の表示
- ユーザー一覧と権限管理
- スクレイピング履歴
- モデルパフォーマンス比較
- システムログ

**実装例**:
```tsx
// src/app/admin/page.tsx
import { AdminOnly } from '@/components/AdminOnly'

export default function AdminDashboard() {
  return (
    <AdminOnly>
      <div className="container mx-auto p-6">
        <h1 className="text-3xl font-bold mb-6">管理者ダッシュボード</h1>
        
        {/* 統計カード */}
        <div className="grid grid-cols-4 gap-4 mb-8">
          <StatCard title="総レース数" value="1,234" />
          <StatCard title="総ユーザー数" value="56" />
          <StatCard title="学習済みモデル" value="5" />
          <StatCard title="API呼び出し" value="8,901" />
        </div>
        
        {/* クイックアクション */}
        <div className="grid grid-cols-2 gap-4">
          <Link href="/data-collection">
            <button>📊 データ収集</button>
          </Link>
          <Link href="/train">
            <button>🧠 モデル学習</button>
          </Link>
        </div>
        
        {/* ユーザー管理 */}
        <UserManagementTable />
        
        {/* スクレイピング履歴 */}
        <ScrapingHistory />
      </div>
    </AdminOnly>
  )
}
```

### 🔒 セキュリティ

#### Row Level Security (RLS)

現状、管理者機能はフロントエンドで制御されていますが、APIレベルでも保護が必要です。

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

### 📝 TODO: 実装が必要な項目

- [ ] Supabase schemaにroleカラムを追加
- [ ] `/data-collection/page.tsx` を `<AdminOnly>` で保護
- [ ] `/train/page.tsx` を `<AdminOnly>` で保護
- [ ] 管理者専用ダッシュボード `/admin` を作成
- [ ] ユーザー管理機能（role変更）
- [ ] スクレイピング履歴テーブルとUI
- [ ] API側でも管理者権限チェック
- [ ] RLSポリシーで管理者データを保護

### 🎯 次のステップ

1. **今すぐ実行**: データベースにroleカラムを追加
2. **優先度高**: 既存ページに `<AdminOnly>` を適用
3. **推奨**: 管理者ダッシュボードを作成
4. **将来**: ユーザー管理機能を実装
