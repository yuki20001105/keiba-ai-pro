-- 管理者機能セットアップSQL
-- Supabase Dashboard → SQL Editor で実行してください

-- ==========================================
-- 1. profilesテーブルにroleカラムを追加
-- ==========================================

-- roleカラムを追加（admin または user）
ALTER TABLE public.profiles 
ADD COLUMN IF NOT EXISTS role TEXT DEFAULT 'user' CHECK (role IN ('admin', 'user'));

-- 既存ユーザーをuserに設定（NULL対策）
UPDATE public.profiles 
SET role = 'user' 
WHERE role IS NULL;

-- ==========================================
-- 2. 管理者ユーザーを設定
-- ==========================================

-- ⚠️ 自分のメールアドレスに変更してください
UPDATE public.profiles 
SET role = 'admin' 
WHERE email = 'your-admin-email@example.com';

-- 確認: 全ユーザーのroleを表示
SELECT id, email, role, created_at 
FROM public.profiles 
ORDER BY created_at DESC;

-- ==========================================
-- 3. スクレイピング履歴テーブル（オプション）
-- ==========================================

CREATE TABLE IF NOT EXISTS public.scraping_logs (
  id UUID DEFAULT uuid_generate_v4() PRIMARY KEY,
  user_id UUID REFERENCES public.profiles(id) ON DELETE CASCADE NOT NULL,
  target_date DATE NOT NULL,
  races_collected INTEGER DEFAULT 0,
  horses_collected INTEGER DEFAULT 0,
  status TEXT CHECK (status IN ('pending', 'running', 'completed', 'failed')),
  error_message TEXT,
  started_at TIMESTAMP WITH TIME ZONE DEFAULT NOW(),
  completed_at TIMESTAMP WITH TIME ZONE
);

-- RLS有効化
ALTER TABLE public.scraping_logs ENABLE ROW LEVEL SECURITY;

-- 管理者のみ閲覧可能
CREATE POLICY "Admins can view scraping logs"
  ON public.scraping_logs FOR SELECT
  USING (
    EXISTS (
      SELECT 1 FROM profiles
      WHERE profiles.id = auth.uid()
      AND profiles.role = 'admin'
    )
  );

-- 管理者のみ挿入可能
CREATE POLICY "Admins can insert scraping logs"
  ON public.scraping_logs FOR INSERT
  WITH CHECK (
    EXISTS (
      SELECT 1 FROM profiles
      WHERE profiles.id = auth.uid()
      AND profiles.role = 'admin'
    )
  );

-- ==========================================
-- 4. モデル学習履歴テーブル（オプション）
-- ==========================================

CREATE TABLE IF NOT EXISTS public.training_logs (
  id UUID DEFAULT uuid_generate_v4() PRIMARY KEY,
  user_id UUID REFERENCES public.profiles(id) ON DELETE CASCADE NOT NULL,
  model_type TEXT NOT NULL,
  model_id TEXT NOT NULL,
  ultimate_mode BOOLEAN DEFAULT false,
  auc NUMERIC(5,4),
  logloss NUMERIC(10,6),
  n_rows INTEGER,
  training_duration_seconds INTEGER,
  status TEXT CHECK (status IN ('pending', 'training', 'completed', 'failed')),
  error_message TEXT,
  started_at TIMESTAMP WITH TIME ZONE DEFAULT NOW(),
  completed_at TIMESTAMP WITH TIME ZONE
);

-- RLS有効化
ALTER TABLE public.training_logs ENABLE ROW LEVEL SECURITY;

-- 管理者のみ閲覧可能
CREATE POLICY "Admins can view training logs"
  ON public.training_logs FOR SELECT
  USING (
    EXISTS (
      SELECT 1 FROM profiles
      WHERE profiles.id = auth.uid()
      AND profiles.role = 'admin'
    )
  );

-- 管理者のみ挿入可能
CREATE POLICY "Admins can insert training logs"
  ON public.training_logs FOR INSERT
  WITH CHECK (
    EXISTS (
      SELECT 1 FROM profiles
      WHERE profiles.id = auth.uid()
      AND profiles.role = 'admin'
    )
  );

-- ==========================================
-- 5. 完了確認
-- ==========================================

SELECT 
  '✅ Setup Complete!' as status,
  (SELECT COUNT(*) FROM profiles WHERE role = 'admin') as admin_count,
  (SELECT COUNT(*) FROM profiles WHERE role = 'user') as user_count;
