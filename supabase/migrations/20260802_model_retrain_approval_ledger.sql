-- ============================================================
-- Model retrain approval ledger (declarative only).
-- Applying this migration is a separate, explicitly approved operation.
-- Approval never creates a job, writes a model artifact, or changes the
-- active-model pointer. Those capabilities require later, separate gates.
-- ============================================================

CREATE EXTENSION IF NOT EXISTS pgcrypto WITH SCHEMA extensions;

CREATE TABLE IF NOT EXISTS public.model_retrain_approval_requests (
    approval_id UUID PRIMARY KEY DEFAULT extensions.gen_random_uuid(),
    dry_run_id UUID NOT NULL UNIQUE,
    approved_by UUID NULL REFERENCES public.profiles(id) ON DELETE RESTRICT,
    approved_at TIMESTAMPTZ NULL,
    approval_status TEXT NOT NULL DEFAULT 'pending' CHECK (
        approval_status IN ('pending', 'approved', 'rejected', 'expired', 'invalidated')
    ),
    approval_comment TEXT NOT NULL DEFAULT '' CHECK (
        char_length(approval_comment) <= 500
        AND approval_comment !~ '[[:cntrl:]]'
    ),
    approved_payload_hash TEXT NOT NULL CHECK (approved_payload_hash ~ '^[0-9a-f]{64}$'),
    requested_by UUID NOT NULL REFERENCES public.profiles(id) ON DELETE RESTRICT,
    requested_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp(),
    expires_at TIMESTAMPTZ NOT NULL,
    invalidation_reason TEXT NULL CHECK (
        invalidation_reason IS NULL OR invalidation_reason IN (
            'payload-mismatch', 'hash-mismatch', 'expired', 'active-model-changed',
            'feature-contract-changed', 'code-version-changed',
            'manual-invalidation', 'other'
        )
    ),
    execution_policy TEXT NOT NULL CHECK (
        execution_policy IN ('read-only-preview', 'staging-train', 'sandbox-train')
    ),
    allowed_actions TEXT[] NOT NULL,
    dry_run_payload JSONB NOT NULL CHECK (
        jsonb_typeof(dry_run_payload) = 'object'
        AND octet_length(dry_run_payload::TEXT) <= 262144
    ),
    record_version INTEGER NOT NULL DEFAULT 1 CHECK (record_version >= 1),
    authoritative_record BOOLEAN NOT NULL DEFAULT TRUE CHECK (authoritative_record = TRUE),
    execution_enabled BOOLEAN NOT NULL DEFAULT FALSE CHECK (execution_enabled = FALSE),
    job_created BOOLEAN NOT NULL DEFAULT FALSE CHECK (job_created = FALSE),
    CHECK (expires_at > requested_at),
    CHECK (dry_run_payload->>'dry_run_id' = dry_run_id::TEXT),
    CHECK (dry_run_payload->>'created_by' = requested_by::TEXT),
    CHECK (dry_run_payload->>'state' = 'preview-ready'),
    CHECK (
        allowed_actions <@ ARRAY[
            'submit_approved_retrain', 'view_approval_status', 'view_job_status'
        ]::TEXT[]
        AND 'view_approval_status' = ANY (allowed_actions)
        AND cardinality(allowed_actions) =
            CASE WHEN 'submit_approved_retrain' = ANY (allowed_actions) THEN 1 ELSE 0 END
            + CASE WHEN 'view_approval_status' = ANY (allowed_actions) THEN 1 ELSE 0 END
            + CASE WHEN 'view_job_status' = ANY (allowed_actions) THEN 1 ELSE 0 END
    ),
    CHECK (
        (execution_policy = 'read-only-preview'
            AND NOT ('submit_approved_retrain' = ANY (allowed_actions))
            AND NOT ('view_job_status' = ANY (allowed_actions)))
        OR (execution_policy IN ('staging-train', 'sandbox-train')
            AND 'submit_approved_retrain' = ANY (allowed_actions)
            AND 'view_job_status' = ANY (allowed_actions))
    ),
    CHECK (
        (approval_status = 'pending'
            AND approved_by IS NULL AND approved_at IS NULL
            AND invalidation_reason IS NULL)
        OR (approval_status IN ('approved', 'rejected')
            AND approved_by IS NOT NULL AND approved_at IS NOT NULL
            AND approved_by <> requested_by AND invalidation_reason IS NULL)
        OR (approval_status = 'expired' AND invalidation_reason = 'expired')
        OR (approval_status = 'invalidated' AND invalidation_reason IS NOT NULL)
    )
);

CREATE INDEX IF NOT EXISTS idx_model_retrain_approval_requested
    ON public.model_retrain_approval_requests (requested_at DESC);
CREATE INDEX IF NOT EXISTS idx_model_retrain_approval_pending
    ON public.model_retrain_approval_requests (approval_status, expires_at)
    WHERE approval_status = 'pending';

CREATE TABLE IF NOT EXISTS public.model_retrain_approval_events (
    event_id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    approval_id UUID NOT NULL REFERENCES public.model_retrain_approval_requests(approval_id) ON DELETE RESTRICT,
    event_seq INTEGER NOT NULL CHECK (event_seq >= 1),
    event_type TEXT NOT NULL CHECK (
        event_type IN ('created', 'approved', 'rejected', 'invalidated', 'expired')
    ),
    actor_user_id UUID NULL REFERENCES public.profiles(id) ON DELETE RESTRICT,
    from_status TEXT NULL CHECK (
        from_status IS NULL OR from_status IN ('pending', 'approved', 'rejected', 'expired', 'invalidated')
    ),
    to_status TEXT NOT NULL CHECK (
        to_status IN ('pending', 'approved', 'rejected', 'expired', 'invalidated')
    ),
    record_version INTEGER NOT NULL CHECK (record_version >= 1),
    approved_payload_hash TEXT NOT NULL CHECK (approved_payload_hash ~ '^[0-9a-f]{64}$'),
    reason TEXT NULL CHECK (
        reason IS NULL OR (
            char_length(reason) BETWEEN 20 AND 500
            AND reason !~ '[[:cntrl:]]'
        )
    ),
    occurred_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp(),
    UNIQUE (approval_id, event_seq)
);

ALTER TABLE public.model_retrain_approval_requests ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.model_retrain_approval_events ENABLE ROW LEVEL SECURITY;

REVOKE ALL ON TABLE public.model_retrain_approval_requests
    FROM PUBLIC, anon, authenticated, service_role;
REVOKE ALL ON TABLE public.model_retrain_approval_events
    FROM PUBLIC, anon, authenticated, service_role;
REVOKE ALL ON SEQUENCE public.model_retrain_approval_events_event_id_seq
    FROM PUBLIC, anon, authenticated, service_role;
GRANT SELECT ON TABLE public.model_retrain_approval_requests TO service_role;
GRANT SELECT ON TABLE public.model_retrain_approval_events TO service_role;

CREATE OR REPLACE FUNCTION public._model_retrain_require_admin(p_actor_user_id UUID)
RETURNS VOID
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = public
AS $$
BEGIN
    IF p_actor_user_id IS NULL OR NOT EXISTS (
        SELECT 1 FROM public.profiles AS p
        WHERE p.id = p_actor_user_id
          AND lower(COALESCE(p.role, '')) = 'admin'
    ) THEN
        RAISE EXCEPTION 'admin role required' USING ERRCODE = '42501';
    END IF;
END;
$$;

CREATE OR REPLACE FUNCTION public._reject_model_retrain_approval_event_mutation()
RETURNS TRIGGER
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = public
AS $$
BEGIN
    RAISE EXCEPTION 'model retrain approval events are append-only' USING ERRCODE = '55000';
END;
$$;

CREATE OR REPLACE FUNCTION public._guard_model_retrain_approval_update()
RETURNS TRIGGER
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = public
AS $$
BEGIN
    IF ROW(
        NEW.approval_id, NEW.dry_run_id, NEW.approved_payload_hash,
        NEW.requested_by, NEW.requested_at, NEW.expires_at,
        NEW.execution_policy, NEW.allowed_actions, NEW.dry_run_payload
    ) IS DISTINCT FROM ROW(
        OLD.approval_id, OLD.dry_run_id, OLD.approved_payload_hash,
        OLD.requested_by, OLD.requested_at, OLD.expires_at,
        OLD.execution_policy, OLD.allowed_actions, OLD.dry_run_payload
    ) THEN
        RAISE EXCEPTION 'model retrain approval binding is immutable' USING ERRCODE = '55000';
    END IF;
    IF NEW.record_version <> OLD.record_version + 1
       OR NEW.authoritative_record IS DISTINCT FROM TRUE
       OR NEW.execution_enabled IS DISTINCT FROM FALSE
       OR NEW.job_created IS DISTINCT FROM FALSE THEN
        RAISE EXCEPTION 'model retrain approval update violates safety state' USING ERRCODE = '55000';
    END IF;
    IF NOT (
        (OLD.approval_status = 'pending'
            AND NEW.approval_status IN ('approved', 'rejected', 'expired', 'invalidated'))
        OR (OLD.approval_status = 'approved'
            AND NEW.approval_status IN ('expired', 'invalidated'))
    ) THEN
        RAISE EXCEPTION 'model retrain approval transition is invalid' USING ERRCODE = '55000';
    END IF;
    RETURN NEW;
END;
$$;

DROP TRIGGER IF EXISTS trg_model_retrain_approval_update_guard
    ON public.model_retrain_approval_requests;
CREATE TRIGGER trg_model_retrain_approval_update_guard
    BEFORE UPDATE ON public.model_retrain_approval_requests
    FOR EACH ROW EXECUTE FUNCTION public._guard_model_retrain_approval_update();

DROP TRIGGER IF EXISTS trg_model_retrain_approval_events_immutable
    ON public.model_retrain_approval_events;
CREATE TRIGGER trg_model_retrain_approval_events_immutable
    BEFORE UPDATE OR DELETE ON public.model_retrain_approval_events
    FOR EACH ROW EXECUTE FUNCTION public._reject_model_retrain_approval_event_mutation();

CREATE OR REPLACE FUNCTION public._expire_model_retrain_approval_if_needed(p_approval_id UUID)
RETURNS VOID
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = public
AS $$
DECLARE
    v_row public.model_retrain_approval_requests%ROWTYPE;
    v_version INTEGER;
BEGIN
    SELECT * INTO v_row
    FROM public.model_retrain_approval_requests AS a
    WHERE a.approval_id = p_approval_id
    FOR UPDATE;
    IF NOT FOUND THEN RETURN; END IF;

    IF v_row.approval_status IN ('pending', 'approved')
       AND v_row.expires_at <= clock_timestamp() THEN
        v_version := v_row.record_version + 1;
        UPDATE public.model_retrain_approval_requests AS a
        SET approval_status = 'expired',
            invalidation_reason = 'expired',
            approval_comment = CASE
                WHEN a.approval_comment = '' THEN 'Approval validity expired before any retrain job was created.'
                ELSE a.approval_comment
            END,
            record_version = v_version,
            execution_enabled = FALSE,
            job_created = FALSE
        WHERE a.approval_id = p_approval_id;

        INSERT INTO public.model_retrain_approval_events (
            approval_id, event_seq, event_type, actor_user_id, from_status,
            to_status, record_version, approved_payload_hash, reason
        ) VALUES (
            v_row.approval_id, v_version, 'expired', NULL, v_row.approval_status,
            'expired', v_version, v_row.approved_payload_hash,
            'Approval validity expired before any retrain job was created.'
        );
    END IF;
END;
$$;

CREATE OR REPLACE FUNCTION public.create_model_retrain_approval(
    p_actor_user_id UUID,
    p_dry_run_id UUID,
    p_approved_payload_hash TEXT,
    p_dry_run_payload JSONB,
    p_execution_policy TEXT,
    p_allowed_actions TEXT[]
)
RETURNS SETOF public.model_retrain_approval_requests
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = public
AS $$
DECLARE
    v_now TIMESTAMPTZ := clock_timestamp();
    v_existing public.model_retrain_approval_requests%ROWTYPE;
    v_approval_id UUID;
    v_generated_at TIMESTAMPTZ;
BEGIN
    PERFORM public._model_retrain_require_admin(p_actor_user_id);
    IF p_dry_run_id IS NULL
       OR p_approved_payload_hash IS NULL
       OR p_approved_payload_hash !~ '^[0-9a-f]{64}$'
       OR p_dry_run_payload IS NULL
       OR jsonb_typeof(p_dry_run_payload) <> 'object'
       OR octet_length(p_dry_run_payload::TEXT) > 262144
       OR p_dry_run_payload->>'dry_run_id' IS DISTINCT FROM p_dry_run_id::TEXT
       OR p_dry_run_payload->>'created_by' IS DISTINCT FROM p_actor_user_id::TEXT
       OR p_dry_run_payload->>'state' IS DISTINCT FROM 'preview-ready'
       OR p_execution_policy NOT IN ('read-only-preview', 'staging-train', 'sandbox-train')
       OR p_allowed_actions IS NULL
       OR cardinality(p_allowed_actions) < 1
       OR cardinality(p_allowed_actions) > 3
       OR NOT ('view_approval_status' = ANY (p_allowed_actions))
       OR NOT (p_allowed_actions <@ ARRAY[
            'submit_approved_retrain', 'view_approval_status', 'view_job_status'
       ]::TEXT[])
       OR cardinality(p_allowed_actions) <>
            CASE WHEN 'submit_approved_retrain' = ANY (p_allowed_actions) THEN 1 ELSE 0 END
            + CASE WHEN 'view_approval_status' = ANY (p_allowed_actions) THEN 1 ELSE 0 END
            + CASE WHEN 'view_job_status' = ANY (p_allowed_actions) THEN 1 ELSE 0 END
       OR (p_execution_policy = 'read-only-preview' AND (
            'submit_approved_retrain' = ANY (p_allowed_actions)
            OR 'view_job_status' = ANY (p_allowed_actions)
       ))
       OR (p_execution_policy IN ('staging-train', 'sandbox-train') AND NOT (
            'submit_approved_retrain' = ANY (p_allowed_actions)
            AND 'view_job_status' = ANY (p_allowed_actions)
       )) THEN
        RAISE EXCEPTION 'invalid model retrain approval request' USING ERRCODE = '22023';
    END IF;

    BEGIN
        v_generated_at := (p_dry_run_payload->>'generated_at')::TIMESTAMPTZ;
    EXCEPTION WHEN OTHERS THEN
        RAISE EXCEPTION 'invalid model retrain approval request' USING ERRCODE = '22023';
    END;
    IF v_generated_at < v_now - INTERVAL '24 hours'
       OR v_generated_at > v_now + INTERVAL '5 minutes' THEN
        RAISE EXCEPTION 'model retrain preview outside freshness window' USING ERRCODE = '22023';
    END IF;

    PERFORM pg_advisory_xact_lock(hashtextextended(p_dry_run_id::TEXT, 0));
    SELECT * INTO v_existing
    FROM public.model_retrain_approval_requests AS a
    WHERE a.dry_run_id = p_dry_run_id
    FOR UPDATE;

    IF FOUND THEN
        IF v_existing.requested_by IS DISTINCT FROM p_actor_user_id
           OR v_existing.approved_payload_hash IS DISTINCT FROM p_approved_payload_hash
           OR v_existing.dry_run_payload IS DISTINCT FROM p_dry_run_payload
           OR v_existing.execution_policy IS DISTINCT FROM p_execution_policy
           OR v_existing.allowed_actions IS DISTINCT FROM p_allowed_actions THEN
            RAISE EXCEPTION 'dry run approval payload conflict' USING ERRCODE = '23505';
        END IF;
        v_approval_id := v_existing.approval_id;
        PERFORM public._expire_model_retrain_approval_if_needed(v_approval_id);
    ELSE
        v_approval_id := extensions.gen_random_uuid();
        INSERT INTO public.model_retrain_approval_requests (
            approval_id, dry_run_id, approved_payload_hash, requested_by,
            requested_at, expires_at, execution_policy, allowed_actions,
            dry_run_payload, approval_status, record_version,
            authoritative_record, execution_enabled, job_created
        ) VALUES (
            v_approval_id, p_dry_run_id, p_approved_payload_hash, p_actor_user_id,
            v_now, v_now + INTERVAL '30 minutes', p_execution_policy,
            p_allowed_actions, p_dry_run_payload, 'pending', 1, TRUE, FALSE, FALSE
        );
        INSERT INTO public.model_retrain_approval_events (
            approval_id, event_seq, event_type, actor_user_id, from_status,
            to_status, record_version, approved_payload_hash, reason
        ) VALUES (
            v_approval_id, 1, 'created', p_actor_user_id, NULL,
            'pending', 1, p_approved_payload_hash, NULL
        );
    END IF;

    RETURN QUERY SELECT * FROM public.model_retrain_approval_requests AS a
        WHERE a.approval_id = v_approval_id;
END;
$$;

CREATE OR REPLACE FUNCTION public.get_model_retrain_approval(
    p_actor_user_id UUID,
    p_approval_id UUID
)
RETURNS SETOF public.model_retrain_approval_requests
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = public
AS $$
BEGIN
    PERFORM public._model_retrain_require_admin(p_actor_user_id);
    PERFORM public._expire_model_retrain_approval_if_needed(p_approval_id);
    RETURN QUERY SELECT * FROM public.model_retrain_approval_requests AS a
        WHERE a.approval_id = p_approval_id;
END;
$$;

CREATE OR REPLACE FUNCTION public.transition_model_retrain_approval(
    p_actor_user_id UUID,
    p_approval_id UUID,
    p_expected_version INTEGER,
    p_action TEXT,
    p_reason TEXT
)
RETURNS SETOF public.model_retrain_approval_requests
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = public
AS $$
DECLARE
    v_row public.model_retrain_approval_requests%ROWTYPE;
    v_reason TEXT;
    v_status TEXT;
    v_event TEXT;
    v_version INTEGER;
BEGIN
    PERFORM public._model_retrain_require_admin(p_actor_user_id);
    v_reason := regexp_replace(btrim(COALESCE(p_reason, '')), '[[:space:]]+', ' ', 'g');
    IF p_action NOT IN ('approve', 'reject', 'revoke')
       OR p_expected_version IS NULL OR p_expected_version < 1
       OR p_reason ~ '[[:cntrl:]]'
       OR char_length(v_reason) NOT BETWEEN 20 AND 500 THEN
        RAISE EXCEPTION 'invalid model retrain approval transition' USING ERRCODE = '22023';
    END IF;

    PERFORM public._expire_model_retrain_approval_if_needed(p_approval_id);
    SELECT * INTO v_row
    FROM public.model_retrain_approval_requests AS a
    WHERE a.approval_id = p_approval_id
    FOR UPDATE;
    IF NOT FOUND THEN
        RAISE EXCEPTION 'model retrain approval not found' USING ERRCODE = 'P0002';
    END IF;
    IF v_row.record_version <> p_expected_version THEN
        RAISE EXCEPTION 'model retrain approval version conflict' USING ERRCODE = '40001';
    END IF;
    IF v_row.approval_status = 'expired' THEN
        RAISE EXCEPTION 'model retrain approval expired' USING ERRCODE = '55000';
    END IF;

    IF p_action IN ('approve', 'reject') THEN
        IF v_row.approval_status <> 'pending' THEN
            RAISE EXCEPTION 'model retrain approval is not pending' USING ERRCODE = '55000';
        END IF;
        IF p_actor_user_id = v_row.requested_by THEN
            RAISE EXCEPTION 'requester cannot decide own approval' USING ERRCODE = '42501';
        END IF;
        v_status := CASE WHEN p_action = 'approve' THEN 'approved' ELSE 'rejected' END;
        v_event := v_status;
    ELSE
        IF p_actor_user_id <> v_row.requested_by THEN
            RAISE EXCEPTION 'only requester can revoke approval' USING ERRCODE = '42501';
        END IF;
        IF v_row.approval_status NOT IN ('pending', 'approved') THEN
            RAISE EXCEPTION 'model retrain approval cannot be revoked' USING ERRCODE = '55000';
        END IF;
        v_status := 'invalidated';
        v_event := 'invalidated';
    END IF;

    v_version := v_row.record_version + 1;
    UPDATE public.model_retrain_approval_requests AS a
    SET approval_status = v_status,
        approved_by = CASE WHEN p_action = 'revoke' THEN a.approved_by ELSE p_actor_user_id END,
        approved_at = CASE WHEN p_action = 'revoke' THEN a.approved_at ELSE clock_timestamp() END,
        approval_comment = v_reason,
        invalidation_reason = CASE WHEN p_action = 'revoke' THEN 'manual-invalidation' ELSE NULL END,
        record_version = v_version,
        authoritative_record = TRUE,
        execution_enabled = FALSE,
        job_created = FALSE
    WHERE a.approval_id = p_approval_id;

    INSERT INTO public.model_retrain_approval_events (
        approval_id, event_seq, event_type, actor_user_id, from_status,
        to_status, record_version, approved_payload_hash, reason
    ) VALUES (
        v_row.approval_id, v_version, v_event, p_actor_user_id,
        v_row.approval_status, v_status, v_version, v_row.approved_payload_hash, v_reason
    );

    RETURN QUERY SELECT * FROM public.model_retrain_approval_requests AS a
        WHERE a.approval_id = p_approval_id;
END;
$$;

REVOKE ALL ON FUNCTION public._model_retrain_require_admin(UUID)
    FROM PUBLIC, anon, authenticated, service_role;
REVOKE ALL ON FUNCTION public._reject_model_retrain_approval_event_mutation()
    FROM PUBLIC, anon, authenticated, service_role;
REVOKE ALL ON FUNCTION public._guard_model_retrain_approval_update()
    FROM PUBLIC, anon, authenticated, service_role;
REVOKE ALL ON FUNCTION public._expire_model_retrain_approval_if_needed(UUID)
    FROM PUBLIC, anon, authenticated, service_role;
REVOKE ALL ON FUNCTION public.create_model_retrain_approval(UUID, UUID, TEXT, JSONB, TEXT, TEXT[])
    FROM PUBLIC, anon, authenticated, service_role;
REVOKE ALL ON FUNCTION public.get_model_retrain_approval(UUID, UUID)
    FROM PUBLIC, anon, authenticated, service_role;
REVOKE ALL ON FUNCTION public.transition_model_retrain_approval(UUID, UUID, INTEGER, TEXT, TEXT)
    FROM PUBLIC, anon, authenticated, service_role;

GRANT EXECUTE ON FUNCTION public.create_model_retrain_approval(UUID, UUID, TEXT, JSONB, TEXT, TEXT[])
    TO service_role;
GRANT EXECUTE ON FUNCTION public.get_model_retrain_approval(UUID, UUID)
    TO service_role;
GRANT EXECUTE ON FUNCTION public.transition_model_retrain_approval(UUID, UUID, INTEGER, TEXT, TEXT)
    TO service_role;
