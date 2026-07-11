-- ============================================================
-- Batch quota consumption (atomic)
-- 1 race analysis = 1 quota unit
--
-- TRUST BOUNDARY:
-- - p_user_id は FastAPI 側で検証済みユーザーIDを service_role 経由で渡す。
-- - browser/client から直接実行させないため execute 権限は service_role 限定。
-- ============================================================

CREATE OR REPLACE FUNCTION public.consume_pred_count_batch(
	p_user_id UUID,
	p_units   INT
)
RETURNS INT
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = public
AS $$
DECLARE
	v_remaining INT;
BEGIN
	IF p_units IS NULL OR p_units < 1 OR p_units > 100 THEN
		RAISE EXCEPTION 'p_units must be between 1 and 100';
	END IF;

	PERFORM public.reset_pred_count_if_needed(p_user_id);

	SELECT pred_count_remaining
		INTO v_remaining
		FROM public.profiles
	 WHERE id = p_user_id
	 FOR UPDATE;

	IF NOT FOUND THEN
		RETURN -999;
	END IF;

	IF v_remaining = -1 THEN
		RETURN -1;
	END IF;

	IF v_remaining < p_units THEN
		RETURN -999;
	END IF;

	UPDATE public.profiles
		 SET pred_count_remaining = pred_count_remaining - p_units
	 WHERE id = p_user_id
	RETURNING pred_count_remaining INTO v_remaining;

	RETURN v_remaining;
END;
$$;

REVOKE ALL ON FUNCTION public.consume_pred_count(UUID) FROM PUBLIC;
REVOKE ALL ON FUNCTION public.consume_pred_count(UUID) FROM anon;
REVOKE ALL ON FUNCTION public.consume_pred_count(UUID) FROM authenticated;
GRANT EXECUTE ON FUNCTION public.consume_pred_count(UUID) TO service_role;

REVOKE ALL ON FUNCTION public.consume_pred_count_batch(UUID, INT) FROM PUBLIC;
REVOKE ALL ON FUNCTION public.consume_pred_count_batch(UUID, INT) FROM anon;
REVOKE ALL ON FUNCTION public.consume_pred_count_batch(UUID, INT) FROM authenticated;
GRANT EXECUTE ON FUNCTION public.consume_pred_count_batch(UUID, INT) TO service_role;
