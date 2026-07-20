-- Phase 3M fresh-project bootstrap: per-user purchase history.

CREATE TABLE public.purchase_history (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id UUID NOT NULL REFERENCES public.profiles(id) ON DELETE CASCADE,
    race_id TEXT NOT NULL,
    purchase_date DATE,
    season TEXT,
    venue TEXT,
    bet_type TEXT NOT NULL,
    combinations TEXT,
    strategy_type TEXT,
    purchase_count INTEGER CHECK (purchase_count IS NULL OR purchase_count >= 0),
    unit_price INTEGER CHECK (unit_price IS NULL OR unit_price >= 0),
    total_cost INTEGER CHECK (total_cost IS NULL OR total_cost >= 0),
    expected_value NUMERIC(12, 4),
    expected_return NUMERIC(12, 4),
    actual_return INTEGER NOT NULL DEFAULT 0 CHECK (actual_return >= 0),
    is_hit BOOLEAN NOT NULL DEFAULT FALSE,
    recovery_rate NUMERIC(12, 4) NOT NULL DEFAULT 0,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX purchase_history_user_created_idx
    ON public.purchase_history (user_id, created_at DESC);
CREATE INDEX purchase_history_race_idx
    ON public.purchase_history (race_id);

ALTER TABLE public.purchase_history ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.purchase_history FORCE ROW LEVEL SECURITY;

CREATE POLICY purchase_history_select_own
    ON public.purchase_history FOR SELECT TO authenticated
    USING ((SELECT auth.uid()) = user_id);
CREATE POLICY purchase_history_insert_own
    ON public.purchase_history FOR INSERT TO authenticated
    WITH CHECK ((SELECT auth.uid()) = user_id);
CREATE POLICY purchase_history_update_own
    ON public.purchase_history FOR UPDATE TO authenticated
    USING ((SELECT auth.uid()) = user_id)
    WITH CHECK ((SELECT auth.uid()) = user_id);
CREATE POLICY purchase_history_delete_own
    ON public.purchase_history FOR DELETE TO authenticated
    USING ((SELECT auth.uid()) = user_id);

REVOKE ALL ON TABLE public.purchase_history FROM PUBLIC, anon, authenticated;
GRANT SELECT, INSERT, UPDATE, DELETE ON public.purchase_history TO authenticated;
GRANT SELECT, INSERT, UPDATE, DELETE ON public.purchase_history TO service_role;
