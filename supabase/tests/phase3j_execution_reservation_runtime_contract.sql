\set ON_ERROR_STOP on

CREATE SCHEMA phase3j_assert AUTHORIZATION postgres;

CREATE OR REPLACE FUNCTION phase3j_assert.ok(p_condition BOOLEAN, p_label TEXT)
RETURNS VOID
LANGUAGE plpgsql
AS $$
BEGIN
    IF p_condition IS DISTINCT FROM TRUE THEN
        RAISE EXCEPTION 'phase3j assertion failed: %', p_label USING ERRCODE = 'P0001';
    END IF;
END;
$$;

CREATE OR REPLACE FUNCTION phase3j_assert.sqlstate(
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
        RAISE EXCEPTION 'phase3j assertion failed: % (unexpected SQLSTATE %)', p_label, SQLSTATE
            USING ERRCODE = 'P0001';
    END;
    RAISE EXCEPTION 'phase3j assertion failed: % (statement succeeded)', p_label
        USING ERRCODE = 'P0001';
END;
$$;

GRANT USAGE ON SCHEMA phase3j_assert TO anon, authenticated, service_role;
GRANT EXECUTE ON FUNCTION phase3j_assert.ok(BOOLEAN, TEXT)
    TO anon, authenticated, service_role;
GRANT EXECUTE ON FUNCTION phase3j_assert.sqlstate(TEXT, TEXT, TEXT)
    TO anon, authenticated, service_role;

-- Catalog and privilege boundary.
SELECT phase3j_assert.ok(
  to_regclass('public.scrape_execution_authorizations') IS NOT NULL,
  'authorization table is present'
);
SELECT 'phase3j_check:authorization_table_present';

SELECT phase3j_assert.ok(
  to_regclass('public.scrape_execution_reservations') IS NOT NULL,
  'reservation table is present'
);
SELECT 'phase3j_check:reservation_table_present';

SELECT phase3j_assert.ok(
  to_regclass('public.scrape_execution_reservation_events') IS NOT NULL,
  'reservation event table is present'
);
SELECT 'phase3j_check:event_table_present';

SELECT phase3j_assert.ok(
  (SELECT count(*) = 3 AND bool_and(c.relrowsecurity)
   FROM pg_class AS c
   WHERE c.oid IN (
     'public.scrape_execution_authorizations'::regclass,
     'public.scrape_execution_reservations'::regclass,
     'public.scrape_execution_reservation_events'::regclass
   )),
  'RLS is enabled on all execution capability tables'
);
SELECT 'phase3j_check:rls_enabled';

SELECT phase3j_assert.ok(
  NOT EXISTS (
    SELECT 1 FROM pg_policies
    WHERE schemaname = 'public'
      AND tablename IN (
        'scrape_execution_authorizations',
        'scrape_execution_reservations',
        'scrape_execution_reservation_events'
      )
  ),
  'browser policies are absent'
);
SELECT 'phase3j_check:no_browser_policies';

SELECT phase3j_assert.ok(
  NOT EXISTS (
    SELECT 1
    FROM (VALUES ('anon'), ('authenticated'), ('service_role')) AS roles(role_name)
    CROSS JOIN (VALUES
      ('public.scrape_execution_authorizations'::regclass),
      ('public.scrape_execution_reservations'::regclass),
      ('public.scrape_execution_reservation_events'::regclass)
    ) AS tables(table_oid)
    CROSS JOIN (VALUES
      ('SELECT'), ('INSERT'), ('UPDATE'), ('DELETE'),
      ('TRUNCATE'), ('REFERENCES'), ('TRIGGER'), ('MAINTAIN')
    ) AS privileges(privilege_name)
    WHERE has_table_privilege(roles.role_name, tables.table_oid, privileges.privilege_name)
      AND NOT (roles.role_name = 'service_role' AND privileges.privilege_name = 'SELECT')
  )
  AND has_table_privilege('service_role', 'public.scrape_execution_authorizations', 'SELECT')
  AND has_table_privilege('service_role', 'public.scrape_execution_reservations', 'SELECT')
  AND has_table_privilege('service_role', 'public.scrape_execution_reservation_events', 'SELECT')
  AND NOT EXISTS (
    SELECT 1
    FROM (VALUES ('anon'), ('authenticated'), ('service_role')) AS roles(role_name)
    CROSS JOIN (VALUES ('SELECT'), ('UPDATE'), ('USAGE')) AS privileges(privilege_name)
    WHERE has_sequence_privilege(
      roles.role_name,
      'public.scrape_execution_reservation_fencing_seq',
      privileges.privilege_name
    ) OR has_sequence_privilege(
      roles.role_name,
      'public.scrape_execution_reservation_events_event_id_seq',
      privileges.privilege_name
    )
  ),
  'tables and sequences are server-read-only outside definer RPCs'
);

SET ROLE service_role;
SELECT phase3j_assert.sqlstate(
  $$INSERT INTO public.scrape_execution_authorizations (
      authorization_id, operation_id, job_id, review_id, review_version,
      owner_user_id, authorized_by_user_id, review_payload_hash,
      execution_request_hash, authorization_binding_hash, authorization_expires_at
    ) VALUES (
      '31300000-0000-4000-8000-000000000099',
      '31100000-0000-4000-8000-000000000099',
      '31200000-0000-4000-8000-000000000099',
      (SELECT review_id FROM public.scrape_uncertainty_review_requests
       WHERE client_request_id = '31000000-0000-4000-8000-000000000001'),
      2, '11111111-1111-4111-8111-111111111111',
      '22222222-2222-4222-8222-222222222222', repeat('a', 64), repeat('b', 64),
      repeat('c', 64), clock_timestamp() + interval '1 minute'
    )$$,
  '42501', 'service role cannot directly create authorization'
);
SELECT phase3j_assert.sqlstate(
  $$INSERT INTO public.scrape_execution_reservations (
      reservation_id, authorization_id, operation_id, job_id, review_id,
      review_version, owner_user_id, execution_request_hash,
      authorization_binding_hash, authorization_version, requested_ttl_seconds,
      fencing_token, expires_at
    ) VALUES (
      '31400000-0000-4000-8000-000000000099',
      '31300000-0000-4000-8000-000000000002',
      '31100000-0000-4000-8000-000000000099',
      '31200000-0000-4000-8000-000000000099',
      (SELECT review_id FROM public.scrape_uncertainty_review_requests
       WHERE client_request_id = '31000000-0000-4000-8000-000000000002'),
      2, '11111111-1111-4111-8111-111111111111', repeat('2', 64), repeat('c', 64),
      1, 60, 999, clock_timestamp() + interval '1 minute'
    )$$,
  '42501', 'service role cannot directly create reservation'
);
RESET ROLE;
SELECT 'phase3j_check:server_read_only_tables';

SELECT phase3j_assert.ok(
  to_regprocedure('public.reserve_scrape_execution(uuid,uuid,uuid,uuid,uuid,integer,uuid,text,integer,integer)') IS NOT NULL
  AND to_regprocedure('public.consume_scrape_execution_reservation(uuid,integer,uuid,uuid,uuid,uuid,integer,uuid,text)') IS NOT NULL
  AND to_regprocedure('public.release_scrape_execution_reservation(uuid,integer,uuid,uuid,uuid,text,text)') IS NOT NULL
  AND to_regprocedure('public.expire_scrape_execution_reservation(uuid,integer)') IS NOT NULL
  AND has_function_privilege('service_role', 'public.reserve_scrape_execution(uuid,uuid,uuid,uuid,uuid,integer,uuid,text,integer,integer)', 'EXECUTE')
  AND has_function_privilege('service_role', 'public.consume_scrape_execution_reservation(uuid,integer,uuid,uuid,uuid,uuid,integer,uuid,text)', 'EXECUTE')
  AND has_function_privilege('service_role', 'public.release_scrape_execution_reservation(uuid,integer,uuid,uuid,uuid,text,text)', 'EXECUTE')
  AND has_function_privilege('service_role', 'public.expire_scrape_execution_reservation(uuid,integer)', 'EXECUTE')
  AND NOT has_function_privilege('anon', 'public.reserve_scrape_execution(uuid,uuid,uuid,uuid,uuid,integer,uuid,text,integer,integer)', 'EXECUTE')
  AND NOT has_function_privilege('authenticated', 'public.reserve_scrape_execution(uuid,uuid,uuid,uuid,uuid,integer,uuid,text,integer,integer)', 'EXECUTE')
  AND NOT has_function_privilege('anon', 'public.consume_scrape_execution_reservation(uuid,integer,uuid,uuid,uuid,uuid,integer,uuid,text)', 'EXECUTE')
  AND NOT has_function_privilege('authenticated', 'public.consume_scrape_execution_reservation(uuid,integer,uuid,uuid,uuid,uuid,integer,uuid,text)', 'EXECUTE')
  AND NOT has_function_privilege('anon', 'public.release_scrape_execution_reservation(uuid,integer,uuid,uuid,uuid,text,text)', 'EXECUTE')
  AND NOT has_function_privilege('authenticated', 'public.release_scrape_execution_reservation(uuid,integer,uuid,uuid,uuid,text,text)', 'EXECUTE')
  AND NOT has_function_privilege('anon', 'public.expire_scrape_execution_reservation(uuid,integer)', 'EXECUTE')
  AND NOT has_function_privilege('authenticated', 'public.expire_scrape_execution_reservation(uuid,integer)', 'EXECUTE'),
  'reservation RPC signatures and grants are exact'
);
SELECT 'phase3j_check:reservation_rpc_signatures';

SELECT phase3j_assert.ok(
  (SELECT count(*) = 8 AND bool_and(p.prosecdef)
   FROM pg_proc AS p JOIN pg_namespace AS n ON n.oid = p.pronamespace
   WHERE n.nspname = 'public'
     AND p.proname IN (
       '_validate_scrape_execution_authorization_insert',
       '_reject_scrape_execution_authorization_mutation',
       '_reject_scrape_execution_reservation_event_mutation',
       '_materialize_scrape_execution_reservation_expiry',
       'reserve_scrape_execution',
       'consume_scrape_execution_reservation',
       'release_scrape_execution_reservation',
       'expire_scrape_execution_reservation'
     )),
  'mutation functions are security definer'
);
SELECT 'phase3j_check:rpc_security_definer';

SELECT phase3j_assert.ok(
  (SELECT count(*) = 9 AND bool_and(
     EXISTS (
       SELECT 1 FROM unnest(COALESCE(p.proconfig, ARRAY[]::TEXT[])) AS setting
       WHERE setting ~ '^search_path=public(, extensions)?$'
     )
   )
   FROM pg_proc AS p JOIN pg_namespace AS n ON n.oid = p.pronamespace
   WHERE n.nspname = 'public'
     AND p.proname IN (
       '_scrape_execution_binding_hash',
       '_validate_scrape_execution_authorization_insert',
       '_reject_scrape_execution_authorization_mutation',
       '_reject_scrape_execution_reservation_event_mutation',
       '_materialize_scrape_execution_reservation_expiry',
       'reserve_scrape_execution',
       'consume_scrape_execution_reservation',
       'release_scrape_execution_reservation',
       'expire_scrape_execution_reservation'
     )),
  'all helper and RPC search paths are fixed'
);
SELECT 'phase3j_check:rpc_search_path_fixed';

SELECT phase3j_assert.ok(
  EXISTS (
    SELECT 1 FROM pg_trigger
    WHERE tgrelid = 'public.scrape_execution_reservation_events'::regclass
      AND tgname = 'trg_scrape_execution_reservation_events_immutable'
      AND tgenabled = 'O' AND NOT tgisinternal
  ),
  'append-only event trigger is enabled'
);
SELECT 'phase3j_check:append_only_events';

SELECT phase3j_assert.ok(
  NOT EXISTS (
    SELECT 1 FROM pg_proc AS p JOIN pg_namespace AS n ON n.oid = p.pronamespace
    WHERE n.nspname = 'public'
      AND p.proname ~ '(create|update|revoke).*scrape_execution_authoriz'
  )
  AND EXISTS (
    SELECT 1 FROM pg_trigger
    WHERE tgrelid = 'public.scrape_execution_authorizations'::regclass
      AND tgname = 'trg_scrape_execution_authorizations_immutable'
      AND tgenabled = 'O' AND NOT tgisinternal
  ),
  'execution authorization is bootstrap-only and immutable'
);
SELECT 'phase3j_check:authorization_bootstrap_only';

-- Review approval by itself never grants execution, and requester self-authorization fails.
SET ROLE service_role;
SELECT phase3j_assert.sqlstate(
  $$SELECT * FROM public.reserve_scrape_execution(
      '31300000-0000-4000-8000-000000000001',
      '31400000-0000-4000-8000-000000000001',
      '31100000-0000-4000-8000-000000000001',
      '31200000-0000-4000-8000-000000000001',
      (SELECT review_id FROM public.scrape_uncertainty_review_requests
       WHERE client_request_id = '31000000-0000-4000-8000-000000000001'),
      2, '11111111-1111-4111-8111-111111111111', repeat('1', 64), 1, 60
    )$$,
  'P0002', 'approved review without explicit authorization is denied'
);
RESET ROLE;

SELECT phase3j_assert.sqlstate(
  $$INSERT INTO public.scrape_execution_authorizations (
      authorization_id, operation_id, job_id, review_id, review_version,
      owner_user_id, authorized_by_user_id, review_payload_hash,
      execution_request_hash, authorization_expires_at
    ) SELECT
      '31300000-0000-4000-8000-000000000001',
      '31100000-0000-4000-8000-000000000001',
      '31200000-0000-4000-8000-000000000001',
      r.review_id, r.version, r.owner_user_id, r.owner_user_id,
      r.request_payload_hash, repeat('1', 64),
      LEAST(clock_timestamp() + interval '1 minute', r.expires_at)
    FROM public.scrape_uncertainty_review_requests AS r
    WHERE r.client_request_id = '31000000-0000-4000-8000-000000000001'$$,
  '42501', 'requester self-authorization is denied'
);
SELECT 'phase3j_check:postgres_approved_review_only_denied';

-- Reserve is exact-payload idempotent; different payload or identifier conflicts.
SET ROLE service_role;
SELECT count(*) FROM public.reserve_scrape_execution(
  '31300000-0000-4000-8000-000000000002',
  '31400000-0000-4000-8000-000000000002',
  '31100000-0000-4000-8000-000000000002',
  '31200000-0000-4000-8000-000000000002',
  (SELECT review_id FROM public.scrape_uncertainty_review_requests
   WHERE client_request_id = '31000000-0000-4000-8000-000000000002'),
  2, '11111111-1111-4111-8111-111111111111', repeat('2', 64), 1, 120
);
SELECT count(*) FROM public.reserve_scrape_execution(
  '31300000-0000-4000-8000-000000000002',
  '31400000-0000-4000-8000-000000000002',
  '31100000-0000-4000-8000-000000000002',
  '31200000-0000-4000-8000-000000000002',
  (SELECT review_id FROM public.scrape_uncertainty_review_requests
   WHERE client_request_id = '31000000-0000-4000-8000-000000000002'),
  2, '11111111-1111-4111-8111-111111111111', repeat('2', 64), 1, 120
);
SELECT phase3j_assert.sqlstate(
  $$SELECT * FROM public.reserve_scrape_execution(
      '31300000-0000-4000-8000-000000000002',
      '31400000-0000-4000-8000-000000000002',
      '31100000-0000-4000-8000-000000000002',
      '31200000-0000-4000-8000-000000000002',
      (SELECT review_id FROM public.scrape_uncertainty_review_requests
       WHERE client_request_id = '31000000-0000-4000-8000-000000000002'),
      2, '11111111-1111-4111-8111-111111111111', repeat('9', 64), 1, 120
    )$$,
  '23505', 'same reservation id with different payload conflicts'
);
SELECT phase3j_assert.sqlstate(
  $$SELECT * FROM public.reserve_scrape_execution(
      '31300000-0000-4000-8000-000000000002',
      '31400000-0000-4000-8000-000000000012',
      '31100000-0000-4000-8000-000000000002',
      '31200000-0000-4000-8000-000000000002',
      (SELECT review_id FROM public.scrape_uncertainty_review_requests
       WHERE client_request_id = '31000000-0000-4000-8000-000000000002'),
      2, '11111111-1111-4111-8111-111111111111', repeat('2', 64), 1, 120
    )$$,
  '23505', 'same authorization with different reservation id conflicts'
);
RESET ROLE;
SELECT phase3j_assert.ok(
  (SELECT count(*) = 1 AND min(status) = 'reserved' AND min(version) = 1
   FROM public.scrape_execution_reservations
   WHERE authorization_id = '31300000-0000-4000-8000-000000000002')
  AND (SELECT count(*) = 1 FROM public.scrape_execution_reservation_events
       WHERE reservation_id = '31400000-0000-4000-8000-000000000002'
         AND event_type = 'reserved'),
  'reserve replay creates one reservation and event'
);

-- Two disposable PostgreSQL sessions issue the same reserve concurrently.
-- One session deliberately holds the operation advisory lock briefly; both
-- calls must converge on one durable reservation and one reserved event.
CREATE EXTENSION IF NOT EXISTS dblink;
SELECT phase3j_assert.ok(
  dblink_connect(
    'phase3j_reserve_a',
    'host=/var/run/postgresql dbname=phase3j_runtime user=postgres'
  ) = 'OK',
  'first disposable concurrency connection opened'
);
SELECT phase3j_assert.ok(
  dblink_connect(
    'phase3j_reserve_b',
    'host=/var/run/postgresql dbname=phase3j_runtime user=postgres'
  ) = 'OK',
  'second disposable concurrency connection opened'
);
SELECT phase3j_assert.ok(
  dblink_send_query(
    'phase3j_reserve_a',
    $concurrent_a$
      WITH lock_first AS MATERIALIZED (
        SELECT pg_advisory_xact_lock(
                 hashtextextended('31300000-0000-4000-8000-000000000006', 0)
               ),
               pg_sleep(1)
      )
      SELECT count(*)
      FROM lock_first
      CROSS JOIN LATERAL public.reserve_scrape_execution(
        '31300000-0000-4000-8000-000000000006',
        '31400000-0000-4000-8000-000000000006',
        '31100000-0000-4000-8000-000000000006',
        '31200000-0000-4000-8000-000000000006',
        (SELECT review_id FROM public.scrape_uncertainty_review_requests
         WHERE client_request_id = '31000000-0000-4000-8000-000000000006'),
        2, '11111111-1111-4111-8111-111111111111', repeat('6', 64), 1, 120
      )
    $concurrent_a$
  ) = 1,
  'first concurrent reserve dispatched'
);
SELECT pg_sleep(0.1);
SELECT phase3j_assert.ok(
  dblink_send_query(
    'phase3j_reserve_b',
    $concurrent_b$
      SELECT count(*) FROM public.reserve_scrape_execution(
        '31300000-0000-4000-8000-000000000006',
        '31400000-0000-4000-8000-000000000006',
        '31100000-0000-4000-8000-000000000006',
        '31200000-0000-4000-8000-000000000006',
        (SELECT review_id FROM public.scrape_uncertainty_review_requests
         WHERE client_request_id = '31000000-0000-4000-8000-000000000006'),
        2, '11111111-1111-4111-8111-111111111111', repeat('6', 64), 1, 120
      )
    $concurrent_b$
  ) = 1,
  'second concurrent reserve dispatched'
);
SELECT phase3j_assert.ok(
  (SELECT result_count = 1
   FROM dblink_get_result('phase3j_reserve_a') AS result(result_count BIGINT)),
  'first concurrent reserve returned one row'
);
SELECT phase3j_assert.ok(
  (SELECT result_count = 1
   FROM dblink_get_result('phase3j_reserve_b') AS result(result_count BIGINT)),
  'second concurrent reserve returned one row'
);
SELECT phase3j_assert.ok(dblink_disconnect('phase3j_reserve_a') = 'OK', 'first connection closed');
SELECT phase3j_assert.ok(dblink_disconnect('phase3j_reserve_b') = 'OK', 'second connection closed');
SELECT phase3j_assert.ok(
  (SELECT count(*) = 1 FROM public.scrape_execution_reservations
   WHERE reservation_id = '31400000-0000-4000-8000-000000000006')
  AND (SELECT count(*) = 1 FROM public.scrape_execution_reservation_events
       WHERE reservation_id = '31400000-0000-4000-8000-000000000006'
         AND event_type = 'reserved'),
  'concurrent reserve replay serialized to one row and one event'
);
SELECT 'phase3j_check:postgres_reservation_replay';

-- Consume uses CAS, is single-use, and returns one stable receipt on replay.
SET ROLE service_role;
SELECT phase3j_assert.sqlstate(
  $$SELECT * FROM public.consume_scrape_execution_reservation(
      '31400000-0000-4000-8000-000000000002', 2,
      '31500000-0000-4000-8000-000000000002',
      '31100000-0000-4000-8000-000000000002',
      '31200000-0000-4000-8000-000000000002',
      (SELECT review_id FROM public.scrape_uncertainty_review_requests
       WHERE client_request_id = '31000000-0000-4000-8000-000000000002'),
      2, '11111111-1111-4111-8111-111111111111', repeat('2', 64)
    )$$,
  '40001', 'stale consume CAS is rejected'
);
SELECT count(*) FROM public.consume_scrape_execution_reservation(
  '31400000-0000-4000-8000-000000000002', 1,
  '31500000-0000-4000-8000-000000000002',
  '31100000-0000-4000-8000-000000000002',
  '31200000-0000-4000-8000-000000000002',
  (SELECT review_id FROM public.scrape_uncertainty_review_requests
   WHERE client_request_id = '31000000-0000-4000-8000-000000000002'),
  2, '11111111-1111-4111-8111-111111111111', repeat('2', 64)
);
SELECT count(*) FROM public.consume_scrape_execution_reservation(
  '31400000-0000-4000-8000-000000000002', 1,
  '31500000-0000-4000-8000-000000000002',
  '31100000-0000-4000-8000-000000000002',
  '31200000-0000-4000-8000-000000000002',
  (SELECT review_id FROM public.scrape_uncertainty_review_requests
   WHERE client_request_id = '31000000-0000-4000-8000-000000000002'),
  2, '11111111-1111-4111-8111-111111111111', repeat('2', 64)
);
SELECT phase3j_assert.sqlstate(
  $$SELECT * FROM public.consume_scrape_execution_reservation(
      '31400000-0000-4000-8000-000000000002', 2,
      '31500000-0000-4000-8000-000000000012',
      '31100000-0000-4000-8000-000000000002',
      '31200000-0000-4000-8000-000000000002',
      (SELECT review_id FROM public.scrape_uncertainty_review_requests
       WHERE client_request_id = '31000000-0000-4000-8000-000000000002'),
      2, '11111111-1111-4111-8111-111111111111', repeat('2', 64)
    )$$,
  '23505', 'second consume identifier is rejected'
);
RESET ROLE;
SELECT phase3j_assert.ok(
  (SELECT status = 'consumed' AND version = 2
          AND consume_receipt_hash ~ '^[0-9a-f]{64}$'
   FROM public.scrape_execution_reservations
   WHERE reservation_id = '31400000-0000-4000-8000-000000000002')
  AND (SELECT count(*) = 1 AND count(DISTINCT consume_receipt_hash) = 1
       FROM public.scrape_execution_reservation_events
       WHERE reservation_id = '31400000-0000-4000-8000-000000000002'
         AND event_type = 'consumed'),
  'consume is CAS guarded, idempotent, and single-use'
);
SELECT 'phase3j_check:postgres_consume_cas';

-- Release is idempotent and blocks later consumption.
SET ROLE service_role;
SELECT count(*) FROM public.reserve_scrape_execution(
  '31300000-0000-4000-8000-000000000003',
  '31400000-0000-4000-8000-000000000003',
  '31100000-0000-4000-8000-000000000003',
  '31200000-0000-4000-8000-000000000003',
  (SELECT review_id FROM public.scrape_uncertainty_review_requests
   WHERE client_request_id = '31000000-0000-4000-8000-000000000003'),
  2, '11111111-1111-4111-8111-111111111111', repeat('3', 64), 1, 120
);
SELECT phase3j_assert.sqlstate(
  $$SELECT * FROM public.release_scrape_execution_reservation(
      '31400000-0000-4000-8000-000000000003', 2,
      '31600000-0000-4000-8000-000000000003',
      '31100000-0000-4000-8000-000000000003',
      '31200000-0000-4000-8000-000000000003', repeat('3', 64),
      'A stale release version must never overwrite the reserved capability.'
    )$$,
  '40001', 'stale release CAS is rejected'
);
SELECT count(*) FROM public.release_scrape_execution_reservation(
  '31400000-0000-4000-8000-000000000003', 1,
  '31600000-0000-4000-8000-000000000003',
  '31100000-0000-4000-8000-000000000003',
  '31200000-0000-4000-8000-000000000003', repeat('3', 64),
  'Release replay confirms compensation without dispatching any worker.'
);
SELECT count(*) FROM public.release_scrape_execution_reservation(
  '31400000-0000-4000-8000-000000000003', 1,
  '31600000-0000-4000-8000-000000000003',
  '31100000-0000-4000-8000-000000000003',
  '31200000-0000-4000-8000-000000000003', repeat('3', 64),
  'Release replay confirms compensation without dispatching any worker.'
);
SELECT phase3j_assert.sqlstate(
  $$SELECT * FROM public.consume_scrape_execution_reservation(
      '31400000-0000-4000-8000-000000000003', 2,
      '31500000-0000-4000-8000-000000000003',
      '31100000-0000-4000-8000-000000000003',
      '31200000-0000-4000-8000-000000000003',
      (SELECT review_id FROM public.scrape_uncertainty_review_requests
       WHERE client_request_id = '31000000-0000-4000-8000-000000000003'),
      2, '11111111-1111-4111-8111-111111111111', repeat('3', 64)
    )$$,
  '55000', 'released reservation cannot be consumed'
);
RESET ROLE;
SELECT phase3j_assert.ok(
  (SELECT status = 'released' AND version = 2
   FROM public.scrape_execution_reservations
   WHERE reservation_id = '31400000-0000-4000-8000-000000000003')
  AND (SELECT count(*) = 1 FROM public.scrape_execution_reservation_events
       WHERE reservation_id = '31400000-0000-4000-8000-000000000003'
         AND event_type = 'released'),
  'release replay emits one terminal event'
);
SELECT 'phase3j_check:postgres_release_replay';

-- Expiry is explicit and fencing tokens remain strictly monotonic.
SET ROLE service_role;
SELECT count(*) FROM public.reserve_scrape_execution(
  '31300000-0000-4000-8000-000000000004',
  '31400000-0000-4000-8000-000000000004',
  '31100000-0000-4000-8000-000000000004',
  '31200000-0000-4000-8000-000000000004',
  (SELECT review_id FROM public.scrape_uncertainty_review_requests
   WHERE client_request_id = '31000000-0000-4000-8000-000000000004'),
  2, '11111111-1111-4111-8111-111111111111', repeat('4', 64), 1, 60
);
RESET ROLE;

-- Disposable-clock injection: only the database owner can mutate this row;
-- service_role remains unable to bypass the lifecycle RPCs.
UPDATE public.scrape_execution_reservations
SET reserved_at = clock_timestamp() - interval '2 minutes',
    expires_at = clock_timestamp() - interval '1 minute'
WHERE reservation_id = '31400000-0000-4000-8000-000000000004';

SET ROLE service_role;
SELECT count(*) FROM public.expire_scrape_execution_reservation(
  '31400000-0000-4000-8000-000000000004', 1
);
SELECT count(*) FROM public.expire_scrape_execution_reservation(
  '31400000-0000-4000-8000-000000000004', 1
);
SELECT count(*) FROM public.reserve_scrape_execution(
  '31300000-0000-4000-8000-000000000005',
  '31400000-0000-4000-8000-000000000005',
  '31100000-0000-4000-8000-000000000005',
  '31200000-0000-4000-8000-000000000005',
  (SELECT review_id FROM public.scrape_uncertainty_review_requests
   WHERE client_request_id = '31000000-0000-4000-8000-000000000005'),
  2, '11111111-1111-4111-8111-111111111111', repeat('5', 64), 1, 120
);
RESET ROLE;

SELECT phase3j_assert.ok(
  (SELECT status = 'expired' AND version = 2 AND expired_at IS NOT NULL
   FROM public.scrape_execution_reservations
   WHERE reservation_id = '31400000-0000-4000-8000-000000000004')
  AND (SELECT count(*) = 1 FROM public.scrape_execution_reservation_events
       WHERE reservation_id = '31400000-0000-4000-8000-000000000004'
         AND event_type = 'expired')
  AND (SELECT later.fencing_token > earlier.fencing_token
       FROM public.scrape_execution_reservations AS earlier
       CROSS JOIN public.scrape_execution_reservations AS later
       WHERE earlier.reservation_id = '31400000-0000-4000-8000-000000000004'
         AND later.reservation_id = '31400000-0000-4000-8000-000000000005'),
  'expiry is audited and fencing token is monotonic'
);

SELECT phase3j_assert.sqlstate(
  $$UPDATE public.scrape_execution_reservation_events SET reason = reason
    WHERE event_id = (SELECT min(event_id) FROM public.scrape_execution_reservation_events)$$,
  '55000', 'reservation event update is rejected'
);
SELECT phase3j_assert.sqlstate(
  $$DELETE FROM public.scrape_execution_reservation_events
    WHERE event_id = (SELECT min(event_id) FROM public.scrape_execution_reservation_events)$$,
  '55000', 'reservation event delete is rejected'
);
SELECT phase3j_assert.sqlstate(
  $$UPDATE public.scrape_execution_authorizations SET authorization_status = authorization_status
    WHERE authorization_id = '31300000-0000-4000-8000-000000000002'$$,
  '55000', 'authorization update is rejected'
);

SELECT phase3j_assert.ok(
  NOT EXISTS (
    SELECT 1 FROM public.scrape_uncertainty_review_requests
    WHERE client_request_id BETWEEN
      '31000000-0000-4000-8000-000000000001'::UUID AND
      '31000000-0000-4000-8000-000000000005'::UUID
      AND (status <> 'approved' OR version <> 2
           OR execution_enabled IS DISTINCT FROM FALSE
           OR lock_release_allowed IS DISTINCT FROM FALSE)
  )
  AND NOT EXISTS (
    SELECT 1 FROM pg_proc AS p JOIN pg_namespace AS n ON n.oid = p.pronamespace
    WHERE n.nspname = 'public'
      AND p.proname ~ '(unlock|dispatch).*scrape'
  ),
  'reservation lifecycle does not mutate review, unlock, or dispatch'
);
SELECT 'phase3j_check:postgres_expiry_fencing';
