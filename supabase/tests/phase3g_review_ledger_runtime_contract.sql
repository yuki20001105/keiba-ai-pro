\set ON_ERROR_STOP on

CREATE SCHEMA phase3g_assert AUTHORIZATION postgres;

CREATE OR REPLACE FUNCTION phase3g_assert.ok(p_condition BOOLEAN, p_label TEXT)
RETURNS VOID
LANGUAGE plpgsql
AS $$
BEGIN
    IF p_condition IS DISTINCT FROM TRUE THEN
        RAISE EXCEPTION 'phase3g assertion failed: %', p_label USING ERRCODE = 'P0001';
    END IF;
END;
$$;

CREATE OR REPLACE FUNCTION phase3g_assert.sqlstate(
    p_statement TEXT,
    p_expected_sqlstate TEXT,
    p_label TEXT
)
RETURNS VOID
LANGUAGE plpgsql
AS $$
BEGIN
    BEGIN
        EXECUTE p_statement;
    EXCEPTION WHEN OTHERS THEN
        IF SQLSTATE = p_expected_sqlstate THEN
            RETURN;
        END IF;
        RAISE EXCEPTION 'phase3g assertion failed: % (unexpected SQLSTATE)', p_label
            USING ERRCODE = 'P0001';
    END;
    RAISE EXCEPTION 'phase3g assertion failed: % (statement succeeded)', p_label
        USING ERRCODE = 'P0001';
END;
$$;

GRANT USAGE ON SCHEMA phase3g_assert TO anon, authenticated, service_role;
GRANT EXECUTE ON FUNCTION phase3g_assert.ok(BOOLEAN, TEXT)
    TO anon, authenticated, service_role;
GRANT EXECUTE ON FUNCTION phase3g_assert.sqlstate(TEXT, TEXT, TEXT)
    TO anon, authenticated, service_role;

-- Catalog and privilege boundary.
SELECT phase3g_assert.ok(
    to_regclass('public.scrape_uncertainty_review_requests') IS NOT NULL,
    'request table is present'
);
SELECT 'phase3g_check:request_table_present';

SELECT phase3g_assert.ok(
    to_regclass('public.scrape_uncertainty_review_events') IS NOT NULL,
    'event table is present'
);
SELECT 'phase3g_check:event_table_present';

SELECT phase3g_assert.ok(
    (SELECT bool_and(c.relrowsecurity)
     FROM pg_class c
     WHERE c.oid IN (
       'public.scrape_uncertainty_review_requests'::regclass,
       'public.scrape_uncertainty_review_events'::regclass
     )),
    'RLS is enabled on both ledger tables'
);
SELECT 'phase3g_check:rls_enabled';

SELECT phase3g_assert.ok(
    NOT EXISTS (
      SELECT 1 FROM pg_policies
      WHERE schemaname = 'public'
        AND tablename IN ('scrape_uncertainty_review_requests', 'scrape_uncertainty_review_events')
    ),
    'browser policies are absent'
);
SELECT 'phase3g_check:no_browser_policies';

SELECT phase3g_assert.ok(
    NOT EXISTS (
      SELECT 1
      FROM (VALUES ('anon'), ('authenticated'), ('service_role')) AS roles(role_name)
      CROSS JOIN (VALUES
        ('public.scrape_uncertainty_review_requests'::regclass),
        ('public.scrape_uncertainty_review_events'::regclass)
      ) AS tables(table_oid)
      CROSS JOIN (VALUES
        ('SELECT'), ('INSERT'), ('UPDATE'), ('DELETE'),
        ('TRUNCATE'), ('REFERENCES'), ('TRIGGER'), ('MAINTAIN')
      ) AS privileges(privilege_name)
      WHERE has_table_privilege(roles.role_name, tables.table_oid, privileges.privilege_name)
        AND NOT (roles.role_name = 'service_role' AND privileges.privilege_name = 'SELECT')
    )
    AND has_table_privilege('service_role', 'public.scrape_uncertainty_review_requests', 'SELECT')
    AND has_table_privilege('service_role', 'public.scrape_uncertainty_review_events', 'SELECT')
    AND NOT EXISTS (
      SELECT 1
      FROM (VALUES ('anon'), ('authenticated'), ('service_role')) AS roles(role_name)
      CROSS JOIN (VALUES ('SELECT'), ('UPDATE'), ('USAGE')) AS privileges(privilege_name)
      WHERE has_sequence_privilege(
        roles.role_name,
        'public.scrape_uncertainty_review_events_event_id_seq',
        privileges.privilege_name
      )
    ),
    'table and sequence grants are exactly server-read-only'
);
SET ROLE service_role;
SELECT phase3g_assert.sqlstate(
  $$INSERT INTO public.scrape_uncertainty_review_requests (
       owner_user_id, client_request_id, failure_kind, start_period, end_period,
       force_rescrape, uncertainty_occurred_at, reason, request_payload_hash, expires_at
     ) VALUES (
       '11111111-1111-4111-8111-111111111111',
       '99999999-9999-4999-8999-999999999991', 'monitoring',
       '2026-01', '2026-01', FALSE, clock_timestamp(),
       'Direct mutation must remain unavailable to the service role.',
       repeat('a', 64), clock_timestamp() + interval '1 hour'
     )$$,
  '42501', 'service role direct insert is denied'
);
RESET ROLE;
SELECT 'phase3g_check:no_browser_table_grants';

SELECT phase3g_assert.ok(
    to_regprocedure('public.create_scrape_uncertainty_review(uuid,uuid,text,text,text,boolean,timestamptz,text,boolean,boolean)') IS NOT NULL
    AND to_regprocedure('public.get_scrape_uncertainty_review(uuid,uuid)') IS NOT NULL
    AND to_regprocedure('public.list_scrape_uncertainty_reviews(uuid,text,integer)') IS NOT NULL
    AND to_regprocedure('public.transition_scrape_uncertainty_review(uuid,uuid,integer,text,text)') IS NOT NULL
    AND has_function_privilege('service_role', 'public.create_scrape_uncertainty_review(uuid,uuid,text,text,text,boolean,timestamptz,text,boolean,boolean)', 'EXECUTE')
    AND has_function_privilege('service_role', 'public.get_scrape_uncertainty_review(uuid,uuid)', 'EXECUTE')
    AND has_function_privilege('service_role', 'public.list_scrape_uncertainty_reviews(uuid,text,integer)', 'EXECUTE')
    AND has_function_privilege('service_role', 'public.transition_scrape_uncertainty_review(uuid,uuid,integer,text,text)', 'EXECUTE')
    AND NOT has_function_privilege('anon', 'public.create_scrape_uncertainty_review(uuid,uuid,text,text,text,boolean,timestamptz,text,boolean,boolean)', 'EXECUTE')
    AND NOT has_function_privilege('anon', 'public.get_scrape_uncertainty_review(uuid,uuid)', 'EXECUTE')
    AND NOT has_function_privilege('anon', 'public.list_scrape_uncertainty_reviews(uuid,text,integer)', 'EXECUTE')
    AND NOT has_function_privilege('anon', 'public.transition_scrape_uncertainty_review(uuid,uuid,integer,text,text)', 'EXECUTE')
    AND NOT has_function_privilege('authenticated', 'public.create_scrape_uncertainty_review(uuid,uuid,text,text,text,boolean,timestamptz,text,boolean,boolean)', 'EXECUTE')
    AND NOT has_function_privilege('authenticated', 'public.get_scrape_uncertainty_review(uuid,uuid)', 'EXECUTE')
    AND NOT has_function_privilege('authenticated', 'public.list_scrape_uncertainty_reviews(uuid,text,integer)', 'EXECUTE')
    AND NOT has_function_privilege('authenticated', 'public.transition_scrape_uncertainty_review(uuid,uuid,integer,text,text)', 'EXECUTE'),
    'service RPC signatures and grants are exact'
);
SELECT 'phase3g_check:service_role_rpc_signatures';

SELECT phase3g_assert.ok(
    (SELECT count(*) = 4 AND bool_and(p.prosecdef)
     FROM pg_proc p JOIN pg_namespace n ON n.oid = p.pronamespace
     WHERE n.nspname = 'public'
       AND p.proname IN (
         'create_scrape_uncertainty_review', 'get_scrape_uncertainty_review',
         'list_scrape_uncertainty_reviews', 'transition_scrape_uncertainty_review'
       )),
    'public RPCs are security definer functions'
);
SELECT 'phase3g_check:rpc_security_definer';

SELECT phase3g_assert.ok(
    (SELECT count(*) = 4 AND bool_and(
       EXISTS (
         SELECT 1 FROM unnest(COALESCE(p.proconfig, ARRAY[]::TEXT[])) AS setting
         WHERE setting ~ '^search_path=public(, extensions)?$'
       )
     )
     FROM pg_proc p JOIN pg_namespace n ON n.oid = p.pronamespace
     WHERE n.nspname = 'public'
       AND p.proname IN (
         'create_scrape_uncertainty_review', 'get_scrape_uncertainty_review',
         'list_scrape_uncertainty_reviews', 'transition_scrape_uncertainty_review'
       )),
    'public RPC search paths are fixed'
);
SELECT 'phase3g_check:rpc_search_path_fixed';

SELECT phase3g_assert.ok(
    EXISTS (
      SELECT 1 FROM pg_trigger
      WHERE tgrelid = 'public.scrape_uncertainty_review_events'::regclass
        AND tgname = 'trg_scrape_uncertainty_events_immutable'
        AND tgenabled = 'O' AND NOT tgisinternal
    ),
    'immutable event trigger is enabled'
);
SELECT 'phase3g_check:immutable_event_trigger';

SELECT phase3g_assert.ok(
    (SELECT count(*) >= 4
     FROM pg_constraint
     WHERE conrelid = 'public.scrape_uncertainty_review_requests'::regclass
       AND contype = 'c'
       AND lower(pg_get_constraintdef(oid)) ~ '(review_only|authoritative|execution_enabled|lock_release_allowed)'),
    'review-only constraints are present'
);
SELECT 'phase3g_check:review_only_constraints';

SELECT phase3g_assert.ok(
    NOT EXISTS (
      SELECT 1 FROM pg_proc p JOIN pg_namespace n ON n.oid = p.pronamespace
      WHERE n.nspname = 'public'
        AND p.proname LIKE '%scrape_uncertainty%'
        AND p.proname ~ '(unlock|execute|consume|reserv)'
    ),
    'no executable authorization RPC exists'
);
SELECT 'phase3g_check:no_execution_rpc';

-- Browser roles cannot call RPCs, and a service-role call still needs an Admin actor.
SET ROLE authenticated;
SELECT phase3g_assert.sqlstate(
  $$SELECT * FROM public.create_scrape_uncertainty_review(
       '44444444-4444-4444-8444-444444444444',
       '99999999-9999-4999-8999-999999999992',
       'monitoring', '2026-01', '2026-01', FALSE, clock_timestamp(),
       'A browser role must never call this review function directly.', TRUE, TRUE
     )$$,
  '42501', 'authenticated cannot execute create RPC'
);
SELECT phase3g_assert.sqlstate(
  $$SELECT * FROM public.get_scrape_uncertainty_review(
       '44444444-4444-4444-8444-444444444444',
       '00000000-0000-4000-8000-000000000001'
     )$$,
  '42501', 'authenticated cannot execute get RPC'
);
SELECT phase3g_assert.sqlstate(
  $$SELECT * FROM public.list_scrape_uncertainty_reviews(
       '44444444-4444-4444-8444-444444444444', 'mine', 20
     )$$,
  '42501', 'authenticated cannot execute list RPC'
);
SELECT phase3g_assert.sqlstate(
  $$SELECT * FROM public.transition_scrape_uncertainty_review(
       '44444444-4444-4444-8444-444444444444',
       '00000000-0000-4000-8000-000000000001',
       1, 'reject', 'A browser role must never call this transition function directly.'
     )$$,
  '42501', 'authenticated cannot execute transition RPC'
);
SELECT phase3g_assert.sqlstate(
  $$UPDATE public.profiles SET role = 'admin'
    WHERE id = '44444444-4444-4444-8444-444444444444'$$,
  '42501', 'authenticated cannot update role'
);
UPDATE public.profiles SET full_name = 'Updated synthetic user'
WHERE id = '44444444-4444-4444-8444-444444444444';
RESET ROLE;
SELECT phase3g_assert.ok(
  (SELECT role = 'user' AND full_name = 'Updated synthetic user'
   FROM public.profiles WHERE id = '44444444-4444-4444-8444-444444444444'),
  'profile presentation update remains safe'
);

SET ROLE service_role;
SELECT phase3g_assert.sqlstate(
  $$SELECT * FROM public.create_scrape_uncertainty_review(
       '44444444-4444-4444-8444-444444444444',
       '99999999-9999-4999-8999-999999999993',
       'monitoring', '2026-01', '2026-01', FALSE, clock_timestamp(),
       'A non-admin actor must be rejected by the database boundary.', TRUE, TRUE
     )$$,
  '42501', 'non-admin actor is rejected'
);
RESET ROLE;

-- Concurrent creation performed by the Python runner remains idempotent.
SET ROLE service_role;
SELECT count(*) FROM public.create_scrape_uncertainty_review(
  '11111111-1111-4111-8111-111111111111',
  'aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaa1',
  'monitoring', '2026-01', '2026-01', FALSE,
  (SELECT occurred_at FROM phase3g_test.runtime_clock WHERE singleton),
  'Concurrent monitoring uncertainty requires independent review.', TRUE, TRUE
);
RESET ROLE;
SELECT phase3g_assert.ok(
  (SELECT count(*) = 1 FROM public.scrape_uncertainty_review_requests
   WHERE client_request_id = 'aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaa1')
  AND (SELECT count(*) = 1
       FROM public.scrape_uncertainty_review_events e
       JOIN public.scrape_uncertainty_review_requests r USING (review_id)
       WHERE r.client_request_id = 'aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaa1'),
  'idempotent retry creates one record and event'
);
SET ROLE service_role;
SELECT phase3g_assert.sqlstate(
  $$SELECT * FROM public.create_scrape_uncertainty_review(
       '11111111-1111-4111-8111-111111111111',
       'aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaa1',
       'monitoring', '2026-01', '2026-01', FALSE,
       (SELECT occurred_at FROM phase3g_test.runtime_clock WHERE singleton),
       'A conflicting reason must not reuse the same client request identifier.', TRUE, TRUE
     )$$,
  '23505', 'client request payload conflict is rejected'
);
SELECT phase3g_assert.sqlstate(
  $$SELECT * FROM public.create_scrape_uncertainty_review(
       '11111111-1111-4111-8111-111111111111',
       'aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaa2',
       'monitoring', '2026-01', '2026-01', FALSE,
       (SELECT occurred_at FROM phase3g_test.runtime_clock WHERE singleton),
       'Concurrent monitoring uncertainty requires independent review.', TRUE, TRUE
     )$$,
  '23505', 'active canonical payload conflict is rejected'
);
RESET ROLE;
SELECT 'phase3g_check:idempotent_create';

-- Self-decision is rejected.
SET ROLE service_role;
SELECT phase3g_assert.sqlstate(
  $$SELECT * FROM public.transition_scrape_uncertainty_review(
       '11111111-1111-4111-8111-111111111111',
       (SELECT review_id FROM public.scrape_uncertainty_review_requests
        WHERE client_request_id = 'aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaa1'),
       1, 'approve', 'The requester cannot approve the requester own uncertainty record.'
     )$$,
  '42501', 'requester self approval is rejected'
);
SELECT phase3g_assert.sqlstate(
  $$SELECT * FROM public.transition_scrape_uncertainty_review(
       '11111111-1111-4111-8111-111111111111',
       (SELECT review_id FROM public.scrape_uncertainty_review_requests
        WHERE client_request_id = 'aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaa1'),
       1, 'reject', 'The requester cannot reject the requester own uncertainty record.'
     )$$,
  '42501', 'requester self rejection is rejected'
);
RESET ROLE;
SELECT 'phase3g_check:self_approval_rejected';

-- Independent approval, rejection, stale CAS, requester revoke, terminal visibility.
SET ROLE service_role;
SELECT count(*) FROM public.create_scrape_uncertainty_review(
  '11111111-1111-4111-8111-111111111111',
  'bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbb1',
  'monitoring', '2026-02', '2026-02', FALSE, clock_timestamp(),
  'Approval flow requires an independent synthetic reviewer decision.', TRUE, TRUE
);
SELECT count(*) FROM public.create_scrape_uncertainty_review(
  '11111111-1111-4111-8111-111111111111',
  'cccccccc-cccc-4ccc-8ccc-ccccccccccc1',
  'client_stop', '2026-03', '2026-03', TRUE, clock_timestamp(),
  'Rejection flow requires an independent synthetic reviewer decision.', TRUE, TRUE
);
SELECT count(*) FROM public.transition_scrape_uncertainty_review(
  '22222222-2222-4222-8222-222222222222',
  (SELECT review_id FROM public.scrape_uncertainty_review_requests
   WHERE client_request_id = 'bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbb1'),
  1, 'approve', 'Independent reviewer approved the review-only synthetic request.'
);
SELECT count(*) FROM public.transition_scrape_uncertainty_review(
  '22222222-2222-4222-8222-222222222222',
  (SELECT review_id FROM public.scrape_uncertainty_review_requests
   WHERE client_request_id = 'cccccccc-cccc-4ccc-8ccc-ccccccccccc1'),
  1, 'reject', 'Independent reviewer rejected the review-only synthetic request.'
);
SELECT phase3g_assert.sqlstate(
  $$SELECT * FROM public.transition_scrape_uncertainty_review(
       '22222222-2222-4222-8222-222222222222',
       (SELECT review_id FROM public.scrape_uncertainty_review_requests
        WHERE client_request_id = 'bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbb1'),
       1, 'reject', 'A stale expected version must never overwrite the winning decision.'
     )$$,
  '40001', 'stale version is rejected'
);
RESET ROLE;
SELECT 'phase3g_check:cas_conflict_rejected';

SET ROLE service_role;
SELECT count(*) FROM public.transition_scrape_uncertainty_review(
  '11111111-1111-4111-8111-111111111111',
  (SELECT review_id FROM public.scrape_uncertainty_review_requests
   WHERE client_request_id = 'bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbb1'),
  2, 'revoke', 'Requester revoked the review-only approval without unlocking execution.'
);
SELECT phase3g_assert.ok(
  (SELECT count(*) = 0 FROM public.get_scrape_uncertainty_review(
    '33333333-3333-4333-8333-333333333333',
    (SELECT review_id FROM public.scrape_uncertainty_review_requests
     WHERE client_request_id = 'bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbb1'))),
  'unrelated admin cannot see terminal review'
);
RESET ROLE;

-- Expiry is materialized and excluded from reviewable results.
SET ROLE service_role;
SELECT count(*) FROM public.create_scrape_uncertainty_review(
  '11111111-1111-4111-8111-111111111111',
  'eeeeeeee-eeee-4eee-8eee-eeeeeeeeeee1',
  'monitoring', '2026-04', '2026-04', FALSE, clock_timestamp(),
  'Expiry flow validates materialization and reviewable list exclusion.', TRUE, TRUE
);
RESET ROLE;
UPDATE public.scrape_uncertainty_review_requests
SET requested_at = clock_timestamp() - interval '2 hours',
    expires_at = clock_timestamp() - interval '1 hour'
WHERE client_request_id = 'eeeeeeee-eeee-4eee-8eee-eeeeeeeeeee1';
SET ROLE service_role;
SELECT count(*) FROM public.list_scrape_uncertainty_reviews(
  '22222222-2222-4222-8222-222222222222', 'reviewable', 100
);
RESET ROLE;
SELECT phase3g_assert.ok(
  (SELECT status = 'expired' AND version = 2
   FROM public.scrape_uncertainty_review_requests
   WHERE client_request_id = 'eeeeeeee-eeee-4eee-8eee-eeeeeeeeeee1')
  AND (SELECT count(*) = 1
       FROM public.scrape_uncertainty_review_events e
       JOIN public.scrape_uncertainty_review_requests r USING (review_id)
       WHERE r.client_request_id = 'eeeeeeee-eeee-4eee-8eee-eeeeeeeeeee1'
         AND e.event_type = 'expired')
  AND NOT EXISTS (
       SELECT 1 FROM public.list_scrape_uncertainty_reviews(
         '22222222-2222-4222-8222-222222222222', 'reviewable', 100
       ) listed
       WHERE listed.client_request_id = 'eeeeeeee-eeee-4eee-8eee-eeeeeeeeeee1'),
  'expired review is materialized, audited, and excluded'
);
SELECT 'phase3g_check:expiry_materialized';

-- Event rows are append-only even to the table owner.
SELECT phase3g_assert.sqlstate(
  $$UPDATE public.scrape_uncertainty_review_events SET reason = reason
    WHERE event_id = (SELECT min(event_id) FROM public.scrape_uncertainty_review_events)$$,
  '55000', 'event update is rejected'
);
SELECT phase3g_assert.sqlstate(
  $$DELETE FROM public.scrape_uncertainty_review_events
    WHERE event_id = (SELECT min(event_id) FROM public.scrape_uncertainty_review_events)$$,
  '55000', 'event delete is rejected'
);
SELECT 'phase3g_check:immutable_event_mutation_rejected';

-- No row can become executable or release a lock.
SELECT phase3g_assert.sqlstate(
  $$UPDATE public.scrape_uncertainty_review_requests SET execution_enabled = TRUE
    WHERE client_request_id = 'aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaa1'$$,
  '23514', 'execution flag check constraint is enforced'
);
SELECT phase3g_assert.sqlstate(
  $$UPDATE public.scrape_uncertainty_review_requests SET lock_release_allowed = TRUE
    WHERE client_request_id = 'aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaa1'$$,
  '23514', 'lock release check constraint is enforced'
);
SELECT phase3g_assert.ok(
  NOT EXISTS (
    SELECT 1 FROM public.scrape_uncertainty_review_requests
    WHERE approval_scope <> 'review_only'
       OR authoritative IS DISTINCT FROM TRUE
       OR execution_enabled IS DISTINCT FROM FALSE
       OR lock_release_allowed IS DISTINCT FROM FALSE
  ),
  'all records remain review-only and non-executable'
);
SELECT 'phase3g_check:review_only_flags_enforced';

SELECT phase3g_assert.ok(
  NOT EXISTS (
    SELECT 1 FROM pg_proc p JOIN pg_namespace n ON n.oid = p.pronamespace
    WHERE n.nspname = 'public'
      AND p.proname LIKE '%scrape_uncertainty%'
      AND p.proname ~ '(unlock|execute|consume|reserv)'
  )
  AND NOT EXISTS (
    SELECT 1 FROM public.scrape_uncertainty_review_requests
    WHERE execution_enabled OR lock_release_allowed
  ),
  'no executable runtime capability was observed'
);
SELECT 'phase3g_check:no_execution_rpc_observed';
