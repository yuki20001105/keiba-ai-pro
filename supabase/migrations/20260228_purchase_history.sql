-- ============================================================
-- purchase_history テーブル（per-user 購入履歴）
-- tracking.db (SQLite) からの移行先
-- ============================================================

CREATE TABLE IF NOT EXISTS public.purchase_history (
  id              UUID DEFAULT gen_random_uuid() PRIMARY KEY,
  user_id         UUID REFERENCES public.profiles(id) ON DELETE CASCADE NOT NULL,
  race_id         TEXT NOT NULL,
  purchase_date   DATE,
  season          TEXT,                    -- 春/夏/秋/冬
  venue           TEXT,
  bet_type        TEXT NOT NULL,           -- 単勝/複勝/馬連/馬単/三連複/三連単 等
  combinations    TEXT,                    -- "1-2,1-3" カンマ区切り
  strategy_type   TEXT,
  purchase_count  INTEGER,
  unit_price      INTEGER,
  total_cost      INTEGER,
  expected_value  NUMERIC(10, 4),
  expected_return NUMERIC(10, 4),
  actual_return   INTEGER DEFAULT 0,
  is_hit          BOOLEAN DEFAULT false,
  recovery_rate   NUMERIC(10, 4) DEFAULT 0.0,
  created_at      TIMESTAMP WITH TIME ZONE DEFAULT NOW()
);

-- ── インデックス ─────────────────────────────────────────────────────
CREATE INDEX IF NOT EXISTS idx_purchase_history_user_id
  ON public.purchase_history(user_id);

CREATE INDEX IF NOT EXISTS idx_purchase_history_race_id
  ON public.purchase_history(race_id);

CREATE INDEX IF NOT EXISTS idx_purchase_history_created_at
  ON public.purchase_history(created_at DESC);

-- ── Row Level Security ───────────────────────────────────────────────
ALTER TABLE public.purchase_history ENABLE ROW LEVEL SECURITY;

CREATE POLICY "Users can view own purchase_history"
  ON public.purchase_history FOR SELECT
  USING (auth.uid() = user_id);

CREATE POLICY "Users can insert own purchase_history"
  ON public.purchase_history FOR INSERT
  WITH CHECK (auth.uid() = user_id);

CREATE POLICY "Users can update own purchase_history"
  ON public.purchase_history FOR UPDATE
  USING (auth.uid() = user_id);

CREATE POLICY "Users can delete own purchase_history"
  ON public.purchase_history FOR DELETE
  USING (auth.uid() = user_id);
