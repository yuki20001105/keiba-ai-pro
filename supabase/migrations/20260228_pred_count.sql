-- ============================================================
-- profiles テーブルへ予測回数制限カラムを追加
-- free: 10回/月, premium: -1（無制限）
-- ============================================================

ALTER TABLE public.profiles ADD COLUMN IF NOT EXISTS pred_count_remaining  INT         DEFAULT 10;
ALTER TABLE public.profiles ADD COLUMN IF NOT EXISTS pred_count_reset_at   TIMESTAMPTZ DEFAULT (date_trunc('month', NOW()) + INTERVAL '1 month');
ALTER TABLE public.profiles ADD COLUMN IF NOT EXISTS role                   TEXT        DEFAULT 'user';
ALTER TABLE public.profiles ADD COLUMN IF NOT EXISTS subscription_tier      TEXT        DEFAULT 'free';

-- ── 月次リセット確認関数 ──────────────────────────────────────────
CREATE OR REPLACE FUNCTION public.reset_pred_count_if_needed(p_user_id UUID)
RETURNS INT AS $$
DECLARE
  v_remaining  INT;
  v_reset_at   TIMESTAMPTZ;
  v_tier       TEXT;
BEGIN
  SELECT pred_count_remaining, pred_count_reset_at, subscription_tier
    INTO v_remaining, v_reset_at, v_tier
    FROM public.profiles WHERE id = p_user_id;

  IF NOT FOUND THEN RETURN 0; END IF;

  IF NOW() >= v_reset_at THEN
    v_remaining := CASE WHEN v_tier = 'premium' THEN -1 ELSE 10 END;
    UPDATE public.profiles
       SET pred_count_remaining = v_remaining,
           pred_count_reset_at  = date_trunc('month', NOW()) + INTERVAL '1 month'
     WHERE id = p_user_id;
  END IF;

  RETURN v_remaining;
END;
$$ LANGUAGE plpgsql SECURITY DEFINER;

-- ── 1回消費関数 ────────────────────────────────────────────────────
-- 戻り値: 消費後の残数 / -1=無制限(premium) / -999=残数不足
CREATE OR REPLACE FUNCTION public.consume_pred_count(p_user_id UUID)
RETURNS INT AS $$
DECLARE
  v_remaining INT;
BEGIN
  PERFORM public.reset_pred_count_if_needed(p_user_id);

  SELECT pred_count_remaining INTO v_remaining
    FROM public.profiles WHERE id = p_user_id;

  IF NOT FOUND   THEN RETURN -999; END IF;
  IF v_remaining = -1 THEN RETURN -1;   END IF;  -- premium
  IF v_remaining <= 0  THEN RETURN -999; END IF;  -- 残数不足

  UPDATE public.profiles
     SET pred_count_remaining = pred_count_remaining - 1
   WHERE id = p_user_id;

  RETURN v_remaining - 1;
END;
$$ LANGUAGE plpgsql SECURITY DEFINER;

-- 既存 premium ユーザーの残数を -1 に統一
UPDATE public.profiles SET pred_count_remaining = -1 WHERE subscription_tier = 'premium';
