-- ============================================================
-- Service-only approved retrain execution bundle.
-- This function returns immutable execution inputs only. It does not read a
-- caller-controlled path, start training, write an artifact, or activate a
-- model. The worker must still own the live lease/fencing token.
-- ============================================================

CREATE OR REPLACE FUNCTION public.get_model_retrain_execution_bundle(
    p_worker_id TEXT,
    p_job_id UUID,
    p_expected_version INTEGER,
    p_fencing_token BIGINT,
    p_candidate_commit_sha TEXT,
    p_active_model_id TEXT
)
RETURNS JSONB
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = public, extensions
AS $$
DECLARE
    v_now TIMESTAMPTZ := clock_timestamp();
    v_job public.model_retrain_jobs%ROWTYPE;
    v_approval public.model_retrain_approval_requests%ROWTYPE;
    v_payload JSONB;
    v_item JSONB;
    v_selected TEXT[];
    v_removed TEXT[];
    v_train_start TEXT;
    v_train_end TEXT;
    v_validation_start TEXT;
    v_validation_end TEXT;
    v_contract_text TEXT;
    v_contract_sha256 TEXT;
BEGIN
    IF p_worker_id IS NULL
       OR p_worker_id !~ '^[a-z0-9][a-z0-9._:-]{2,79}$'
       OR p_job_id IS NULL
       OR p_expected_version IS NULL OR p_expected_version < 1
       OR p_fencing_token IS NULL OR p_fencing_token < 1
       OR p_candidate_commit_sha IS NULL
       OR p_candidate_commit_sha !~ '^[0-9a-f]{40}$'
       OR p_active_model_id IS NULL
       OR p_active_model_id !~ '^[A-Za-z0-9][A-Za-z0-9_.:-]{0,127}$' THEN
        RAISE EXCEPTION 'invalid model retrain execution bundle request'
            USING ERRCODE = '22023';
    END IF;

    SELECT * INTO v_job
    FROM public.model_retrain_jobs AS j
    WHERE j.job_id = p_job_id
    FOR UPDATE;
    IF NOT FOUND THEN
        RAISE EXCEPTION 'model retrain job not found' USING ERRCODE = 'P0002';
    END IF;
    IF v_job.record_version <> p_expected_version THEN
        RAISE EXCEPTION 'model retrain job version conflict' USING ERRCODE = '40001';
    END IF;
    IF v_job.job_state NOT IN ('claimed', 'running')
       OR v_job.worker_id IS DISTINCT FROM p_worker_id
       OR v_job.fencing_token IS DISTINCT FROM p_fencing_token
       OR v_job.lease_expires_at IS NULL
       OR v_job.lease_expires_at <= v_now THEN
        RAISE EXCEPTION 'stale model retrain worker cannot read execution bundle'
            USING ERRCODE = '42501';
    END IF;

    PERFORM public._expire_model_retrain_approval_if_needed(v_job.approval_id);
    SELECT * INTO v_approval
    FROM public.model_retrain_approval_requests AS a
    WHERE a.approval_id = v_job.approval_id
    FOR SHARE;
    IF NOT FOUND
       OR v_approval.approval_status <> 'approved'
       OR v_approval.expires_at <= v_now
       OR v_approval.job_created IS DISTINCT FROM TRUE
       OR v_approval.approved_payload_hash IS DISTINCT FROM v_job.approved_payload_hash
       OR v_approval.dry_run_id IS DISTINCT FROM v_job.dry_run_id
       OR v_approval.requested_by IS DISTINCT FROM v_job.requested_by
       OR v_approval.approved_by IS DISTINCT FROM v_job.approved_by
       OR v_approval.execution_policy IS DISTINCT FROM v_job.execution_policy
       OR v_approval.execution_policy NOT IN ('staging-train', 'sandbox-train')
       OR NOT ('submit_approved_retrain' = ANY (v_approval.allowed_actions)) THEN
        RAISE EXCEPTION 'model retrain approval no longer authorizes execution bundle'
            USING ERRCODE = '42501';
    END IF;

    v_payload := v_approval.dry_run_payload;
    IF v_payload IS NULL
       OR jsonb_typeof(v_payload) <> 'object'
       OR v_payload->>'state' IS DISTINCT FROM 'preview-ready'
       OR v_payload->>'dry_run_id' IS DISTINCT FROM v_job.dry_run_id::TEXT
       OR v_payload->>'created_by' IS DISTINCT FROM v_job.requested_by::TEXT
       OR v_payload->>'target' IS DISTINCT FROM 'win'
       OR v_payload->>'model_type' IS DISTINCT FROM 'lightgbm'
       OR v_payload->>'git_commit' IS DISTINCT FROM p_candidate_commit_sha
       OR v_payload->>'active_model_id' IS DISTINCT FROM p_active_model_id
       OR COALESCE(v_payload->>'data_snapshot_id', '') !~ '^[0-9a-f]{64}$'
       OR v_payload->>'data_snapshot_id' = repeat('0', 64)
       OR COALESCE(v_payload->>'feature_contract_hash', '') !~ '^[0-9a-f]{64}$'
       OR jsonb_typeof(v_payload->'train_period') <> 'object'
       OR jsonb_typeof(v_payload->'validation_period') <> 'object'
       OR jsonb_typeof(v_payload->'selected_features') <> 'array'
       OR jsonb_typeof(v_payload->'removed_features') <> 'array'
       OR jsonb_typeof(v_payload->'safety_checks') <> 'array' THEN
        RAISE EXCEPTION 'approved model retrain payload is not executable'
            USING ERRCODE = '55000';
    END IF;

    v_train_start := v_payload#>>'{train_period,start}';
    v_train_end := v_payload#>>'{train_period,end}';
    v_validation_start := v_payload#>>'{validation_period,start}';
    v_validation_end := v_payload#>>'{validation_period,end}';
    IF COALESCE(v_train_start, '') !~ '^[0-9]{8}$'
       OR COALESCE(v_train_end, '') !~ '^[0-9]{8}$'
       OR COALESCE(v_validation_start, '') !~ '^[0-9]{8}$'
       OR COALESCE(v_validation_end, '') !~ '^[0-9]{8}$' THEN
        RAISE EXCEPTION 'approved model retrain periods are invalid'
            USING ERRCODE = '55000';
    END IF;
    IF to_char(to_date(v_train_start, 'YYYYMMDD'), 'YYYYMMDD') <> v_train_start
       OR to_char(to_date(v_train_end, 'YYYYMMDD'), 'YYYYMMDD') <> v_train_end
       OR to_char(to_date(v_validation_start, 'YYYYMMDD'), 'YYYYMMDD') <> v_validation_start
       OR to_char(to_date(v_validation_end, 'YYYYMMDD'), 'YYYYMMDD') <> v_validation_end
       OR v_train_start > v_train_end
       OR v_train_end >= v_validation_start
       OR v_validation_start > v_validation_end THEN
        RAISE EXCEPTION 'approved model retrain periods are invalid'
            USING ERRCODE = '55000';
    END IF;

    IF jsonb_array_length(v_payload->'selected_features') NOT BETWEEN 1 AND 2048
       OR jsonb_array_length(v_payload->'removed_features') > 2048 THEN
        RAISE EXCEPTION 'approved model retrain feature count is invalid'
            USING ERRCODE = '55000';
    END IF;
    FOR v_item IN SELECT value FROM jsonb_array_elements(v_payload->'selected_features') LOOP
        IF jsonb_typeof(v_item) <> 'string'
           OR (v_item #>> '{}') !~ '^[A-Za-z0-9][A-Za-z0-9_.:-]{0,127}$' THEN
            RAISE EXCEPTION 'approved model retrain selected feature is invalid'
                USING ERRCODE = '55000';
        END IF;
    END LOOP;
    FOR v_item IN SELECT value FROM jsonb_array_elements(v_payload->'removed_features') LOOP
        IF jsonb_typeof(v_item) <> 'string'
           OR (v_item #>> '{}') !~ '^[A-Za-z0-9][A-Za-z0-9_.:-]{0,127}$' THEN
            RAISE EXCEPTION 'approved model retrain removed feature is invalid'
                USING ERRCODE = '55000';
        END IF;
    END LOOP;
    SELECT array_agg(value #>> '{}' ORDER BY ordinal)
    INTO v_selected
    FROM jsonb_array_elements(v_payload->'selected_features')
        WITH ORDINALITY AS selected(value, ordinal);
    SELECT COALESCE(array_agg(value #>> '{}' ORDER BY ordinal), ARRAY[]::TEXT[])
    INTO v_removed
    FROM jsonb_array_elements(v_payload->'removed_features')
        WITH ORDINALITY AS removed(value, ordinal);
    IF cardinality(v_selected) <> (
            SELECT count(DISTINCT feature) FROM unnest(v_selected) AS features(feature)
       )
       OR cardinality(v_removed) <> (
            SELECT count(DISTINCT feature) FROM unnest(v_removed) AS features(feature)
       )
       OR NOT (v_removed <@ v_selected)
       OR cardinality(v_selected) - cardinality(v_removed) < 1 THEN
        RAISE EXCEPTION 'approved model retrain feature binding is invalid'
            USING ERRCODE = '55000';
    END IF;
    IF COALESCE(v_payload->>'feature_count', '') !~ '^[0-9]{1,4}$' THEN
        RAISE EXCEPTION 'approved model retrain feature binding is invalid'
            USING ERRCODE = '55000';
    END IF;
    IF (v_payload->>'feature_count')::INTEGER
            <> cardinality(v_selected) - cardinality(v_removed) THEN
        RAISE EXCEPTION 'approved model retrain feature binding is invalid'
            USING ERRCODE = '55000';
    END IF;

    FOR v_item IN SELECT value FROM jsonb_array_elements(v_payload->'safety_checks') LOOP
        IF jsonb_typeof(v_item) <> 'object'
           OR COALESCE(v_item->>'key', '') !~ '^[A-Za-z0-9][A-Za-z0-9_.:-]{0,127}$'
           OR v_item->>'status' IS DISTINCT FROM 'pass' THEN
            RAISE EXCEPTION 'approved model retrain safety checks are not all passing'
                USING ERRCODE = '55000';
        END IF;
    END LOOP;
    IF NOT (v_payload->'safety_checks' @> '[{"key":"future_field_exclusion","status":"pass"}]'::JSONB)
       OR NOT (v_payload->'safety_checks' @> '[{"key":"out_of_time_split","status":"pass"}]'::JSONB)
       OR NOT (v_payload->'safety_checks' @> '[{"key":"active_model_immutable","status":"pass"}]'::JSONB)
       OR NOT (v_payload->'safety_checks' @> '[{"key":"production_write_blocked","status":"pass"}]'::JSONB)
       OR NOT (v_payload->'safety_checks' @> '[{"key":"path_input_rejected","status":"pass"}]'::JSONB)
       OR NOT (v_payload->'safety_checks' @> '[{"key":"data_snapshot_bound","status":"pass"}]'::JSONB)
       OR NOT (v_payload->'safety_checks' @> '[{"key":"candidate_commit_bound","status":"pass"}]'::JSONB) THEN
        RAISE EXCEPTION 'approved model retrain required safety check is missing'
            USING ERRCODE = '55000';
    END IF;

    v_contract_text := '{"model_type":' || to_jsonb(v_payload->>'model_type')::TEXT
        || ',"removed_features":[' || COALESCE((
            SELECT string_agg(to_jsonb(feature)::TEXT, ',' ORDER BY feature)
            FROM unnest(v_removed) AS removed_features(feature)
        ), '') || '],"selected_features":[' || COALESCE((
            SELECT string_agg(to_jsonb(feature)::TEXT, ',' ORDER BY feature)
            FROM unnest(v_selected) AS selected_features(feature)
        ), '') || '],"target":' || to_jsonb(v_payload->>'target')::TEXT || '}';
    v_contract_sha256 := encode(
        extensions.digest(convert_to(v_contract_text, 'UTF8'), 'sha256'),
        'hex'
    );
    IF v_contract_sha256 IS DISTINCT FROM v_payload->>'feature_contract_hash' THEN
        RAISE EXCEPTION 'approved model retrain feature contract hash mismatch'
            USING ERRCODE = '55000';
    END IF;

    RETURN jsonb_build_object(
        'schema_version', 1,
        'job_id', v_job.job_id,
        'approval_id', v_job.approval_id,
        'dry_run_id', v_job.dry_run_id,
        'approved_payload_hash', v_job.approved_payload_hash,
        'worker_id', v_job.worker_id,
        'fencing_token', v_job.fencing_token,
        'record_version', v_job.record_version,
        'lease_expires_at', v_job.lease_expires_at,
        'execution_policy', v_job.execution_policy,
        'target', 'win',
        'model_type', 'lightgbm',
        'train_period', jsonb_build_object('start', v_train_start, 'end', v_train_end),
        'validation_period', jsonb_build_object(
            'start', v_validation_start,
            'end', v_validation_end
        ),
        'selected_features', to_jsonb(v_selected),
        'removed_features', to_jsonb(v_removed),
        'data_snapshot_sha256', v_payload->>'data_snapshot_id',
        'feature_contract_sha256', v_contract_sha256,
        'candidate_commit_sha', p_candidate_commit_sha,
        'active_model_id', p_active_model_id,
        'training_parameters', jsonb_build_object(
            'force_sync', FALSE,
            'test_size', 0.2,
            'cv_folds', 5,
            'use_optuna', FALSE
        )
    );
END;
$$;

REVOKE ALL ON FUNCTION public.get_model_retrain_execution_bundle(
    TEXT, UUID, INTEGER, BIGINT, TEXT, TEXT
) FROM PUBLIC, anon, authenticated;
GRANT EXECUTE ON FUNCTION public.get_model_retrain_execution_bundle(
    TEXT, UUID, INTEGER, BIGINT, TEXT, TEXT
) TO service_role;
