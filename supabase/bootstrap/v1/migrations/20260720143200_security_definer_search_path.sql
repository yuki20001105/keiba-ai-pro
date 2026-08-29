-- Phase 3M fresh-project bootstrap: final fail-closed grants and function
-- search-path normalization after every domain and reconciled migration.

REVOKE ALL ON SCHEMA public FROM PUBLIC, anon, authenticated;
GRANT USAGE ON SCHEMA public TO anon, authenticated, service_role;

ALTER DEFAULT PRIVILEGES FOR ROLE postgres IN SCHEMA public
    REVOKE ALL ON TABLES FROM PUBLIC, anon, authenticated;
ALTER DEFAULT PRIVILEGES FOR ROLE postgres IN SCHEMA public
    REVOKE ALL ON SEQUENCES FROM PUBLIC, anon, authenticated;
ALTER DEFAULT PRIVILEGES FOR ROLE postgres IN SCHEMA public
    REVOKE EXECUTE ON FUNCTIONS FROM PUBLIC, anon, authenticated;

ALTER TABLE public.profiles ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.profiles FORCE ROW LEVEL SECURITY;
ALTER TABLE public.predictions ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.predictions FORCE ROW LEVEL SECURITY;
ALTER TABLE public.bets ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.bets FORCE ROW LEVEL SECURITY;
ALTER TABLE public.bank_records ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.bank_records FORCE ROW LEVEL SECURITY;
ALTER TABLE public.ocr_usage ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.ocr_usage FORCE ROW LEVEL SECURITY;
ALTER TABLE public.purchase_history ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.purchase_history FORCE ROW LEVEL SECURITY;

REVOKE ALL ON TABLE public.profiles FROM PUBLIC, anon, authenticated;
REVOKE ALL ON TABLE public.predictions FROM PUBLIC, anon, authenticated;
REVOKE ALL ON TABLE public.bets FROM PUBLIC, anon, authenticated;
REVOKE ALL ON TABLE public.bank_records FROM PUBLIC, anon, authenticated;
REVOKE ALL ON TABLE public.ocr_usage FROM PUBLIC, anon, authenticated;
REVOKE ALL ON TABLE public.purchase_history FROM PUBLIC, anon, authenticated;

GRANT SELECT (
    id, email, full_name, role, subscription_tier,
    ocr_monthly_limit, ocr_used_this_month, ocr_reset_date,
    pred_count_remaining, pred_count_reset_at,
    created_at, updated_at
) ON public.profiles TO authenticated;
GRANT UPDATE (full_name) ON public.profiles TO authenticated;
GRANT SELECT, INSERT, UPDATE, DELETE ON public.predictions TO authenticated;
GRANT SELECT, INSERT, UPDATE, DELETE ON public.bets TO authenticated;
GRANT SELECT, INSERT, UPDATE ON public.bank_records TO authenticated;
GRANT SELECT, INSERT ON public.ocr_usage TO authenticated;
GRANT SELECT, INSERT, UPDATE, DELETE ON public.purchase_history TO authenticated;

GRANT SELECT, INSERT, UPDATE, DELETE ON public.profiles TO service_role;
GRANT SELECT, INSERT, UPDATE, DELETE ON public.predictions TO service_role;
GRANT SELECT, INSERT, UPDATE, DELETE ON public.bets TO service_role;
GRANT SELECT, INSERT, UPDATE, DELETE ON public.bank_records TO service_role;
GRANT SELECT, INSERT, UPDATE, DELETE ON public.ocr_usage TO service_role;
GRANT SELECT, INSERT, UPDATE, DELETE ON public.purchase_history TO service_role;

REVOKE ALL ON FUNCTION public.phase3m_touch_updated_at()
    FROM PUBLIC, anon, authenticated;
REVOKE ALL ON FUNCTION public.phase3m_handle_new_user()
    FROM PUBLIC, anon, authenticated;
REVOKE ALL ON FUNCTION public.reset_pred_count_if_needed(UUID)
    FROM PUBLIC, anon, authenticated;
REVOKE ALL ON FUNCTION public.consume_pred_count_batch(UUID, INTEGER)
    FROM PUBLIC, anon, authenticated;
REVOKE ALL ON FUNCTION public.consume_pred_count(UUID)
    FROM PUBLIC, anon, authenticated;

GRANT EXECUTE ON FUNCTION public.reset_pred_count_if_needed(UUID) TO service_role;
GRANT EXECUTE ON FUNCTION public.consume_pred_count_batch(UUID, INTEGER) TO service_role;
GRANT EXECUTE ON FUNCTION public.consume_pred_count(UUID) TO service_role;

DO $phase3m_harden_security_definer$
DECLARE
    target RECORD;
BEGIN
    FOR target IN
        SELECT p.oid::REGPROCEDURE AS signature
        FROM pg_catalog.pg_proc AS p
        JOIN pg_catalog.pg_namespace AS n ON n.oid = p.pronamespace
        WHERE n.nspname = 'public' AND p.prosecdef
        ORDER BY p.oid::REGPROCEDURE::TEXT
    LOOP
        EXECUTE format(
            'ALTER FUNCTION %s SET search_path = pg_catalog, public, extensions',
            target.signature
        );
        EXECUTE format(
            'REVOKE EXECUTE ON FUNCTION %s FROM PUBLIC, anon, authenticated',
            target.signature
        );
    END LOOP;
END;
$phase3m_harden_security_definer$;
