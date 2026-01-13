-- ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
-- Row Level Security (RLS) ポリシー設定
-- ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

-- 0. 既存のポリシーを削除（エラー回避）
DROP POLICY IF EXISTS "Users can view own profile" ON users;
DROP POLICY IF EXISTS "Users can update own profile" ON users;
DROP POLICY IF EXISTS "Users can view own races" ON races;
DROP POLICY IF EXISTS "Users can insert own races" ON races;
DROP POLICY IF EXISTS "Users can update own races" ON races;
DROP POLICY IF EXISTS "Users can delete own races" ON races;
DROP POLICY IF EXISTS "Users can view own results" ON results;
DROP POLICY IF EXISTS "Users can insert own results" ON results;
DROP POLICY IF EXISTS "Users can update own results" ON results;
DROP POLICY IF EXISTS "Users can delete own results" ON results;
DROP POLICY IF EXISTS "Users can view own payouts" ON race_payouts;
DROP POLICY IF EXISTS "Users can insert own payouts" ON race_payouts;
DROP POLICY IF EXISTS "Users can update own payouts" ON race_payouts;
DROP POLICY IF EXISTS "Users can delete own payouts" ON race_payouts;
DROP POLICY IF EXISTS "Default user can view all races" ON races;
DROP POLICY IF EXISTS "Default user can view all results" ON results;
DROP POLICY IF EXISTS "Default user can view all payouts" ON race_payouts;

-- 1. RLS有効化
ALTER TABLE users ENABLE ROW LEVEL SECURITY;
ALTER TABLE races ENABLE ROW LEVEL SECURITY;
ALTER TABLE results ENABLE ROW LEVEL SECURITY;
ALTER TABLE race_payouts ENABLE ROW LEVEL SECURITY;

-- ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
-- 2. usersテーブルのポリシー
-- ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

-- ユーザーは自分のデータのみ閲覧可能
CREATE POLICY "Users can view own profile" ON users
  FOR SELECT
  USING (auth.uid() = id);

-- ユーザーは自分のデータのみ更新可能
CREATE POLICY "Users can update own profile" ON users
  FOR UPDATE
  USING (auth.uid() = id);

-- ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
-- 3. racesテーブルのポリシー
-- ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

-- ユーザーは自分のレースデータのみ閲覧可能
CREATE POLICY "Users can view own races" ON races
  FOR SELECT
  USING (auth.uid() = user_id);

-- ユーザーは自分のレースデータを挿入可能
CREATE POLICY "Users can insert own races" ON races
  FOR INSERT
  WITH CHECK (auth.uid() = user_id);

-- ユーザーは自分のレースデータのみ更新可能
CREATE POLICY "Users can update own races" ON races
  FOR UPDATE
  USING (auth.uid() = user_id);

-- ユーザーは自分のレースデータのみ削除可能
CREATE POLICY "Users can delete own races" ON races
  FOR DELETE
  USING (auth.uid() = user_id);

-- ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
-- 4. resultsテーブルのポリシー
-- ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

-- ユーザーは自分の結果データのみ閲覧可能
CREATE POLICY "Users can view own results" ON results
  FOR SELECT
  USING (auth.uid() = user_id);

-- ユーザーは自分の結果データを挿入可能
CREATE POLICY "Users can insert own results" ON results
  FOR INSERT
  WITH CHECK (auth.uid() = user_id);

-- ユーザーは自分の結果データのみ更新可能
CREATE POLICY "Users can update own results" ON results
  FOR UPDATE
  USING (auth.uid() = user_id);

-- ユーザーは自分の結果データのみ削除可能
CREATE POLICY "Users can delete own results" ON results
  FOR DELETE
  USING (auth.uid() = user_id);

-- ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
-- 5. race_payoutsテーブルのポリシー
-- ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

-- ユーザーは自分の払い戻しデータのみ閲覧可能
CREATE POLICY "Users can view own payouts" ON race_payouts
  FOR SELECT
  USING (auth.uid() = user_id);

-- ユーザーは自分の払い戻しデータを挿入可能
CREATE POLICY "Users can insert own payouts" ON race_payouts
  FOR INSERT
  WITH CHECK (auth.uid() = user_id);

-- ユーザーは自分の払い戻しデータのみ更新可能
CREATE POLICY "Users can update own payouts" ON race_payouts
  FOR UPDATE
  USING (auth.uid() = user_id);

-- ユーザーは自分の払い戻しデータのみ削除可能
CREATE POLICY "Users can delete own payouts" ON race_payouts
  FOR DELETE
  USING (auth.uid() = user_id);

-- ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
-- 6. 管理者権限（オプション）
-- ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

-- 特定のユーザーを管理者として設定する場合
-- デフォルトユーザー（00000000-0000-0000-0000-000000000000）は
-- すべてのデータにアクセス可能

-- デフォルトユーザーはすべてのレースを閲覧可能
CREATE POLICY "Default user can view all races" ON races
  FOR SELECT
  USING (user_id = '00000000-0000-0000-0000-000000000000');

-- デフォルトユーザーはすべての結果を閲覧可能
CREATE POLICY "Default user can view all results" ON results
  FOR SELECT
  USING (user_id = '00000000-0000-0000-0000-000000000000');

-- デフォルトユーザーはすべての払い戻しを閲覧可能
CREATE POLICY "Default user can view all payouts" ON race_payouts
  FOR SELECT
  USING (user_id = '00000000-0000-0000-0000-000000000000');

-- ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
-- 7. 確認クエリ
-- ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

-- RLS状態確認
SELECT 
  schemaname,
  tablename,
  rowsecurity
FROM pg_tables
WHERE schemaname = 'public'
ORDER BY tablename;

-- ポリシー一覧確認
SELECT 
  schemaname,
  tablename,
  policyname,
  permissive,
  roles,
  cmd,
  qual,
  with_check
FROM pg_policies
WHERE schemaname = 'public'
ORDER BY tablename, policyname;
