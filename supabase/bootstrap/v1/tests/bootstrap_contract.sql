-- Phase 3M post-bootstrap catalog and behavior contract.
-- Any failed assertion aborts the disposable database gate.

BEGIN;
SET LOCAL lock_timeout = '5s';
SET LOCAL statement_timeout = '120s';

DO $phase3m_contract$
DECLARE
    v_user_id CONSTANT UUID := '30000000-0000-4000-8000-000000000001';
    v_count INTEGER;
    v_text TEXT;
BEGIN
    IF to_regclass('public.profiles') IS NULL
       OR to_regclass('public.bank_records') IS NULL
       OR to_regclass('public.races') IS NULL
       OR to_regclass('public.scrape_uncertainty_review_requests') IS NULL
       OR to_regclass('public.scrape_execution_reservations') IS NULL
       OR to_regclass('public.admin_role_change_audit') IS NULL
       OR to_regclass('phase3m_internal.bootstrap_history') IS NULL THEN
        RAISE EXCEPTION 'phase3m required relation missing';
    END IF;

    SELECT count(*) INTO v_count FROM phase3m_internal.bootstrap_history;
    IF v_count < 1
       OR (SELECT min(ordinal) FROM phase3m_internal.bootstrap_history) <> 1
       OR (SELECT max(ordinal) FROM phase3m_internal.bootstrap_history) <> v_count
       OR (SELECT count(DISTINCT version) FROM phase3m_internal.bootstrap_history) <> v_count
       OR (SELECT count(DISTINCT path) FROM phase3m_internal.bootstrap_history) <> v_count
       OR (SELECT count(DISTINCT chain_digest) FROM phase3m_internal.bootstrap_history) <> 1
       OR (SELECT count(DISTINCT bootstrap_id) FROM phase3m_internal.bootstrap_history) <> 1
       OR (SELECT count(DISTINCT manifest_sha256) FROM phase3m_internal.bootstrap_history) <> 1
       OR (SELECT count(DISTINCT expected_commit_sha) FROM phase3m_internal.bootstrap_history) <> 1
       OR EXISTS (
           SELECT 1 FROM phase3m_internal.bootstrap_history
           WHERE migration_sha256 !~ '^[0-9a-f]{64}$'
              OR expected_commit_sha !~ '^[0-9a-f]{40}$'
              OR path !~ '^supabase/(bootstrap/v1/migrations|migrations)/[A-Za-z0-9_.-]+[.]sql$'
       ) THEN
        RAISE EXCEPTION 'phase3m bootstrap history contract failed';
    END IF;
    IF has_schema_privilege('anon', 'phase3m_internal', 'USAGE')
       OR has_schema_privilege('authenticated', 'phase3m_internal', 'USAGE')
       OR has_schema_privilege('service_role', 'phase3m_internal', 'USAGE') THEN
        RAISE EXCEPTION 'phase3m bootstrap history schema is exposed';
    END IF;

    SELECT count(*) INTO v_count
    FROM pg_catalog.pg_class AS c
    JOIN pg_catalog.pg_namespace AS n ON n.oid = c.relnamespace
    WHERE n.nspname = 'public'
      AND c.relkind IN ('r', 'p')
      AND NOT c.relrowsecurity;
    IF v_count <> 0 THEN
        RAISE EXCEPTION 'phase3m public table without RLS';
    END IF;

    IF has_schema_privilege('anon', 'public', 'CREATE')
       OR has_schema_privilege('authenticated', 'public', 'CREATE')
       OR has_schema_privilege('anon', 'extensions', 'CREATE')
       OR has_schema_privilege('authenticated', 'extensions', 'CREATE') THEN
        RAISE EXCEPTION 'phase3m browser role can create trusted-schema objects';
    END IF;

    SELECT count(*) INTO v_count
    FROM pg_catalog.pg_class AS c
    JOIN pg_catalog.pg_namespace AS n ON n.oid = c.relnamespace
    WHERE n.nspname = 'public'
      AND c.relkind IN ('r', 'p', 'v', 'm')
      AND (
          has_table_privilege('anon', c.oid, 'SELECT')
          OR has_table_privilege('anon', c.oid, 'INSERT')
          OR has_table_privilege('anon', c.oid, 'UPDATE')
          OR has_table_privilege('anon', c.oid, 'DELETE')
          OR has_table_privilege('anon', c.oid, 'TRUNCATE')
          OR has_table_privilege('anon', c.oid, 'REFERENCES')
          OR has_table_privilege('anon', c.oid, 'TRIGGER')
      );
    IF v_count <> 0 THEN
        RAISE EXCEPTION 'phase3m anonymous public-object privilege detected';
    END IF;

    SELECT count(*) INTO v_count
    FROM pg_catalog.pg_class AS c
    JOIN pg_catalog.pg_namespace AS n ON n.oid = c.relnamespace
    WHERE n.nspname = 'public'
      AND c.relkind = 'S'
      AND (
          has_sequence_privilege('anon', c.oid, 'USAGE')
          OR has_sequence_privilege('anon', c.oid, 'SELECT')
          OR has_sequence_privilege('anon', c.oid, 'UPDATE')
      );
    IF v_count <> 0 THEN
        RAISE EXCEPTION 'phase3m anonymous sequence privilege detected';
    END IF;

    SELECT count(*) INTO v_count
    FROM pg_catalog.pg_class AS c
    JOIN pg_catalog.pg_namespace AS n ON n.oid = c.relnamespace
    WHERE n.nspname = 'auth'
      AND c.relkind IN ('r', 'p', 'v', 'm')
      AND (
          has_table_privilege('anon', c.oid, 'SELECT')
          OR has_table_privilege('anon', c.oid, 'INSERT')
          OR has_table_privilege('anon', c.oid, 'UPDATE')
          OR has_table_privilege('anon', c.oid, 'DELETE')
          OR has_table_privilege('authenticated', c.oid, 'SELECT')
          OR has_table_privilege('authenticated', c.oid, 'INSERT')
          OR has_table_privilege('authenticated', c.oid, 'UPDATE')
          OR has_table_privilege('authenticated', c.oid, 'DELETE')
      );
    IF v_count <> 0 THEN
        RAISE EXCEPTION 'phase3m browser privilege on auth object';
    END IF;

    SELECT count(*) INTO v_count
    FROM pg_catalog.pg_class AS c
    JOIN pg_catalog.pg_namespace AS n ON n.oid = c.relnamespace
    WHERE n.nspname = 'public'
      AND c.relname = ANY (ARRAY[
          'races_ultimate', 'race_results_ultimate', 'model_metadata',
          'horse_pedigree', 'ml_models', 'ml_training_data',
          'scrape_uncertainty_review_requests', 'scrape_uncertainty_review_events',
          'scrape_execution_authorizations', 'scrape_execution_reservations',
          'scrape_execution_reservation_events', 'admin_role_change_audit'
      ])
      AND (
          has_table_privilege('authenticated', c.oid, 'SELECT')
          OR has_table_privilege('authenticated', c.oid, 'INSERT')
          OR has_table_privilege('authenticated', c.oid, 'UPDATE')
          OR has_table_privilege('authenticated', c.oid, 'DELETE')
          OR has_table_privilege('authenticated', c.oid, 'TRUNCATE')
          OR has_table_privilege('authenticated', c.oid, 'REFERENCES')
          OR has_table_privilege('authenticated', c.oid, 'TRIGGER')
      );
    IF v_count <> 0 THEN
        RAISE EXCEPTION 'phase3m browser privilege on server-only object';
    END IF;

    IF has_table_privilege('service_role', 'public.admin_role_change_audit', 'SELECT')
       OR has_table_privilege('service_role', 'public.admin_role_change_audit', 'INSERT')
       OR has_table_privilege('service_role', 'public.admin_role_change_audit', 'UPDATE')
       OR has_table_privilege('service_role', 'public.admin_role_change_audit', 'DELETE') THEN
        RAISE EXCEPTION 'phase3m admin role audit table is directly exposed';
    END IF;

    IF has_column_privilege('authenticated', 'public.profiles', 'role', 'UPDATE')
       OR has_column_privilege('authenticated', 'public.profiles', 'subscription_tier', 'UPDATE')
       OR has_column_privilege('authenticated', 'public.profiles', 'stripe_customer_id', 'SELECT')
       OR has_column_privilege('authenticated', 'public.profiles', 'stripe_subscription_id', 'SELECT') THEN
        RAISE EXCEPTION 'phase3m sensitive profile privilege detected';
    END IF;

    SELECT count(*) INTO v_count
    FROM pg_catalog.pg_proc AS p
    JOIN pg_catalog.pg_namespace AS n ON n.oid = p.pronamespace
    WHERE n.nspname = 'public'
      AND p.prosecdef
      AND (
          p.proconfig IS NULL
          OR NOT EXISTS (
              SELECT 1
              FROM unnest(p.proconfig) AS setting
              WHERE setting ~ '^search_path=(pg_catalog, )?public(, extensions)?$'
          )
          OR EXISTS (
              SELECT 1
              FROM unnest(p.proconfig) AS setting
              WHERE setting LIKE 'search_path=%'
                AND setting !~ '^search_path=(pg_catalog, )?public(, extensions)?$'
          )
      );
    IF v_count <> 0 THEN
        RAISE EXCEPTION 'phase3m SECURITY DEFINER search_path is not fixed';
    END IF;

    SELECT count(*) INTO v_count
    FROM pg_catalog.pg_proc AS p
    JOIN pg_catalog.pg_namespace AS n ON n.oid = p.pronamespace
    WHERE n.nspname = 'public'
      AND p.prosecdef
      AND has_function_privilege('anon', p.oid, 'EXECUTE');
    IF v_count <> 0 THEN
        RAISE EXCEPTION 'phase3m anonymous SECURITY DEFINER execution detected';
    END IF;

    SELECT count(*) INTO v_count
    FROM pg_catalog.pg_proc AS p
    JOIN pg_catalog.pg_namespace AS n ON n.oid = p.pronamespace
    WHERE n.nspname = 'public'
      AND p.prosecdef
      AND has_function_privilege('authenticated', p.oid, 'EXECUTE');
    IF v_count <> 0 THEN
        RAISE EXCEPTION 'phase3m authenticated SECURITY DEFINER execution detected';
    END IF;

    IF has_function_privilege('authenticated', 'public.consume_pred_count(uuid)', 'EXECUTE')
       OR has_function_privilege('authenticated', 'public.consume_pred_count_batch(uuid,integer)', 'EXECUTE')
       OR has_function_privilege('authenticated', 'public.consume_ocr_quota(uuid)', 'EXECUTE')
       OR has_function_privilege('authenticated', 'public.update_admin_profile_role(uuid,uuid,text,uuid)', 'EXECUTE')
       OR has_function_privilege('authenticated', 'public.reserve_scrape_execution(uuid,uuid,uuid,uuid,uuid,integer,uuid,text,integer,integer)', 'EXECUTE')
       OR has_function_privilege('authenticated', 'public.consume_scrape_execution_reservation(uuid,integer,uuid,uuid,uuid,uuid,integer,uuid,text)', 'EXECUTE')
       OR has_function_privilege('authenticated', 'public.release_scrape_execution_reservation(uuid,integer,uuid,uuid,uuid,text,text)', 'EXECUTE') THEN
        RAISE EXCEPTION 'phase3m server-only RPC exposed to browser role';
    END IF;

    IF NOT (
        has_function_privilege('service_role', 'public.reset_pred_count_if_needed(uuid)', 'EXECUTE')
        AND has_function_privilege('service_role', 'public.consume_pred_count(uuid)', 'EXECUTE')
        AND has_function_privilege('service_role', 'public.consume_pred_count_batch(uuid,integer)', 'EXECUTE')
        AND has_function_privilege('service_role', 'public.consume_ocr_quota(uuid)', 'EXECUTE')
        AND has_function_privilege('service_role', 'public.update_admin_profile_role(uuid,uuid,text,uuid)', 'EXECUTE')
        AND has_function_privilege('service_role', 'public.create_scrape_uncertainty_review(uuid,uuid,text,text,text,boolean,timestamp with time zone,text,boolean,boolean)', 'EXECUTE')
        AND has_function_privilege('service_role', 'public.get_scrape_uncertainty_review(uuid,uuid)', 'EXECUTE')
        AND has_function_privilege('service_role', 'public.list_scrape_uncertainty_reviews(uuid,text,integer)', 'EXECUTE')
        AND has_function_privilege('service_role', 'public.transition_scrape_uncertainty_review(uuid,uuid,integer,text,text)', 'EXECUTE')
        AND has_function_privilege('service_role', 'public.reserve_scrape_execution(uuid,uuid,uuid,uuid,uuid,integer,uuid,text,integer,integer)', 'EXECUTE')
        AND has_function_privilege('service_role', 'public.consume_scrape_execution_reservation(uuid,integer,uuid,uuid,uuid,uuid,integer,uuid,text)', 'EXECUTE')
        AND has_function_privilege('service_role', 'public.release_scrape_execution_reservation(uuid,integer,uuid,uuid,uuid,text,text)', 'EXECUTE')
        AND has_function_privilege('service_role', 'public.expire_scrape_execution_reservation(uuid,integer)', 'EXECUTE')
    ) THEN
        RAISE EXCEPTION 'phase3m service role RPC grant missing';
    END IF;

    INSERT INTO auth.users (id, email, raw_user_meta_data)
    VALUES (
        v_user_id,
        'phase3m-contract@example.invalid',
        '{"full_name":"Bootstrap Contract","role":"admin","subscription_tier":"premium"}'::JSONB
    );

    INSERT INTO auth.users (id, email, raw_user_meta_data)
    VALUES (
        '30000000-0000-4000-8000-000000000002',
        'phase3m-contract-b@example.invalid',
        '{"full_name":"Second Contract User"}'::JSONB
    );

    SELECT count(*) INTO v_count
    FROM public.profiles
    WHERE id = v_user_id
      AND email = 'phase3m-contract@example.invalid'
      AND full_name = 'Bootstrap Contract'
      AND role = 'user'
      AND subscription_tier = 'free';
    IF v_count <> 1 THEN
        RAISE EXCEPTION 'phase3m profile trigger contract failed';
    END IF;

    SELECT count(*) INTO v_count
    FROM public.bank_records
    WHERE user_id = v_user_id
      AND initial_bank = 100000
      AND current_bank = 100000
      AND total_bet = 0
      AND total_return = 0;
    IF v_count <> 1 THEN
        RAISE EXCEPTION 'phase3m bank bootstrap contract failed';
    END IF;

    SELECT count(*) INTO v_count
    FROM storage.buckets
    WHERE id = 'models'
      AND name = 'models'
      AND public IS FALSE
      AND file_size_limit = 104857600
      AND allowed_mime_types @> ARRAY['application/octet-stream']::TEXT[];
    IF v_count <> 1 THEN
        RAISE EXCEPTION 'phase3m private model bucket contract failed';
    END IF;

    SELECT count(*) INTO v_count
    FROM pg_catalog.pg_policy AS pol
    JOIN pg_catalog.pg_class AS c ON c.oid = pol.polrelid
    JOIN pg_catalog.pg_namespace AS n ON n.oid = c.relnamespace
    WHERE n.nspname = 'storage'
      AND c.relname = 'objects'
      AND pol.polname = 'phase3m_models_browser_deny';
    IF v_count <> 1 THEN
        RAISE EXCEPTION 'phase3m private model storage policy contract failed';
    END IF;

    SELECT array_to_string(c.reloptions, ',') INTO v_text
    FROM pg_catalog.pg_class AS c
    JOIN pg_catalog.pg_namespace AS n ON n.oid = c.relnamespace
    WHERE n.nspname = 'public' AND c.relname = 'ml_training_data' AND c.relkind = 'v';
    IF v_text IS NULL OR position('security_invoker=true' IN v_text) = 0 THEN
        RAISE EXCEPTION 'phase3m ML view is not security invoker';
    END IF;

    SELECT count(*) INTO v_count
    FROM pg_catalog.pg_trigger AS t
    JOIN pg_catalog.pg_class AS c ON c.oid = t.tgrelid
    JOIN pg_catalog.pg_namespace AS n ON n.oid = c.relnamespace
    WHERE NOT t.tgisinternal
      AND t.tgenabled <> 'D'
      AND (
          (n.nspname = 'auth' AND c.relname = 'users' AND t.tgname = 'on_auth_user_created')
          OR (n.nspname = 'public' AND c.relname = 'profiles' AND t.tgname = 'profiles_touch_updated_at')
          OR (n.nspname = 'public' AND c.relname = 'bank_records' AND t.tgname = 'bank_records_touch_updated_at')
          OR (n.nspname = 'public' AND c.relname = 'race_results_ultimate' AND t.tgname = 'phase3m_race_results_ultimate_horse_number')
      );
    IF v_count <> 4 THEN
        RAISE EXCEPTION 'phase3m required trigger missing or disabled';
    END IF;

    INSERT INTO public.races_ultimate (race_id, data)
    VALUES ('phase3m-contract-race', '{"source":"contract"}'::JSONB);

    INSERT INTO public.race_results_ultimate (race_id, data)
    VALUES ('phase3m-contract-race', '{"horse_num":"7"}'::JSONB);
    IF NOT EXISTS (
        SELECT 1 FROM public.race_results_ultimate
        WHERE race_id = 'phase3m-contract-race' AND horse_number = '7'
    ) THEN
        RAISE EXCEPTION 'phase3m normalization trigger contract failed';
    END IF;
END;
$phase3m_contract$;

SET LOCAL ROLE authenticated;
SELECT set_config('request.jwt.claim.sub', '30000000-0000-4000-8000-000000000001', TRUE);
SELECT set_config('request.jwt.claim.role', 'authenticated', TRUE);
SELECT set_config(
    'request.jwt.claims',
    '{"sub":"30000000-0000-4000-8000-000000000001","role":"authenticated"}',
    TRUE
);

DO $phase3m_user_a$
DECLARE
    v_count INTEGER;
    v_denied BOOLEAN;
BEGIN
    IF auth.uid() <> '30000000-0000-4000-8000-000000000001'::UUID
       OR auth.role() <> 'authenticated'
       OR auth.jwt() ->> 'sub' <> '30000000-0000-4000-8000-000000000001' THEN
        RAISE EXCEPTION 'phase3m JWT emulation contract failed';
    END IF;

    SELECT count(*) INTO v_count FROM public.profiles;
    IF v_count <> 1 THEN
        RAISE EXCEPTION 'phase3m profile RLS own-row boundary failed';
    END IF;
    SELECT count(*) INTO v_count FROM public.bank_records;
    IF v_count <> 1 THEN
        RAISE EXCEPTION 'phase3m bank RLS own-row boundary failed';
    END IF;

    UPDATE public.profiles SET full_name = 'Updated Contract User'
    WHERE id = '30000000-0000-4000-8000-000000000001';
    GET DIAGNOSTICS v_count = ROW_COUNT;
    IF v_count <> 1 THEN
        RAISE EXCEPTION 'phase3m own profile update failed';
    END IF;

    v_denied := FALSE;
    BEGIN
        UPDATE public.profiles SET role = 'admin'
        WHERE id = '30000000-0000-4000-8000-000000000001';
    EXCEPTION WHEN insufficient_privilege THEN
        v_denied := TRUE;
    END;
    IF NOT v_denied THEN
        RAISE EXCEPTION 'phase3m role escalation was not denied';
    END IF;

    v_denied := FALSE;
    BEGIN
        UPDATE public.profiles SET pred_count_remaining = -1
        WHERE id = '30000000-0000-4000-8000-000000000001';
    EXCEPTION WHEN insufficient_privilege THEN
        v_denied := TRUE;
    END;
    IF NOT v_denied THEN
        RAISE EXCEPTION 'phase3m quota mutation was not denied';
    END IF;

    UPDATE public.bank_records SET current_bank = 90000
    WHERE user_id = '30000000-0000-4000-8000-000000000001';
    GET DIAGNOSTICS v_count = ROW_COUNT;
    IF v_count <> 1 THEN
        RAISE EXCEPTION 'phase3m own bank update failed';
    END IF;

    INSERT INTO public.predictions (
        id, user_id, race_name, race_date, horse_data, predicted_results
    ) VALUES (
        '31000000-0000-4000-8000-000000000001',
        '30000000-0000-4000-8000-000000000001',
        'Contract Race', DATE '2026-01-01', '{}'::JSONB, '{}'::JSONB
    );
    UPDATE public.predictions SET race_name = 'Updated Contract Race'
    WHERE id = '31000000-0000-4000-8000-000000000001';
    GET DIAGNOSTICS v_count = ROW_COUNT;
    IF v_count <> 1 THEN
        RAISE EXCEPTION 'phase3m own prediction update failed';
    END IF;

    INSERT INTO public.bets (
        id, user_id, prediction_id, race_name, race_date, bet_type, bet_amount
    ) VALUES (
        '32000000-0000-4000-8000-000000000001',
        '30000000-0000-4000-8000-000000000001',
        '31000000-0000-4000-8000-000000000001',
        'Contract Race', DATE '2026-01-01', 'win', 100
    );
    UPDATE public.bets SET bet_amount = 200
    WHERE id = '32000000-0000-4000-8000-000000000001';
    GET DIAGNOSTICS v_count = ROW_COUNT;
    IF v_count <> 1 THEN
        RAISE EXCEPTION 'phase3m own bet update failed';
    END IF;

    INSERT INTO public.purchase_history (
        id, user_id, race_id, bet_type, total_cost
    ) VALUES (
        '33000000-0000-4000-8000-000000000001',
        '30000000-0000-4000-8000-000000000001',
        'phase3m-contract-race', 'win', 100
    );
    UPDATE public.purchase_history SET total_cost = 200
    WHERE id = '33000000-0000-4000-8000-000000000001';
    GET DIAGNOSTICS v_count = ROW_COUNT;
    IF v_count <> 1 THEN
        RAISE EXCEPTION 'phase3m own purchase update failed';
    END IF;

    IF NOT has_table_privilege('authenticated', 'storage.objects', 'INSERT') THEN
        RAISE EXCEPTION 'phase3m hosted Storage ACL emulation missing';
    END IF;
    SELECT count(*) INTO v_count FROM storage.buckets WHERE id = 'models';
    IF v_count <> 0 THEN
        RAISE EXCEPTION 'phase3m authenticated role observed private bucket';
    END IF;
    v_denied := FALSE;
    BEGIN
        INSERT INTO storage.objects (bucket_id, name)
        VALUES ('models', 'browser-denied.bin');
    EXCEPTION WHEN insufficient_privilege THEN
        v_denied := TRUE;
    END;
    IF NOT v_denied THEN
        RAISE EXCEPTION 'phase3m authenticated models write was not denied';
    END IF;
END;
$phase3m_user_a$;

RESET ROLE;
SET LOCAL ROLE authenticated;
SELECT set_config('request.jwt.claim.sub', '30000000-0000-4000-8000-000000000002', TRUE);
SELECT set_config('request.jwt.claim.role', 'authenticated', TRUE);
SELECT set_config(
    'request.jwt.claims',
    '{"sub":"30000000-0000-4000-8000-000000000002","role":"authenticated"}',
    TRUE
);

DO $phase3m_user_b$
DECLARE
    v_count INTEGER;
    v_denied BOOLEAN;
BEGIN
    SELECT count(*) INTO v_count FROM public.profiles;
    IF v_count <> 1 OR EXISTS (
        SELECT 1 FROM public.profiles
        WHERE id = '30000000-0000-4000-8000-000000000001'
    ) THEN
        RAISE EXCEPTION 'phase3m cross-user profile read exposed';
    END IF;
    IF EXISTS (
        SELECT 1 FROM public.predictions
        WHERE id = '31000000-0000-4000-8000-000000000001'
    ) OR EXISTS (
        SELECT 1 FROM public.bets
        WHERE id = '32000000-0000-4000-8000-000000000001'
    ) OR EXISTS (
        SELECT 1 FROM public.purchase_history
        WHERE id = '33000000-0000-4000-8000-000000000001'
    ) OR EXISTS (
        SELECT 1 FROM public.bank_records
        WHERE user_id = '30000000-0000-4000-8000-000000000001'
    ) THEN
        RAISE EXCEPTION 'phase3m cross-user row read exposed';
    END IF;

    UPDATE public.predictions SET race_name = 'idor'
    WHERE id = '31000000-0000-4000-8000-000000000001';
    GET DIAGNOSTICS v_count = ROW_COUNT;
    IF v_count <> 0 THEN
        RAISE EXCEPTION 'phase3m cross-user prediction update exposed';
    END IF;
    DELETE FROM public.bets
    WHERE id = '32000000-0000-4000-8000-000000000001';
    GET DIAGNOSTICS v_count = ROW_COUNT;
    IF v_count <> 0 THEN
        RAISE EXCEPTION 'phase3m cross-user bet delete exposed';
    END IF;
    UPDATE public.bank_records SET current_bank = 1
    WHERE user_id = '30000000-0000-4000-8000-000000000001';
    GET DIAGNOSTICS v_count = ROW_COUNT;
    IF v_count <> 0 THEN
        RAISE EXCEPTION 'phase3m cross-user bank update exposed';
    END IF;
    DELETE FROM public.purchase_history
    WHERE id = '33000000-0000-4000-8000-000000000001';
    GET DIAGNOSTICS v_count = ROW_COUNT;
    IF v_count <> 0 THEN
        RAISE EXCEPTION 'phase3m cross-user purchase delete exposed';
    END IF;

    v_denied := FALSE;
    BEGIN
        INSERT INTO public.predictions (
            id, user_id, race_name, race_date, horse_data, predicted_results
        ) VALUES (
            '31000000-0000-4000-8000-000000000002',
            '30000000-0000-4000-8000-000000000001',
            'IDOR Race', DATE '2026-01-02', '{}'::JSONB, '{}'::JSONB
        );
    EXCEPTION WHEN insufficient_privilege THEN
        v_denied := TRUE;
    END;
    IF NOT v_denied THEN
        RAISE EXCEPTION 'phase3m cross-user prediction insert exposed';
    END IF;

    v_denied := FALSE;
    BEGIN
        INSERT INTO public.bets (
            id, user_id, race_name, race_date, bet_type, bet_amount
        ) VALUES (
            '32000000-0000-4000-8000-000000000002',
            '30000000-0000-4000-8000-000000000001',
            'IDOR Race', DATE '2026-01-02', 'win', 100
        );
    EXCEPTION WHEN insufficient_privilege THEN
        v_denied := TRUE;
    END;
    IF NOT v_denied THEN
        RAISE EXCEPTION 'phase3m cross-user bet insert exposed';
    END IF;

    v_denied := FALSE;
    BEGIN
        INSERT INTO public.bets (
            id, user_id, prediction_id, race_name, race_date, bet_type, bet_amount
        ) VALUES (
            '32000000-0000-4000-8000-000000000003',
            '30000000-0000-4000-8000-000000000002',
            '31000000-0000-4000-8000-000000000001',
            'Cross-owner Prediction', DATE '2026-01-02', 'win', 100
        );
    EXCEPTION WHEN foreign_key_violation THEN
        v_denied := TRUE;
    END;
    IF NOT v_denied THEN
        RAISE EXCEPTION 'phase3m cross-owner prediction reference exposed';
    END IF;

    v_denied := FALSE;
    BEGIN
        INSERT INTO public.purchase_history (
            id, user_id, race_id, bet_type, total_cost
        ) VALUES (
            '33000000-0000-4000-8000-000000000002',
            '30000000-0000-4000-8000-000000000001',
            'idor-race', 'win', 100
        );
    EXCEPTION WHEN insufficient_privilege THEN
        v_denied := TRUE;
    END;
    IF NOT v_denied THEN
        RAISE EXCEPTION 'phase3m cross-user purchase insert exposed';
    END IF;
END;
$phase3m_user_b$;

RESET ROLE;
SET LOCAL ROLE authenticated;
SELECT set_config('request.jwt.claim.sub', '30000000-0000-4000-8000-000000000001', TRUE);
SELECT set_config('request.jwt.claim.role', 'authenticated', TRUE);
SELECT set_config(
    'request.jwt.claims',
    '{"sub":"30000000-0000-4000-8000-000000000001","role":"authenticated"}',
    TRUE
);

DO $phase3m_user_a_cleanup$
DECLARE
    v_count INTEGER;
BEGIN
    DELETE FROM public.bets WHERE id = '32000000-0000-4000-8000-000000000001';
    GET DIAGNOSTICS v_count = ROW_COUNT;
    IF v_count <> 1 THEN RAISE EXCEPTION 'phase3m own bet delete failed'; END IF;
    DELETE FROM public.predictions WHERE id = '31000000-0000-4000-8000-000000000001';
    GET DIAGNOSTICS v_count = ROW_COUNT;
    IF v_count <> 1 THEN RAISE EXCEPTION 'phase3m own prediction delete failed'; END IF;
    DELETE FROM public.purchase_history WHERE id = '33000000-0000-4000-8000-000000000001';
    GET DIAGNOSTICS v_count = ROW_COUNT;
    IF v_count <> 1 THEN RAISE EXCEPTION 'phase3m own purchase delete failed'; END IF;
END;
$phase3m_user_a_cleanup$;

RESET ROLE;
SET LOCAL ROLE service_role;
DO $phase3m_service_storage$
DECLARE
    v_count INTEGER;
    v_allowed BOOLEAN;
    v_used INTEGER;
    v_limit INTEGER;
    v_reset_at TIMESTAMPTZ;
    v_updated_id UUID;
    v_updated_role TEXT;
    v_request_id UUID;
    v_denied BOOLEAN;
BEGIN
    UPDATE public.profiles
    SET role = CASE
        WHEN id = '30000000-0000-4000-8000-000000000001' THEN 'admin'
        ELSE 'user'
    END
    WHERE id IN (
        '30000000-0000-4000-8000-000000000001',
        '30000000-0000-4000-8000-000000000002'
    );

    v_denied := FALSE;
    BEGIN
        PERFORM 1
        FROM public.update_admin_profile_role(
            '30000000-0000-4000-8000-000000000002',
            '30000000-0000-4000-8000-000000000001',
            'user',
            '35000000-0000-4000-8000-000000000001'
        );
    EXCEPTION WHEN insufficient_privilege THEN
        v_denied := TRUE;
    END;
    IF NOT v_denied THEN
        RAISE EXCEPTION 'phase3m non-admin actor role transition was not denied';
    END IF;

    SELECT q.id, q.role, q.request_id
    INTO v_updated_id, v_updated_role, v_request_id
    FROM public.update_admin_profile_role(
        '30000000-0000-4000-8000-000000000001',
        '30000000-0000-4000-8000-000000000002',
        'admin',
        '35000000-0000-4000-8000-000000000002'
    ) AS q;
    IF v_updated_id <> '30000000-0000-4000-8000-000000000002'
       OR v_updated_role <> 'admin'
       OR v_request_id <> '35000000-0000-4000-8000-000000000002' THEN
        RAISE EXCEPTION 'phase3m admin role promotion response mismatch';
    END IF;

    SELECT q.id, q.role, q.request_id
    INTO v_updated_id, v_updated_role, v_request_id
    FROM public.update_admin_profile_role(
        '30000000-0000-4000-8000-000000000002',
        '30000000-0000-4000-8000-000000000001',
        'user',
        '35000000-0000-4000-8000-000000000003'
    ) AS q;
    IF v_updated_id <> '30000000-0000-4000-8000-000000000001'
       OR v_updated_role <> 'user'
       OR v_request_id <> '35000000-0000-4000-8000-000000000003' THEN
        RAISE EXCEPTION 'phase3m admin role demotion response mismatch';
    END IF;

    v_denied := FALSE;
    BEGIN
        PERFORM 1
        FROM public.update_admin_profile_role(
            '30000000-0000-4000-8000-000000000002',
            '30000000-0000-4000-8000-000000000002',
            'user',
            '35000000-0000-4000-8000-000000000004'
        );
    EXCEPTION WHEN raise_exception THEN
        v_denied := TRUE;
    END;
    IF NOT v_denied OR NOT EXISTS (
        SELECT 1 FROM public.profiles
        WHERE id = '30000000-0000-4000-8000-000000000002' AND role = 'admin'
    ) THEN
        RAISE EXCEPTION 'phase3m last Admin demotion invariant failed';
    END IF;

    UPDATE public.profiles
    SET ocr_monthly_limit = 2,
        ocr_used_this_month = 0,
        ocr_reset_date = NOW()
    WHERE id = '30000000-0000-4000-8000-000000000001';

    SELECT q.allowed, q.used_count, q.monthly_limit, q.reset_at
    INTO v_allowed, v_used, v_limit, v_reset_at
    FROM public.consume_ocr_quota('30000000-0000-4000-8000-000000000001') AS q;
    IF v_allowed IS NOT TRUE OR v_used <> 1 OR v_limit <> 2 OR v_reset_at IS NULL THEN
        RAISE EXCEPTION 'phase3m OCR quota first reservation failed';
    END IF;

    SELECT q.allowed, q.used_count, q.monthly_limit, q.reset_at
    INTO v_allowed, v_used, v_limit, v_reset_at
    FROM public.consume_ocr_quota('30000000-0000-4000-8000-000000000001') AS q;
    IF v_allowed IS NOT TRUE OR v_used <> 2 OR v_limit <> 2 THEN
        RAISE EXCEPTION 'phase3m OCR quota boundary reservation failed';
    END IF;

    SELECT q.allowed, q.used_count, q.monthly_limit, q.reset_at
    INTO v_allowed, v_used, v_limit, v_reset_at
    FROM public.consume_ocr_quota('30000000-0000-4000-8000-000000000001') AS q;
    IF v_allowed IS NOT FALSE OR v_used <> 2 OR v_limit <> 2 THEN
        RAISE EXCEPTION 'phase3m OCR quota exhaustion contract failed';
    END IF;

    UPDATE public.profiles
    SET ocr_used_this_month = 2,
        ocr_reset_date = NOW() - INTERVAL '2 months'
    WHERE id = '30000000-0000-4000-8000-000000000001';
    SELECT q.allowed, q.used_count, q.monthly_limit, q.reset_at
    INTO v_allowed, v_used, v_limit, v_reset_at
    FROM public.consume_ocr_quota('30000000-0000-4000-8000-000000000001') AS q;
    IF v_allowed IS NOT TRUE OR v_used <> 1 OR v_limit <> 2
       OR v_reset_at < NOW() - INTERVAL '1 minute' THEN
        RAISE EXCEPTION 'phase3m OCR quota monthly reset contract failed';
    END IF;

    SELECT count(*) INTO v_count FROM storage.buckets WHERE id = 'models' AND public IS FALSE;
    IF v_count <> 1 THEN
        RAISE EXCEPTION 'phase3m service role cannot observe models bucket';
    END IF;
    INSERT INTO storage.objects (id, bucket_id, name)
    VALUES ('34000000-0000-4000-8000-000000000001', 'models', 'service-visible.bin');
    SELECT count(*) INTO v_count
    FROM storage.objects WHERE bucket_id = 'models' AND name = 'service-visible.bin';
    IF v_count <> 1 THEN
        RAISE EXCEPTION 'phase3m service role storage write failed';
    END IF;
    DELETE FROM storage.objects WHERE id = '34000000-0000-4000-8000-000000000001';
END;
$phase3m_service_storage$;
RESET ROLE;

DO $phase3m_admin_audit$
DECLARE
    v_count INTEGER;
BEGIN
    SELECT count(*) INTO v_count
    FROM public.admin_role_change_audit
    WHERE (request_id = '35000000-0000-4000-8000-000000000002'
           AND actor_user_id = '30000000-0000-4000-8000-000000000001'
           AND target_user_id = '30000000-0000-4000-8000-000000000002'
           AND previous_role = 'user' AND new_role = 'admin')
       OR (request_id = '35000000-0000-4000-8000-000000000003'
           AND actor_user_id = '30000000-0000-4000-8000-000000000002'
           AND target_user_id = '30000000-0000-4000-8000-000000000001'
           AND previous_role = 'admin' AND new_role = 'user');
    IF v_count <> 2
       OR EXISTS (
           SELECT 1 FROM public.admin_role_change_audit
           WHERE request_id IN (
               '35000000-0000-4000-8000-000000000001',
               '35000000-0000-4000-8000-000000000004'
           )
       ) THEN
        RAISE EXCEPTION 'phase3m admin role audit contract failed';
    END IF;
END;
$phase3m_admin_audit$;

SELECT marker
FROM (VALUES
    ('phase3m_check:all_public_tables_rls'),
    ('phase3m_check:authenticated_idor_boundaries'),
    ('phase3m_check:bootstrap_history_authoritative'),
    ('phase3m_check:no_unsafe_browser_grants'),
    ('phase3m_check:security_definer_hardened'),
    ('phase3m_check:service_rpc_grants'),
    ('phase3m_check:profile_bank_trigger'),
    ('phase3m_check:private_model_storage'),
    ('phase3m_check:security_invoker_ml_view'),
    ('phase3m_check:storage_role_boundaries'),
    ('phase3m_check:required_triggers_enabled')
) AS markers(marker)
ORDER BY marker;

WITH catalog_items AS (
    SELECT format(
        'relation|%s|%s|%s|%s|%s|%s|%s',
        n.nspname, c.relname, c.relkind, c.relrowsecurity, c.relforcerowsecurity,
        COALESCE(array_to_string(c.reloptions, ','), ''), COALESCE(c.relacl::TEXT, '')
    ) AS item
    FROM pg_catalog.pg_class AS c
    JOIN pg_catalog.pg_namespace AS n ON n.oid = c.relnamespace
    WHERE n.nspname IN ('public', 'auth', 'storage', 'phase3m_internal')
      AND c.relkind IN ('r', 'p', 'v', 'm', 'S')

    UNION ALL
    SELECT format(
        'column|%s|%s|%s|%s|%s|%s|%s|%s|%s',
        n.nspname, c.relname, a.attnum, a.attname,
        pg_catalog.format_type(a.atttypid, a.atttypmod),
        COALESCE(pg_catalog.pg_get_expr(d.adbin, d.adrelid), ''),
        a.attnotnull, a.attidentity, a.attgenerated
    )
    FROM pg_catalog.pg_attribute AS a
    JOIN pg_catalog.pg_class AS c ON c.oid = a.attrelid
    JOIN pg_catalog.pg_namespace AS n ON n.oid = c.relnamespace
    LEFT JOIN pg_catalog.pg_attrdef AS d
      ON d.adrelid = a.attrelid AND d.adnum = a.attnum
    WHERE n.nspname IN ('public', 'auth', 'storage', 'phase3m_internal')
      AND a.attnum > 0 AND NOT a.attisdropped

    UNION ALL
    SELECT format(
        'constraint|%s|%s|%s|%s',
        n.nspname, c.relname, con.conname, pg_catalog.pg_get_constraintdef(con.oid, TRUE)
    )
    FROM pg_catalog.pg_constraint AS con
    JOIN pg_catalog.pg_class AS c ON c.oid = con.conrelid
    JOIN pg_catalog.pg_namespace AS n ON n.oid = c.relnamespace
    WHERE n.nspname IN ('public', 'auth', 'storage', 'phase3m_internal')

    UNION ALL
    SELECT format('index|%s|%s|%s', n.nspname, c.relname, pg_catalog.pg_get_indexdef(i.indexrelid))
    FROM pg_catalog.pg_index AS i
    JOIN pg_catalog.pg_class AS c ON c.oid = i.indrelid
    JOIN pg_catalog.pg_namespace AS n ON n.oid = c.relnamespace
    WHERE n.nspname IN ('public', 'auth', 'storage', 'phase3m_internal')

    UNION ALL
    SELECT format(
        'policy|%s|%s|%s|%s|%s|%s|%s',
        schemaname, tablename, policyname, cmd,
        array_to_string(roles, ','), COALESCE(qual, ''), COALESCE(with_check, '')
    )
    FROM pg_catalog.pg_policies
    WHERE schemaname IN ('public', 'auth', 'storage', 'phase3m_internal')

    UNION ALL
    SELECT format(
        'function|%s|%s|%s|%s|%s|%s|%s',
        n.nspname, p.proname, pg_catalog.oidvectortypes(p.proargtypes), p.prosecdef,
        COALESCE(array_to_string(p.proconfig, ','), ''), COALESCE(p.proacl::TEXT, ''),
        pg_catalog.pg_get_functiondef(p.oid)
    )
    FROM pg_catalog.pg_proc AS p
    JOIN pg_catalog.pg_namespace AS n ON n.oid = p.pronamespace
    WHERE n.nspname IN ('public', 'auth', 'storage', 'phase3m_internal')

    UNION ALL
    SELECT format(
        'trigger|%s|%s|%s|%s',
        n.nspname, c.relname, t.tgname, pg_catalog.pg_get_triggerdef(t.oid, TRUE)
    )
    FROM pg_catalog.pg_trigger AS t
    JOIN pg_catalog.pg_class AS c ON c.oid = t.tgrelid
    JOIN pg_catalog.pg_namespace AS n ON n.oid = c.relnamespace
    WHERE n.nspname IN ('public', 'auth', 'storage', 'phase3m_internal') AND NOT t.tgisinternal

    UNION ALL
    SELECT format('extension|%s|%s|%s', e.extname, e.extversion, n.nspname)
    FROM pg_catalog.pg_extension AS e
    JOIN pg_catalog.pg_namespace AS n ON n.oid = e.extnamespace
    WHERE e.extname IN ('pgcrypto', 'uuid-ossp')
)
SELECT 'phase3m_fingerprint:' ||
       encode(extensions.digest(string_agg(item, E'\n' ORDER BY item), 'sha256'), 'hex')
FROM catalog_items;

ROLLBACK;
