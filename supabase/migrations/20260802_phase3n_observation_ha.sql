-- Phase 3N: immutable model observations and shared HA maintenance jobs.
--
-- This migration is append-only. It does not alter the first nineteen
-- canonical migrations, enable a scheduler, or grant browser write access.
-- All timestamps representing receipt/recording are PostgreSQL server times.

CREATE SEQUENCE IF NOT EXISTS public.phase3n_ha_fencing_seq AS BIGINT;

CREATE TABLE IF NOT EXISTS public.phase3n_model_manifests (
    manifest_id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    idempotency_key TEXT NOT NULL UNIQUE
        CHECK (char_length(idempotency_key) BETWEEN 16 AND 200 AND idempotency_key !~ '[[:cntrl:]]'),
    model_id TEXT NOT NULL CHECK (model_id ~ '^[A-Za-z0-9][A-Za-z0-9_.:-]{0,127}$'),
    model_version TEXT NOT NULL CHECK (model_version ~ '^[A-Za-z0-9][A-Za-z0-9_.:-]{0,127}$'),
    model_artifact_sha256 TEXT NOT NULL CHECK (model_artifact_sha256 ~ '^[0-9a-f]{64}$'),
    candidate_commit_sha TEXT NOT NULL CHECK (candidate_commit_sha ~ '^[0-9a-f]{40}$'),
    feature_manifest_sha256 TEXT NOT NULL CHECK (feature_manifest_sha256 ~ '^[0-9a-f]{64}$'),
    model_feature_columns TEXT[] NOT NULL CHECK (cardinality(model_feature_columns) BETWEEN 1 AND 2048),
    training_data_ended_at TIMESTAMPTZ NOT NULL,
    expanding_window_checks_passed BOOLEAN NOT NULL CHECK (expanding_window_checks_passed),
    source_environment TEXT NOT NULL CHECK (source_environment IN ('staging', 'production')),
    payload_sha256 TEXT NOT NULL CHECK (payload_sha256 ~ '^[0-9a-f]{64}$'),
    registered_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp(),
    CONSTRAINT phase3n_model_manifest_identity UNIQUE (
        model_artifact_sha256, candidate_commit_sha, feature_manifest_sha256, source_environment
    )
);

CREATE TABLE IF NOT EXISTS public.phase3n_prediction_observations (
    observation_id UUID PRIMARY KEY,
    idempotency_key TEXT NOT NULL UNIQUE
        CHECK (char_length(idempotency_key) BETWEEN 16 AND 200 AND idempotency_key !~ '[[:cntrl:]]'),
    manifest_id UUID NOT NULL REFERENCES public.phase3n_model_manifests(manifest_id),
    race_id TEXT NOT NULL CHECK (char_length(race_id) BETWEEN 6 AND 64 AND race_id !~ '[[:cntrl:]]'),
    horse_id TEXT NOT NULL CHECK (char_length(horse_id) BETWEEN 1 AND 128 AND horse_id !~ '[[:cntrl:]]'),
    horse_number INTEGER NOT NULL CHECK (horse_number BETWEEN 1 AND 99),
    race_date DATE NOT NULL,
    prediction_at TIMESTAMPTZ NOT NULL,
    data_observed_at TIMESTAMPTZ NOT NULL,
    data_cutoff_at TIMESTAMPTZ NOT NULL,
    feature_values_sha256 TEXT NOT NULL CHECK (feature_values_sha256 ~ '^[0-9a-f]{64}$'),
    predicted_value DOUBLE PRECISION NOT NULL CHECK (
        predicted_value <> 'NaN'::DOUBLE PRECISION
        AND predicted_value NOT IN ('Infinity'::DOUBLE PRECISION, '-Infinity'::DOUBLE PRECISION)
    ),
    predicted_probability DOUBLE PRECISION NOT NULL
        CHECK (predicted_probability BETWEEN 0 AND 1),
    predicted_rank INTEGER NOT NULL CHECK (predicted_rank BETWEEN 1 AND 99),
    odds_at_prediction NUMERIC(12, 4) NULL CHECK (odds_at_prediction IS NULL OR odds_at_prediction > 0),
    recommendation TEXT NOT NULL CHECK (recommendation IN ('bet', 'pass', 'unavailable')),
    qualifying_bet BOOLEAN NOT NULL,
    wager_amount NUMERIC(14, 2) NOT NULL CHECK (
        wager_amount >= 0 AND wager_amount <> 'NaN'::NUMERIC AND wager_amount <> 'Infinity'::NUMERIC
    ),
    baseline_wager_amount NUMERIC(14, 2) NOT NULL CHECK (
        baseline_wager_amount >= 0 AND baseline_wager_amount <> 'NaN'::NUMERIC
        AND baseline_wager_amount <> 'Infinity'::NUMERIC
    ),
    latency_ms NUMERIC(12, 3) NOT NULL CHECK (
        latency_ms >= 0 AND latency_ms <> 'NaN'::NUMERIC AND latency_ms <> 'Infinity'::NUMERIC
    ),
    source_environment TEXT NOT NULL CHECK (source_environment IN ('staging', 'production')),
    leakage_violation_count INTEGER NOT NULL DEFAULT 0 CHECK (leakage_violation_count = 0),
    payload_sha256 TEXT NOT NULL CHECK (payload_sha256 ~ '^[0-9a-f]{64}$'),
    recorded_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp(),
    CONSTRAINT phase3n_prediction_observation_identity UNIQUE (
        manifest_id, race_id, horse_id, prediction_at
    ),
    CONSTRAINT phase3n_prediction_temporal_order CHECK (
        data_observed_at <= data_cutoff_at
        AND data_cutoff_at <= prediction_at
        AND prediction_at = recorded_at
    ),
    CONSTRAINT phase3n_prediction_bet_shape CHECK (
        (qualifying_bet AND recommendation = 'bet' AND wager_amount > 0)
        OR (NOT qualifying_bet AND recommendation <> 'bet' AND wager_amount = 0)
    )
);

CREATE TABLE IF NOT EXISTS public.phase3n_result_observation_events (
    result_event_id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    idempotency_key TEXT NOT NULL UNIQUE
        CHECK (char_length(idempotency_key) BETWEEN 16 AND 200 AND idempotency_key !~ '[[:cntrl:]]'),
    observation_id UUID NOT NULL UNIQUE REFERENCES public.phase3n_prediction_observations(observation_id),
    settled_at TIMESTAMPTZ NOT NULL,
    y_true INTEGER NOT NULL CHECK (y_true IN (0, 1)),
    finish_order INTEGER NULL CHECK (finish_order IS NULL OR finish_order BETWEEN 1 AND 99),
    bet_outcome TEXT NOT NULL CHECK (bet_outcome IN ('won', 'lost', 'void', 'not-bet')),
    return_amount NUMERIC(14, 2) NOT NULL CHECK (
        return_amount >= 0 AND return_amount <> 'NaN'::NUMERIC AND return_amount <> 'Infinity'::NUMERIC
    ),
    baseline_return_amount NUMERIC(14, 2) NOT NULL CHECK (
        baseline_return_amount >= 0 AND baseline_return_amount <> 'NaN'::NUMERIC
        AND baseline_return_amount <> 'Infinity'::NUMERIC
    ),
    payload_sha256 TEXT NOT NULL CHECK (payload_sha256 ~ '^[0-9a-f]{64}$'),
    recorded_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp()
);

CREATE TABLE IF NOT EXISTS public.phase3n_observation_ingest_attempts (
    attempt_id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    event_kind TEXT NOT NULL CHECK (event_kind IN ('model-manifest', 'prediction', 'result')),
    idempotency_key TEXT NOT NULL CHECK (char_length(idempotency_key) BETWEEN 16 AND 200),
    outcome TEXT NOT NULL CHECK (outcome IN ('inserted', 'duplicate', 'conflict', 'rejected')),
    payload_sha256 TEXT NOT NULL CHECK (payload_sha256 ~ '^[0-9a-f]{64}$'),
    failure_code TEXT NULL CHECK (
        failure_code IS NULL OR failure_code ~ '^[a-z0-9][a-z0-9-]{0,99}$'
    ),
    attempted_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp()
);

CREATE INDEX IF NOT EXISTS phase3n_prediction_observations_report_idx
    ON public.phase3n_prediction_observations(source_environment, race_date, prediction_at);
CREATE INDEX IF NOT EXISTS phase3n_result_observation_events_settled_idx
    ON public.phase3n_result_observation_events(settled_at);
CREATE INDEX IF NOT EXISTS phase3n_observation_attempts_report_idx
    ON public.phase3n_observation_ingest_attempts(attempted_at, event_kind, outcome);

CREATE TABLE IF NOT EXISTS public.phase3n_ha_jobs (
    job_id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    idempotency_key TEXT NOT NULL UNIQUE
        CHECK (char_length(idempotency_key) BETWEEN 16 AND 200 AND idempotency_key !~ '[[:cntrl:]]'),
    job_kind TEXT NOT NULL CHECK (job_kind IN ('observation-result-reconcile', 'cache-rebuild', 'ha-contract-test')),
    request_payload JSONB NOT NULL CHECK (jsonb_typeof(request_payload) = 'object'),
    request_sha256 TEXT NOT NULL CHECK (request_sha256 ~ '^[0-9a-f]{64}$'),
    state TEXT NOT NULL DEFAULT 'pending' CHECK (state IN ('pending', 'leased', 'completed', 'failed')),
    worker_owner TEXT NULL CHECK (worker_owner IS NULL OR char_length(worker_owner) BETWEEN 3 AND 128),
    fencing_token BIGINT NOT NULL DEFAULT 0 CHECK (fencing_token >= 0),
    lease_expires_at TIMESTAMPTZ NULL,
    effect_key TEXT NULL CHECK (effect_key IS NULL OR char_length(effect_key) BETWEEN 16 AND 200),
    effect_sha256 TEXT NULL CHECK (effect_sha256 IS NULL OR effect_sha256 ~ '^[0-9a-f]{64}$'),
    attempt_count INTEGER NOT NULL DEFAULT 0 CHECK (attempt_count BETWEEN 0 AND 20),
    created_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp(),
    completed_at TIMESTAMPTZ NULL,
    CONSTRAINT phase3n_ha_job_state_shape CHECK (
        (state = 'pending' AND worker_owner IS NULL AND lease_expires_at IS NULL
            AND effect_key IS NULL AND effect_sha256 IS NULL AND completed_at IS NULL)
        OR (state = 'leased' AND worker_owner IS NOT NULL AND lease_expires_at IS NOT NULL
            AND fencing_token >= 1 AND effect_key IS NULL AND effect_sha256 IS NULL AND completed_at IS NULL)
        OR (state = 'completed' AND worker_owner IS NULL AND lease_expires_at IS NULL
            AND effect_key IS NOT NULL AND effect_sha256 IS NOT NULL AND completed_at IS NOT NULL)
        OR (state = 'failed' AND worker_owner IS NULL AND lease_expires_at IS NULL
            AND effect_key IS NULL AND effect_sha256 IS NULL AND completed_at IS NOT NULL)
    )
);

CREATE TABLE IF NOT EXISTS public.phase3n_ha_effects (
    effect_id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    job_id UUID NOT NULL UNIQUE REFERENCES public.phase3n_ha_jobs(job_id),
    effect_key TEXT NOT NULL UNIQUE CHECK (char_length(effect_key) BETWEEN 16 AND 200),
    fencing_token BIGINT NOT NULL CHECK (fencing_token >= 1),
    effect_sha256 TEXT NOT NULL CHECK (effect_sha256 ~ '^[0-9a-f]{64}$'),
    applied_by TEXT NOT NULL CHECK (char_length(applied_by) BETWEEN 3 AND 128),
    applied_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp()
);

CREATE TABLE IF NOT EXISTS public.phase3n_ha_events (
    event_id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    job_id UUID NOT NULL REFERENCES public.phase3n_ha_jobs(job_id),
    event_type TEXT NOT NULL CHECK (
        event_type IN ('enqueued', 'duplicate-enqueue', 'claimed', 'reclaimed', 'heartbeat',
                       'effect-applied', 'stale-fence-rejected', 'duplicate-effect-rejected', 'failed')
    ),
    worker_owner TEXT NULL CHECK (worker_owner IS NULL OR char_length(worker_owner) BETWEEN 3 AND 128),
    fencing_token BIGINT NOT NULL CHECK (fencing_token >= 0),
    detail_code TEXT NOT NULL CHECK (detail_code ~ '^[a-z0-9][a-z0-9-]{0,99}$'),
    occurred_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp()
);

CREATE INDEX IF NOT EXISTS phase3n_ha_jobs_claim_idx
    ON public.phase3n_ha_jobs(state, lease_expires_at, created_at);
CREATE INDEX IF NOT EXISTS phase3n_ha_events_timeline_idx
    ON public.phase3n_ha_events(job_id, event_id);

CREATE OR REPLACE FUNCTION public._phase3n_reject_immutable_mutation()
RETURNS TRIGGER
LANGUAGE plpgsql
SET search_path = pg_catalog, public
AS $$
BEGIN
    RAISE EXCEPTION 'phase3n-append-only-mutation-forbidden' USING ERRCODE = '55000';
END;
$$;

DO $phase3n_immutable_triggers$
DECLARE
    v_table TEXT;
BEGIN
    FOREACH v_table IN ARRAY ARRAY[
        'phase3n_model_manifests',
        'phase3n_prediction_observations',
        'phase3n_result_observation_events',
        'phase3n_observation_ingest_attempts',
        'phase3n_ha_effects',
        'phase3n_ha_events'
    ] LOOP
        EXECUTE format('DROP TRIGGER IF EXISTS phase3n_reject_mutation ON public.%I', v_table);
        EXECUTE format(
            'CREATE TRIGGER phase3n_reject_mutation BEFORE UPDATE OR DELETE ON public.%I '
            'FOR EACH ROW EXECUTE FUNCTION public._phase3n_reject_immutable_mutation()',
            v_table
        );
    END LOOP;
END
$phase3n_immutable_triggers$;

CREATE OR REPLACE FUNCTION public.register_phase3n_model_manifest(p_manifest JSONB)
RETURNS TABLE(mutation_code TEXT, returned_manifest_id UUID, registered_at TIMESTAMPTZ)
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = pg_catalog, public
AS $$
DECLARE
    v_now TIMESTAMPTZ := clock_timestamp();
    v_id UUID;
    v_existing public.phase3n_model_manifests%ROWTYPE;
    v_columns TEXT[];
    v_payload_sha TEXT;
    v_idempotency TEXT;
BEGIN
    IF p_manifest IS NULL OR jsonb_typeof(p_manifest) <> 'object' THEN
        RAISE EXCEPTION 'phase3n-model-manifest-invalid' USING ERRCODE = '22023';
    END IF;
    v_idempotency := p_manifest->>'idempotency_key';
    v_payload_sha := p_manifest->>'payload_sha256';
    SELECT array_agg(value ORDER BY ordinality) INTO v_columns
    FROM jsonb_array_elements_text(p_manifest->'model_feature_columns') WITH ORDINALITY AS c(value, ordinality);
    IF v_idempotency IS NULL OR v_payload_sha !~ '^[0-9a-f]{64}$'
       OR v_columns IS NULL OR cardinality(v_columns) NOT BETWEEN 1 AND 2048
       OR (
           SELECT count(*) <> count(DISTINCT column_name)
           FROM unnest(v_columns) AS feature_columns(column_name)
       )
       OR EXISTS (
           SELECT 1 FROM unnest(v_columns) AS c(column_name)
           WHERE c.column_name = ANY (ARRAY[
               'time_seconds','finish_time','last_3f','last_3f_time','last_3f_rank',
               'last_3f_rank_normalized','corner_1','corner_2','corner_3','corner_4',
               'corner_positions','corner_positions_list','corner_position_avg',
               'corner_position_variance','last_corner_position','position_change','margin',
               'prize_money','finish','finish_position','actual_finish','win','place','return_tables'
           ])
       ) THEN
        RAISE EXCEPTION 'phase3n-model-manifest-invalid' USING ERRCODE = '22023';
    END IF;
    SELECT * INTO v_existing FROM public.phase3n_model_manifests
    WHERE idempotency_key = v_idempotency;
    IF FOUND THEN
        INSERT INTO public.phase3n_observation_ingest_attempts(
            event_kind, idempotency_key, outcome, payload_sha256, failure_code
        ) VALUES (
            'model-manifest', v_idempotency,
            CASE WHEN v_existing.payload_sha256 = v_payload_sha THEN 'duplicate' ELSE 'conflict' END,
            v_payload_sha,
            CASE WHEN v_existing.payload_sha256 = v_payload_sha THEN NULL ELSE 'payload-binding-conflict' END
        );
        RETURN QUERY SELECT
            CASE WHEN v_existing.payload_sha256 = v_payload_sha THEN 'duplicate' ELSE 'conflict' END,
            v_existing.manifest_id, v_existing.registered_at;
        RETURN;
    END IF;
    v_id := gen_random_uuid();
    INSERT INTO public.phase3n_model_manifests(
        manifest_id, idempotency_key, model_id, model_version, model_artifact_sha256,
        candidate_commit_sha, feature_manifest_sha256, model_feature_columns,
        training_data_ended_at, expanding_window_checks_passed, source_environment,
        payload_sha256, registered_at
    ) VALUES (
        v_id, v_idempotency, p_manifest->>'model_id', p_manifest->>'model_version',
        p_manifest->>'model_artifact_sha256', p_manifest->>'candidate_commit_sha',
        p_manifest->>'feature_manifest_sha256', v_columns,
        (p_manifest->>'training_data_ended_at')::TIMESTAMPTZ,
        (p_manifest->>'expanding_window_checks_passed')::BOOLEAN,
        p_manifest->>'source_environment', v_payload_sha, v_now
    );
    INSERT INTO public.phase3n_observation_ingest_attempts(
        event_kind, idempotency_key, outcome, payload_sha256
    ) VALUES ('model-manifest', v_idempotency, 'inserted', v_payload_sha);
    RETURN QUERY SELECT 'inserted', v_id, v_now;
END;
$$;

CREATE OR REPLACE FUNCTION public.record_phase3n_prediction_observation(p_observation JSONB)
RETURNS TABLE(mutation_code TEXT, returned_observation_id UUID, prediction_at TIMESTAMPTZ)
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = pg_catalog, public
AS $$
DECLARE
    v_now TIMESTAMPTZ := clock_timestamp();
    v_id UUID;
    v_existing public.phase3n_prediction_observations%ROWTYPE;
    v_payload_sha TEXT := p_observation->>'payload_sha256';
    v_idempotency TEXT := p_observation->>'idempotency_key';
    v_manifest public.phase3n_model_manifests%ROWTYPE;
    v_data_observed TIMESTAMPTZ;
    v_data_cutoff TIMESTAMPTZ;
BEGIN
    IF p_observation IS NULL OR jsonb_typeof(p_observation) <> 'object'
       OR v_payload_sha !~ '^[0-9a-f]{64}$' THEN
        RAISE EXCEPTION 'phase3n-prediction-observation-invalid' USING ERRCODE = '22023';
    END IF;
    SELECT * INTO v_existing FROM public.phase3n_prediction_observations
    WHERE idempotency_key = v_idempotency;
    IF FOUND THEN
        INSERT INTO public.phase3n_observation_ingest_attempts(
            event_kind, idempotency_key, outcome, payload_sha256, failure_code
        ) VALUES (
            'prediction', v_idempotency,
            CASE WHEN v_existing.payload_sha256 = v_payload_sha THEN 'duplicate' ELSE 'conflict' END,
            v_payload_sha,
            CASE WHEN v_existing.payload_sha256 = v_payload_sha THEN NULL ELSE 'payload-binding-conflict' END
        );
        RETURN QUERY SELECT
            CASE WHEN v_existing.payload_sha256 = v_payload_sha THEN 'duplicate' ELSE 'conflict' END,
            v_existing.observation_id, v_existing.prediction_at;
        RETURN;
    END IF;
    SELECT * INTO v_manifest FROM public.phase3n_model_manifests
    WHERE manifest_id = (p_observation->>'manifest_id')::UUID;
    IF NOT FOUND OR v_manifest.source_environment <> p_observation->>'source_environment' THEN
        RAISE EXCEPTION 'phase3n-model-manifest-binding-invalid' USING ERRCODE = '23503';
    END IF;
    v_data_observed := (p_observation->>'data_observed_at')::TIMESTAMPTZ;
    v_data_cutoff := (p_observation->>'data_cutoff_at')::TIMESTAMPTZ;
    IF v_data_observed > v_data_cutoff OR v_data_cutoff > v_now
       OR v_manifest.training_data_ended_at >= v_now
       OR COALESCE((p_observation->>'leakage_violation_count')::INTEGER, -1) <> 0 THEN
        INSERT INTO public.phase3n_observation_ingest_attempts(
            event_kind, idempotency_key, outcome, payload_sha256, failure_code
        ) VALUES ('prediction', v_idempotency, 'rejected', v_payload_sha, 'temporal-or-leakage-invalid');
        RETURN QUERY SELECT 'rejected', NULL::UUID, v_now;
        RETURN;
    END IF;
    v_id := (p_observation->>'observation_id')::UUID;
    INSERT INTO public.phase3n_prediction_observations(
        observation_id, idempotency_key, manifest_id, race_id, horse_id, horse_number,
        race_date, prediction_at, data_observed_at, data_cutoff_at, feature_values_sha256,
        predicted_value, predicted_probability, predicted_rank, odds_at_prediction,
        recommendation, qualifying_bet, wager_amount, baseline_wager_amount, latency_ms,
        source_environment, leakage_violation_count, payload_sha256, recorded_at
    ) VALUES (
        v_id, v_idempotency, v_manifest.manifest_id, p_observation->>'race_id',
        p_observation->>'horse_id', (p_observation->>'horse_number')::INTEGER,
        (p_observation->>'race_date')::DATE, v_now, v_data_observed, v_data_cutoff,
        p_observation->>'feature_values_sha256', (p_observation->>'predicted_value')::DOUBLE PRECISION,
        (p_observation->>'predicted_probability')::DOUBLE PRECISION,
        (p_observation->>'predicted_rank')::INTEGER,
        NULLIF(p_observation->>'odds_at_prediction', '')::NUMERIC,
        p_observation->>'recommendation', (p_observation->>'qualifying_bet')::BOOLEAN,
        (p_observation->>'wager_amount')::NUMERIC,
        (p_observation->>'baseline_wager_amount')::NUMERIC,
        (p_observation->>'latency_ms')::NUMERIC, p_observation->>'source_environment',
        (p_observation->>'leakage_violation_count')::INTEGER, v_payload_sha, v_now
    );
    INSERT INTO public.phase3n_observation_ingest_attempts(
        event_kind, idempotency_key, outcome, payload_sha256
    ) VALUES ('prediction', v_idempotency, 'inserted', v_payload_sha);
    RETURN QUERY SELECT 'inserted', v_id, v_now;
END;
$$;

CREATE OR REPLACE FUNCTION public.record_phase3n_result_observation(p_result JSONB)
RETURNS TABLE(mutation_code TEXT, returned_result_event_id UUID, recorded_at TIMESTAMPTZ)
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = pg_catalog, public
AS $$
DECLARE
    v_now TIMESTAMPTZ := clock_timestamp();
    v_existing public.phase3n_result_observation_events%ROWTYPE;
    v_prediction public.phase3n_prediction_observations%ROWTYPE;
    v_payload_sha TEXT := p_result->>'payload_sha256';
    v_idempotency TEXT := p_result->>'idempotency_key';
    v_id UUID;
BEGIN
    IF p_result IS NULL OR jsonb_typeof(p_result) <> 'object'
       OR v_payload_sha !~ '^[0-9a-f]{64}$' THEN
        RAISE EXCEPTION 'phase3n-result-observation-invalid' USING ERRCODE = '22023';
    END IF;
    SELECT * INTO v_existing FROM public.phase3n_result_observation_events
    WHERE idempotency_key = v_idempotency;
    IF FOUND THEN
        INSERT INTO public.phase3n_observation_ingest_attempts(
            event_kind, idempotency_key, outcome, payload_sha256, failure_code
        ) VALUES (
            'result', v_idempotency,
            CASE WHEN v_existing.payload_sha256 = v_payload_sha THEN 'duplicate' ELSE 'conflict' END,
            v_payload_sha,
            CASE WHEN v_existing.payload_sha256 = v_payload_sha THEN NULL ELSE 'payload-binding-conflict' END
        );
        RETURN QUERY SELECT
            CASE WHEN v_existing.payload_sha256 = v_payload_sha THEN 'duplicate' ELSE 'conflict' END,
            v_existing.result_event_id, v_existing.recorded_at;
        RETURN;
    END IF;
    SELECT * INTO v_prediction FROM public.phase3n_prediction_observations
    WHERE observation_id = (p_result->>'observation_id')::UUID;
    IF NOT FOUND OR (p_result->>'settled_at')::TIMESTAMPTZ < v_prediction.prediction_at
       OR (p_result->>'settled_at')::TIMESTAMPTZ > v_now THEN
        INSERT INTO public.phase3n_observation_ingest_attempts(
            event_kind, idempotency_key, outcome, payload_sha256, failure_code
        ) VALUES ('result', v_idempotency, 'rejected', v_payload_sha, 'settlement-time-invalid');
        RETURN QUERY SELECT 'rejected', NULL::UUID, v_now;
        RETURN;
    END IF;
    INSERT INTO public.phase3n_result_observation_events(
        idempotency_key, observation_id, settled_at, y_true, finish_order, bet_outcome,
        return_amount, baseline_return_amount, payload_sha256, recorded_at
    ) VALUES (
        v_idempotency, v_prediction.observation_id, (p_result->>'settled_at')::TIMESTAMPTZ,
        (p_result->>'y_true')::INTEGER, NULLIF(p_result->>'finish_order', '')::INTEGER,
        p_result->>'bet_outcome', (p_result->>'return_amount')::NUMERIC,
        (p_result->>'baseline_return_amount')::NUMERIC, v_payload_sha, v_now
    ) RETURNING result_event_id INTO v_id;
    INSERT INTO public.phase3n_observation_ingest_attempts(
        event_kind, idempotency_key, outcome, payload_sha256
    ) VALUES ('result', v_idempotency, 'inserted', v_payload_sha);
    RETURN QUERY SELECT 'inserted', v_id, v_now;
END;
$$;

CREATE OR REPLACE FUNCTION public.enqueue_phase3n_ha_job(
    p_idempotency_key TEXT, p_job_kind TEXT, p_request_payload JSONB, p_request_sha256 TEXT
)
RETURNS TABLE(mutation_code TEXT, returned_job_id UUID, fencing_token BIGINT, lease_expires_at TIMESTAMPTZ)
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = pg_catalog, public
AS $$
DECLARE
    v_job public.phase3n_ha_jobs%ROWTYPE;
BEGIN
    SELECT * INTO v_job FROM public.phase3n_ha_jobs WHERE idempotency_key = p_idempotency_key;
    IF FOUND THEN
        INSERT INTO public.phase3n_ha_events(job_id, event_type, fencing_token, detail_code)
        VALUES (v_job.job_id, 'duplicate-enqueue', v_job.fencing_token, 'idempotent-replay');
        RETURN QUERY SELECT
            CASE WHEN v_job.request_sha256 = p_request_sha256 AND v_job.job_kind = p_job_kind
                 THEN 'duplicate' ELSE 'conflict' END,
            v_job.job_id, v_job.fencing_token, v_job.lease_expires_at;
        RETURN;
    END IF;
    INSERT INTO public.phase3n_ha_jobs(
        idempotency_key, job_kind, request_payload, request_sha256
    ) VALUES (p_idempotency_key, p_job_kind, p_request_payload, p_request_sha256)
    RETURNING * INTO v_job;
    INSERT INTO public.phase3n_ha_events(job_id, event_type, fencing_token, detail_code)
    VALUES (v_job.job_id, 'enqueued', 0, 'new-job');
    RETURN QUERY SELECT 'inserted', v_job.job_id, 0::BIGINT, NULL::TIMESTAMPTZ;
END;
$$;

CREATE OR REPLACE FUNCTION public.claim_phase3n_ha_job(
    p_worker_owner TEXT, p_lease_seconds INTEGER, p_job_kind TEXT DEFAULT NULL
)
RETURNS TABLE(
    mutation_code TEXT, returned_job_id UUID, returned_job_kind TEXT,
    request_payload JSONB, worker_owner TEXT, fencing_token BIGINT,
    lease_expires_at TIMESTAMPTZ, attempt_count INTEGER
)
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = pg_catalog, public
AS $$
DECLARE
    v_now TIMESTAMPTZ := clock_timestamp();
    v_job public.phase3n_ha_jobs%ROWTYPE;
    v_reclaimed BOOLEAN;
BEGIN
    IF p_worker_owner IS NULL OR char_length(p_worker_owner) NOT BETWEEN 3 AND 128
       OR p_lease_seconds NOT BETWEEN 2 AND 300 THEN
        RAISE EXCEPTION 'phase3n-ha-claim-invalid' USING ERRCODE = '22023';
    END IF;
    SELECT * INTO v_job FROM public.phase3n_ha_jobs AS j
    WHERE (j.state = 'pending' OR (j.state = 'leased' AND j.lease_expires_at <= v_now))
      AND (p_job_kind IS NULL OR j.job_kind = p_job_kind)
      AND j.attempt_count < 20
    ORDER BY j.created_at, j.job_id
    FOR UPDATE SKIP LOCKED LIMIT 1;
    IF NOT FOUND THEN
        RETURN QUERY SELECT 'not-found', NULL::UUID, NULL::TEXT, NULL::JSONB,
            NULL::TEXT, NULL::BIGINT, NULL::TIMESTAMPTZ, NULL::INTEGER;
        RETURN;
    END IF;
    v_reclaimed := v_job.state = 'leased';
    UPDATE public.phase3n_ha_jobs AS j
    SET state = 'leased', worker_owner = p_worker_owner,
        fencing_token = nextval('public.phase3n_ha_fencing_seq'::regclass),
        lease_expires_at = v_now + make_interval(secs => p_lease_seconds),
        attempt_count = j.attempt_count + 1, updated_at = v_now
    WHERE j.job_id = v_job.job_id RETURNING * INTO v_job;
    INSERT INTO public.phase3n_ha_events(job_id, event_type, worker_owner, fencing_token, detail_code)
    VALUES (
        v_job.job_id, CASE WHEN v_reclaimed THEN 'reclaimed' ELSE 'claimed' END,
        v_job.worker_owner, v_job.fencing_token,
        CASE WHEN v_reclaimed THEN 'expired-lease-takeover' ELSE 'initial-lease' END
    );
    RETURN QUERY SELECT 'applied', v_job.job_id, v_job.job_kind, v_job.request_payload,
        v_job.worker_owner, v_job.fencing_token, v_job.lease_expires_at, v_job.attempt_count;
END;
$$;

CREATE OR REPLACE FUNCTION public.heartbeat_phase3n_ha_job(
    p_job_id UUID, p_worker_owner TEXT, p_fencing_token BIGINT, p_lease_seconds INTEGER
)
RETURNS TABLE(mutation_code TEXT, lease_expires_at TIMESTAMPTZ)
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = pg_catalog, public
AS $$
DECLARE
    v_now TIMESTAMPTZ := clock_timestamp();
    v_expiry TIMESTAMPTZ;
BEGIN
    UPDATE public.phase3n_ha_jobs AS j
    SET lease_expires_at = v_now + make_interval(secs => p_lease_seconds), updated_at = v_now
    WHERE j.job_id = p_job_id AND j.state = 'leased' AND j.worker_owner = p_worker_owner
      AND j.fencing_token = p_fencing_token AND j.lease_expires_at > v_now
    RETURNING j.lease_expires_at INTO v_expiry;
    IF v_expiry IS NULL THEN
        INSERT INTO public.phase3n_ha_events(job_id, event_type, worker_owner, fencing_token, detail_code)
        VALUES (p_job_id, 'stale-fence-rejected', p_worker_owner, p_fencing_token, 'heartbeat-rejected');
        RETURN QUERY SELECT 'conflict', NULL::TIMESTAMPTZ;
        RETURN;
    END IF;
    INSERT INTO public.phase3n_ha_events(job_id, event_type, worker_owner, fencing_token, detail_code)
    VALUES (p_job_id, 'heartbeat', p_worker_owner, p_fencing_token, 'lease-renewed');
    RETURN QUERY SELECT 'applied', v_expiry;
END;
$$;

CREATE OR REPLACE FUNCTION public.apply_phase3n_ha_effect(
    p_job_id UUID, p_worker_owner TEXT, p_fencing_token BIGINT,
    p_effect_key TEXT, p_effect_sha256 TEXT
)
RETURNS TABLE(mutation_code TEXT, returned_effect_id UUID, accepted_fencing_token BIGINT)
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = pg_catalog, public
AS $$
DECLARE
    v_now TIMESTAMPTZ := clock_timestamp();
    v_job public.phase3n_ha_jobs%ROWTYPE;
    v_effect public.phase3n_ha_effects%ROWTYPE;
BEGIN
    SELECT * INTO v_job FROM public.phase3n_ha_jobs WHERE job_id = p_job_id FOR UPDATE;
    IF NOT FOUND THEN
        RETURN QUERY SELECT 'not-found', NULL::UUID, NULL::BIGINT;
        RETURN;
    END IF;
    IF v_job.state <> 'leased' OR v_job.worker_owner <> p_worker_owner
       OR v_job.fencing_token <> p_fencing_token OR v_job.lease_expires_at <= v_now THEN
        INSERT INTO public.phase3n_ha_events(job_id, event_type, worker_owner, fencing_token, detail_code)
        VALUES (p_job_id, 'stale-fence-rejected', p_worker_owner, p_fencing_token, 'effect-rejected');
        RETURN QUERY SELECT 'conflict', NULL::UUID, v_job.fencing_token;
        RETURN;
    END IF;
    SELECT * INTO v_effect FROM public.phase3n_ha_effects
    WHERE job_id = p_job_id OR effect_key = p_effect_key;
    IF FOUND THEN
        INSERT INTO public.phase3n_ha_events(job_id, event_type, worker_owner, fencing_token, detail_code)
        VALUES (p_job_id, 'duplicate-effect-rejected', p_worker_owner, p_fencing_token, 'effect-idempotency-hit');
        RETURN QUERY SELECT 'duplicate', v_effect.effect_id, v_effect.fencing_token;
        RETURN;
    END IF;
    INSERT INTO public.phase3n_ha_effects(job_id, effect_key, fencing_token, effect_sha256, applied_by)
    VALUES (p_job_id, p_effect_key, p_fencing_token, p_effect_sha256, p_worker_owner)
    RETURNING * INTO v_effect;
    UPDATE public.phase3n_ha_jobs AS j
    SET state = 'completed', worker_owner = NULL, lease_expires_at = NULL,
        effect_key = p_effect_key, effect_sha256 = p_effect_sha256,
        completed_at = v_now, updated_at = v_now
    WHERE j.job_id = p_job_id;
    INSERT INTO public.phase3n_ha_events(job_id, event_type, worker_owner, fencing_token, detail_code)
    VALUES (p_job_id, 'effect-applied', p_worker_owner, p_fencing_token, 'effect-committed');
    RETURN QUERY SELECT 'applied', v_effect.effect_id, v_effect.fencing_token;
END;
$$;

ALTER TABLE public.phase3n_model_manifests ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.phase3n_prediction_observations ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.phase3n_result_observation_events ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.phase3n_observation_ingest_attempts ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.phase3n_ha_jobs ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.phase3n_ha_effects ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.phase3n_ha_events ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.phase3n_model_manifests FORCE ROW LEVEL SECURITY;
ALTER TABLE public.phase3n_prediction_observations FORCE ROW LEVEL SECURITY;
ALTER TABLE public.phase3n_result_observation_events FORCE ROW LEVEL SECURITY;
ALTER TABLE public.phase3n_observation_ingest_attempts FORCE ROW LEVEL SECURITY;
ALTER TABLE public.phase3n_ha_jobs FORCE ROW LEVEL SECURITY;
ALTER TABLE public.phase3n_ha_effects FORCE ROW LEVEL SECURITY;
ALTER TABLE public.phase3n_ha_events FORCE ROW LEVEL SECURITY;

REVOKE ALL ON TABLE public.phase3n_model_manifests, public.phase3n_prediction_observations,
    public.phase3n_result_observation_events, public.phase3n_observation_ingest_attempts,
    public.phase3n_ha_jobs, public.phase3n_ha_effects, public.phase3n_ha_events
    FROM PUBLIC, anon, authenticated, service_role;
REVOKE ALL ON SEQUENCE public.phase3n_ha_fencing_seq,
    public.phase3n_observation_ingest_attempts_attempt_id_seq,
    public.phase3n_ha_events_event_id_seq
    FROM PUBLIC, anon, authenticated, service_role;
GRANT SELECT ON TABLE public.phase3n_model_manifests, public.phase3n_prediction_observations,
    public.phase3n_result_observation_events, public.phase3n_observation_ingest_attempts,
    public.phase3n_ha_jobs, public.phase3n_ha_effects, public.phase3n_ha_events
    TO service_role;

REVOKE ALL ON FUNCTION public._phase3n_reject_immutable_mutation() FROM PUBLIC, anon, authenticated, service_role;
REVOKE ALL ON FUNCTION public.register_phase3n_model_manifest(JSONB) FROM PUBLIC, anon, authenticated;
REVOKE ALL ON FUNCTION public.record_phase3n_prediction_observation(JSONB) FROM PUBLIC, anon, authenticated;
REVOKE ALL ON FUNCTION public.record_phase3n_result_observation(JSONB) FROM PUBLIC, anon, authenticated;
REVOKE ALL ON FUNCTION public.enqueue_phase3n_ha_job(TEXT, TEXT, JSONB, TEXT) FROM PUBLIC, anon, authenticated;
REVOKE ALL ON FUNCTION public.claim_phase3n_ha_job(TEXT, INTEGER, TEXT) FROM PUBLIC, anon, authenticated;
REVOKE ALL ON FUNCTION public.heartbeat_phase3n_ha_job(UUID, TEXT, BIGINT, INTEGER) FROM PUBLIC, anon, authenticated;
REVOKE ALL ON FUNCTION public.apply_phase3n_ha_effect(UUID, TEXT, BIGINT, TEXT, TEXT) FROM PUBLIC, anon, authenticated;
GRANT EXECUTE ON FUNCTION public.register_phase3n_model_manifest(JSONB) TO service_role;
GRANT EXECUTE ON FUNCTION public.record_phase3n_prediction_observation(JSONB) TO service_role;
GRANT EXECUTE ON FUNCTION public.record_phase3n_result_observation(JSONB) TO service_role;
GRANT EXECUTE ON FUNCTION public.enqueue_phase3n_ha_job(TEXT, TEXT, JSONB, TEXT) TO service_role;
GRANT EXECUTE ON FUNCTION public.claim_phase3n_ha_job(TEXT, INTEGER, TEXT) TO service_role;
GRANT EXECUTE ON FUNCTION public.heartbeat_phase3n_ha_job(UUID, TEXT, BIGINT, INTEGER) TO service_role;
GRANT EXECUTE ON FUNCTION public.apply_phase3n_ha_effect(UUID, TEXT, BIGINT, TEXT, TEXT) TO service_role;
