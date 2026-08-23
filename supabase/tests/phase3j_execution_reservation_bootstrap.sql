\set ON_ERROR_STOP on

-- This bootstrap is loaded only into the disposable Phase 3J PostgreSQL
-- container after the Phase 3G bootstrap and both review/reservation migrations.
-- Review RPCs create independently approved review evidence. Explicit execution
-- authorization is then inserted directly by the database owner: no application
-- authorization-creation RPC exists in Phase 3J.

SET ROLE service_role;

SELECT count(*) FROM public.create_scrape_uncertainty_review(
  '11111111-1111-4111-8111-111111111111',
  '31000000-0000-4000-8000-000000000001',
  'monitoring', '2026-05', '2026-05', FALSE, clock_timestamp(),
  'Approved review deliberately has no explicit execution authorization.', TRUE, TRUE
);
SELECT count(*) FROM public.transition_scrape_uncertainty_review(
  '22222222-2222-4222-8222-222222222222',
  (SELECT review_id FROM public.scrape_uncertainty_review_requests
   WHERE client_request_id = '31000000-0000-4000-8000-000000000001'),
  1, 'approve', 'Independent reviewer approved review evidence without execution authority.'
);

SELECT count(*) FROM public.create_scrape_uncertainty_review(
  '11111111-1111-4111-8111-111111111111',
  '31000000-0000-4000-8000-000000000002',
  'monitoring', '2026-06', '2026-06', FALSE, clock_timestamp(),
  'Primary reservation and consume contract uses independent review evidence.', TRUE, TRUE
);
SELECT count(*) FROM public.transition_scrape_uncertainty_review(
  '22222222-2222-4222-8222-222222222222',
  (SELECT review_id FROM public.scrape_uncertainty_review_requests
   WHERE client_request_id = '31000000-0000-4000-8000-000000000002'),
  1, 'approve', 'Independent reviewer approved the primary synthetic review evidence.'
);

SELECT count(*) FROM public.create_scrape_uncertainty_review(
  '11111111-1111-4111-8111-111111111111',
  '31000000-0000-4000-8000-000000000003',
  'client_stop', '2026-07', '2026-07', TRUE, clock_timestamp(),
  'Release replay contract uses a separate independently reviewed operation.', TRUE, TRUE
);
SELECT count(*) FROM public.transition_scrape_uncertainty_review(
  '22222222-2222-4222-8222-222222222222',
  (SELECT review_id FROM public.scrape_uncertainty_review_requests
   WHERE client_request_id = '31000000-0000-4000-8000-000000000003'),
  1, 'approve', 'Independent reviewer approved the release replay review evidence.'
);

SELECT count(*) FROM public.create_scrape_uncertainty_review(
  '11111111-1111-4111-8111-111111111111',
  '31000000-0000-4000-8000-000000000004',
  'monitoring', '2026-08', '2026-08', FALSE, clock_timestamp(),
  'Expiry contract uses a short reservation under independent review evidence.', TRUE, TRUE
);
SELECT count(*) FROM public.transition_scrape_uncertainty_review(
  '22222222-2222-4222-8222-222222222222',
  (SELECT review_id FROM public.scrape_uncertainty_review_requests
   WHERE client_request_id = '31000000-0000-4000-8000-000000000004'),
  1, 'approve', 'Independent reviewer approved the expiry contract review evidence.'
);

SELECT count(*) FROM public.create_scrape_uncertainty_review(
  '11111111-1111-4111-8111-111111111111',
  '31000000-0000-4000-8000-000000000005',
  'monitoring', '2026-09', '2026-09', FALSE, clock_timestamp(),
  'Fencing monotonicity contract uses a second independently reviewed operation.', TRUE, TRUE
);
SELECT count(*) FROM public.transition_scrape_uncertainty_review(
  '22222222-2222-4222-8222-222222222222',
  (SELECT review_id FROM public.scrape_uncertainty_review_requests
   WHERE client_request_id = '31000000-0000-4000-8000-000000000005'),
  1, 'approve', 'Independent reviewer approved the fencing contract review evidence.'
);

SELECT count(*) FROM public.create_scrape_uncertainty_review(
  '11111111-1111-4111-8111-111111111111',
  '31000000-0000-4000-8000-000000000006',
  'monitoring', '2026-10', '2026-10', FALSE, clock_timestamp(),
  'Concurrent reservation replay uses another independently reviewed operation.', TRUE, TRUE
);
SELECT count(*) FROM public.transition_scrape_uncertainty_review(
  '22222222-2222-4222-8222-222222222222',
  (SELECT review_id FROM public.scrape_uncertainty_review_requests
   WHERE client_request_id = '31000000-0000-4000-8000-000000000006'),
  1, 'approve', 'Independent reviewer approved concurrent reservation review evidence.'
);

RESET ROLE;

INSERT INTO public.scrape_execution_authorizations (
  authorization_id, operation_id, job_id, review_id, review_version,
  owner_user_id, authorized_by_user_id, review_payload_hash,
  execution_request_hash, authorization_expires_at
)
SELECT
  seed.authorization_id, seed.operation_id, seed.job_id,
  review.review_id, review.version, review.owner_user_id, review.decided_by,
  review.request_payload_hash, seed.execution_request_hash,
  LEAST(clock_timestamp() + interval '10 minutes', review.expires_at)
FROM (VALUES
  ('31300000-0000-4000-8000-000000000002'::UUID,
   '31100000-0000-4000-8000-000000000002'::UUID,
   '31200000-0000-4000-8000-000000000002'::UUID,
   '31000000-0000-4000-8000-000000000002'::UUID, repeat('2', 64)),
  ('31300000-0000-4000-8000-000000000003'::UUID,
   '31100000-0000-4000-8000-000000000003'::UUID,
   '31200000-0000-4000-8000-000000000003'::UUID,
   '31000000-0000-4000-8000-000000000003'::UUID, repeat('3', 64)),
  ('31300000-0000-4000-8000-000000000004'::UUID,
   '31100000-0000-4000-8000-000000000004'::UUID,
   '31200000-0000-4000-8000-000000000004'::UUID,
   '31000000-0000-4000-8000-000000000004'::UUID, repeat('4', 64)),
  ('31300000-0000-4000-8000-000000000005'::UUID,
   '31100000-0000-4000-8000-000000000005'::UUID,
   '31200000-0000-4000-8000-000000000005'::UUID,
   '31000000-0000-4000-8000-000000000005'::UUID, repeat('5', 64)),
  ('31300000-0000-4000-8000-000000000006'::UUID,
   '31100000-0000-4000-8000-000000000006'::UUID,
   '31200000-0000-4000-8000-000000000006'::UUID,
   '31000000-0000-4000-8000-000000000006'::UUID, repeat('6', 64))
) AS seed(authorization_id, operation_id, job_id, client_request_id, execution_request_hash)
JOIN public.scrape_uncertainty_review_requests AS review
  ON review.client_request_id = seed.client_request_id;
