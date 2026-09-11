BEGIN TRANSACTION ISOLATION LEVEL REPEATABLE READ, READ ONLY;
-- provider project ref (out-of-band binding) grfwkutcsavqicaimssn
-- phase3n adoption contract sha256 4670ee1c174ef3092e7a32c192a9fccc8711982532252576b214c6569fbf3083
SET LOCAL statement_timeout = '300s';
SET LOCAL idle_in_transaction_session_timeout = '300s';
DO $phase3n_legacy_preflight$
BEGIN
    IF (SELECT array_agg(c.relname::TEXT ORDER BY c.relname::TEXT)
          FROM pg_catalog.pg_class AS c
          JOIN pg_catalog.pg_namespace AS n ON n.oid = c.relnamespace
         WHERE n.nspname = 'public' AND c.relkind IN ('r', 'p'))
       IS DISTINCT FROM ARRAY['bank_records','bets','entries','horse_details','horse_pedigree','jockey_details','ml_models','model_metadata','ocr_usage','past_performances','payouts','predictions','profiles','purchase_history','race_lap_times','race_odds','race_payouts','race_results','race_results_ultimate','races','races_ultimate','results','trainer_details','users']::TEXT[] THEN
        RAISE EXCEPTION 'phase3n-legacy-public-table-set-changed';
    END IF;
    IF to_regnamespace('phase3n_legacy_20260816') IS NOT NULL THEN
        RAISE EXCEPTION 'phase3n-legacy-archive-already-exists';
    END IF;
    IF to_regclass('phase3m_internal.bootstrap_history') IS NOT NULL THEN
        RAISE EXCEPTION 'phase3n-legacy-bootstrap-history-unexpected';
    END IF;
    IF EXISTS (
        SELECT 1
          FROM pg_catalog.pg_class AS c
          JOIN pg_catalog.pg_namespace AS n ON n.oid = c.relnamespace
         WHERE n.nspname = 'public'
           AND c.relkind IN ('r', 'p')
           AND c.relname LIKE 'phase3n_%'
    ) THEN
        RAISE EXCEPTION 'phase3n-legacy-phase3n-object-unexpected';
    END IF;
    IF (SELECT count(*) FROM public.bank_records) <> 0 THEN
        RAISE EXCEPTION 'phase3n-legacy-row-count-changed:bank_records';
    END IF;
    IF (SELECT count(*) FROM public.bets) <> 0 THEN
        RAISE EXCEPTION 'phase3n-legacy-row-count-changed:bets';
    END IF;
    IF (SELECT count(*) FROM public.entries) <> 0 THEN
        RAISE EXCEPTION 'phase3n-legacy-row-count-changed:entries';
    END IF;
    IF (SELECT count(*) FROM public.horse_details) <> 0 THEN
        RAISE EXCEPTION 'phase3n-legacy-row-count-changed:horse_details';
    END IF;
    IF (SELECT count(*) FROM public.horse_pedigree) <> 1805 THEN
        RAISE EXCEPTION 'phase3n-legacy-row-count-changed:horse_pedigree';
    END IF;
    IF (SELECT count(*) FROM public.jockey_details) <> 0 THEN
        RAISE EXCEPTION 'phase3n-legacy-row-count-changed:jockey_details';
    END IF;
    IF (SELECT count(*) FROM public.ml_models) <> 0 THEN
        RAISE EXCEPTION 'phase3n-legacy-row-count-changed:ml_models';
    END IF;
    IF (SELECT count(*) FROM public.model_metadata) <> 73 THEN
        RAISE EXCEPTION 'phase3n-legacy-row-count-changed:model_metadata';
    END IF;
    IF (SELECT count(*) FROM public.ocr_usage) <> 0 THEN
        RAISE EXCEPTION 'phase3n-legacy-row-count-changed:ocr_usage';
    END IF;
    IF (SELECT count(*) FROM public.past_performances) <> 0 THEN
        RAISE EXCEPTION 'phase3n-legacy-row-count-changed:past_performances';
    END IF;
    IF (SELECT count(*) FROM public.payouts) <> 0 THEN
        RAISE EXCEPTION 'phase3n-legacy-row-count-changed:payouts';
    END IF;
    IF (SELECT count(*) FROM public.predictions) <> 0 THEN
        RAISE EXCEPTION 'phase3n-legacy-row-count-changed:predictions';
    END IF;
    IF (SELECT count(*) FROM public.profiles) <> 3 THEN
        RAISE EXCEPTION 'phase3n-legacy-row-count-changed:profiles';
    END IF;
    IF (SELECT count(*) FROM public.purchase_history) <> 4 THEN
        RAISE EXCEPTION 'phase3n-legacy-row-count-changed:purchase_history';
    END IF;
    IF (SELECT count(*) FROM public.race_lap_times) <> 0 THEN
        RAISE EXCEPTION 'phase3n-legacy-row-count-changed:race_lap_times';
    END IF;
    IF (SELECT count(*) FROM public.race_odds) <> 0 THEN
        RAISE EXCEPTION 'phase3n-legacy-row-count-changed:race_odds';
    END IF;
    IF (SELECT count(*) FROM public.race_payouts) <> 5847 THEN
        RAISE EXCEPTION 'phase3n-legacy-row-count-changed:race_payouts';
    END IF;
    IF (SELECT count(*) FROM public.race_results) <> 9588 THEN
        RAISE EXCEPTION 'phase3n-legacy-row-count-changed:race_results';
    END IF;
    IF (SELECT count(*) FROM public.race_results_ultimate) <> 719 THEN
        RAISE EXCEPTION 'phase3n-legacy-row-count-changed:race_results_ultimate';
    END IF;
    IF (SELECT count(*) FROM public.races) <> 288 THEN
        RAISE EXCEPTION 'phase3n-legacy-row-count-changed:races';
    END IF;
    IF (SELECT count(*) FROM public.races_ultimate) <> 72 THEN
        RAISE EXCEPTION 'phase3n-legacy-row-count-changed:races_ultimate';
    END IF;
    IF (SELECT count(*) FROM public.results) <> 0 THEN
        RAISE EXCEPTION 'phase3n-legacy-row-count-changed:results';
    END IF;
    IF (SELECT count(*) FROM public.trainer_details) <> 0 THEN
        RAISE EXCEPTION 'phase3n-legacy-row-count-changed:trainer_details';
    END IF;
    IF (SELECT count(*) FROM public.users) <> 1 THEN
        RAISE EXCEPTION 'phase3n-legacy-row-count-changed:users';
    END IF;
    IF (
        SELECT encode(
            extensions.digest(
                convert_to(COALESCE(string_agg(row_hash, '|' ORDER BY row_key), ''), 'UTF8'),
                'sha256'
            ),
            'hex'
        )
        FROM (
            SELECT horse_id::TEXT AS row_key,
                   encode(
                       extensions.digest(convert_to(to_jsonb(t)::TEXT, 'UTF8'), 'sha256'),
                       'hex'
                   ) AS row_hash
            FROM public.horse_pedigree AS t
        ) AS source_rows
    )
       IS DISTINCT FROM '9074ba1d4d4b89c52619cc770ec99a579416b2b9ce818fc855a9cb08d6e2d644' THEN
        RAISE EXCEPTION 'phase3n-legacy-row-digest-changed:horse_pedigree';
    END IF;
    IF (
        SELECT encode(
            extensions.digest(
                convert_to(COALESCE(string_agg(row_hash, '|' ORDER BY row_key), ''), 'UTF8'),
                'sha256'
            ),
            'hex'
        )
        FROM (
            SELECT model_id::TEXT AS row_key,
                   encode(
                       extensions.digest(convert_to(to_jsonb(t)::TEXT, 'UTF8'), 'sha256'),
                       'hex'
                   ) AS row_hash
            FROM public.model_metadata AS t
        ) AS source_rows
    )
       IS DISTINCT FROM 'ad6ecf1c56bc921f7b1f20ca8efcb44fed309f547947a8ac46b3f989a8084359' THEN
        RAISE EXCEPTION 'phase3n-legacy-row-digest-changed:model_metadata';
    END IF;
    IF (
        SELECT encode(
            extensions.digest(
                convert_to(COALESCE(string_agg(row_hash, '|' ORDER BY row_key), ''), 'UTF8'),
                'sha256'
            ),
            'hex'
        )
        FROM (
            SELECT id::TEXT AS row_key,
                   encode(
                       extensions.digest(convert_to(to_jsonb(t)::TEXT, 'UTF8'), 'sha256'),
                       'hex'
                   ) AS row_hash
            FROM public.profiles AS t
        ) AS source_rows
    )
       IS DISTINCT FROM '67b0f8b62d310af3b6fa9d293335f2faa91cb5b5075e097af594a9d864bcaef1' THEN
        RAISE EXCEPTION 'phase3n-legacy-row-digest-changed:profiles';
    END IF;
    IF (
        SELECT encode(
            extensions.digest(
                convert_to(COALESCE(string_agg(row_hash, '|' ORDER BY row_key), ''), 'UTF8'),
                'sha256'
            ),
            'hex'
        )
        FROM (
            SELECT id::TEXT AS row_key,
                   encode(
                       extensions.digest(convert_to(to_jsonb(t)::TEXT, 'UTF8'), 'sha256'),
                       'hex'
                   ) AS row_hash
            FROM public.purchase_history AS t
        ) AS source_rows
    )
       IS DISTINCT FROM 'c7bf6e3b4ee949eb4c569c660285fa64cf1308467b9075d927142cff27244e93' THEN
        RAISE EXCEPTION 'phase3n-legacy-row-digest-changed:purchase_history';
    END IF;
    IF (
        SELECT encode(
            extensions.digest(
                convert_to(COALESCE(string_agg(row_hash, '|' ORDER BY row_key), ''), 'UTF8'),
                'sha256'
            ),
            'hex'
        )
        FROM (
            SELECT id::TEXT AS row_key,
                   encode(
                       extensions.digest(convert_to(to_jsonb(t)::TEXT, 'UTF8'), 'sha256'),
                       'hex'
                   ) AS row_hash
            FROM public.race_payouts AS t
        ) AS source_rows
    )
       IS DISTINCT FROM 'a4ea839ff5bae8c1776edec41df7e647532bf027ea5c4da0463adda595ea460d' THEN
        RAISE EXCEPTION 'phase3n-legacy-row-digest-changed:race_payouts';
    END IF;
    IF (
        SELECT encode(
            extensions.digest(
                convert_to(COALESCE(string_agg(row_hash, '|' ORDER BY row_key), ''), 'UTF8'),
                'sha256'
            ),
            'hex'
        )
        FROM (
            SELECT id::TEXT AS row_key,
                   encode(
                       extensions.digest(convert_to(to_jsonb(t)::TEXT, 'UTF8'), 'sha256'),
                       'hex'
                   ) AS row_hash
            FROM public.race_results AS t
        ) AS source_rows
    )
       IS DISTINCT FROM 'eb48e6dab0d2dd6514fe807dedf18fac494a9c1f33c0aa4b540c66f2588ad019' THEN
        RAISE EXCEPTION 'phase3n-legacy-row-digest-changed:race_results';
    END IF;
    IF (
        SELECT encode(
            extensions.digest(
                convert_to(COALESCE(string_agg(row_hash, '|' ORDER BY row_key), ''), 'UTF8'),
                'sha256'
            ),
            'hex'
        )
        FROM (
            SELECT id::TEXT AS row_key,
                   encode(
                       extensions.digest(convert_to(to_jsonb(t)::TEXT, 'UTF8'), 'sha256'),
                       'hex'
                   ) AS row_hash
            FROM public.race_results_ultimate AS t
        ) AS source_rows
    )
       IS DISTINCT FROM '112d562f8bf89e8e771b23d8b4f80aa8b41f9a798cde01fdb41df21af6af42f2' THEN
        RAISE EXCEPTION 'phase3n-legacy-row-digest-changed:race_results_ultimate';
    END IF;
    IF (
        SELECT encode(
            extensions.digest(
                convert_to(COALESCE(string_agg(row_hash, '|' ORDER BY row_key), ''), 'UTF8'),
                'sha256'
            ),
            'hex'
        )
        FROM (
            SELECT race_id::TEXT AS row_key,
                   encode(
                       extensions.digest(convert_to(to_jsonb(t)::TEXT, 'UTF8'), 'sha256'),
                       'hex'
                   ) AS row_hash
            FROM public.races AS t
        ) AS source_rows
    )
       IS DISTINCT FROM '96aa605a704d04c938b48c43982e55c41c73f0fe6ac4d7c79cb063f8cc48a258' THEN
        RAISE EXCEPTION 'phase3n-legacy-row-digest-changed:races';
    END IF;
    IF (
        SELECT encode(
            extensions.digest(
                convert_to(COALESCE(string_agg(row_hash, '|' ORDER BY row_key), ''), 'UTF8'),
                'sha256'
            ),
            'hex'
        )
        FROM (
            SELECT race_id::TEXT AS row_key,
                   encode(
                       extensions.digest(convert_to(to_jsonb(t)::TEXT, 'UTF8'), 'sha256'),
                       'hex'
                   ) AS row_hash
            FROM public.races_ultimate AS t
        ) AS source_rows
    )
       IS DISTINCT FROM 'a27b2555c7ef8b1f7f5dd7784dd6f1d7b4115737c2225788f1f272ce985671da' THEN
        RAISE EXCEPTION 'phase3n-legacy-row-digest-changed:races_ultimate';
    END IF;
    IF (
        SELECT encode(
            extensions.digest(
                convert_to(COALESCE(string_agg(row_hash, '|' ORDER BY row_key), ''), 'UTF8'),
                'sha256'
            ),
            'hex'
        )
        FROM (
            SELECT id::TEXT AS row_key,
                   encode(
                       extensions.digest(convert_to(to_jsonb(t)::TEXT, 'UTF8'), 'sha256'),
                       'hex'
                   ) AS row_hash
            FROM public.users AS t
        ) AS source_rows
    )
       IS DISTINCT FROM '04b79d98eebf1bd137cdbcae02e950ff6e13dd0d116e1417b3ddbd5e5a9aceaf' THEN
        RAISE EXCEPTION 'phase3n-legacy-row-digest-changed:users';
    END IF;
    IF (SELECT count(*) FROM public.race_payouts AS source
        LEFT JOIN public.profiles AS p ON p.id = source.user_id
        WHERE source.user_id IS NOT NULL AND p.id IS NULL) <> 5847
       OR (SELECT count(DISTINCT source.user_id) FROM public.race_payouts AS source
        LEFT JOIN public.profiles AS p ON p.id = source.user_id
        WHERE source.user_id IS NOT NULL AND p.id IS NULL) <> 1
       OR (SELECT count(*) FROM public.race_payouts AS source
        LEFT JOIN auth.users AS u ON u.id = source.user_id
        WHERE source.user_id IS NOT NULL AND u.id IS NULL) <> 5847 THEN
        RAISE EXCEPTION 'phase3n-legacy-user-reference-contract-changed:race_payouts';
    END IF;
    IF (SELECT count(*) FROM public.race_results AS source
        LEFT JOIN public.profiles AS p ON p.id = source.user_id
        WHERE source.user_id IS NOT NULL AND p.id IS NULL) <> 9588
       OR (SELECT count(DISTINCT source.user_id) FROM public.race_results AS source
        LEFT JOIN public.profiles AS p ON p.id = source.user_id
        WHERE source.user_id IS NOT NULL AND p.id IS NULL) <> 1
       OR (SELECT count(*) FROM public.race_results AS source
        LEFT JOIN auth.users AS u ON u.id = source.user_id
        WHERE source.user_id IS NOT NULL AND u.id IS NULL) <> 9588 THEN
        RAISE EXCEPTION 'phase3n-legacy-user-reference-contract-changed:race_results';
    END IF;
    IF (SELECT count(*) FROM public.races AS source
        LEFT JOIN public.profiles AS p ON p.id = source.user_id
        WHERE source.user_id IS NOT NULL AND p.id IS NULL) <> 288
       OR (SELECT count(DISTINCT source.user_id) FROM public.races AS source
        LEFT JOIN public.profiles AS p ON p.id = source.user_id
        WHERE source.user_id IS NOT NULL AND p.id IS NULL) <> 1
       OR (SELECT count(*) FROM public.races AS source
        LEFT JOIN auth.users AS u ON u.id = source.user_id
        WHERE source.user_id IS NOT NULL AND u.id IS NULL) <> 288 THEN
        RAISE EXCEPTION 'phase3n-legacy-user-reference-contract-changed:races';
    END IF;
    IF EXISTS (
        SELECT 1 FROM public.race_results AS rr
        LEFT JOIN public.races AS r ON r.race_id = rr.race_id
        WHERE r.race_id IS NULL
    ) OR EXISTS (
        SELECT 1 FROM public.race_payouts AS rp
        LEFT JOIN public.races AS r ON r.race_id = rp.race_id
        WHERE r.race_id IS NULL
    ) OR EXISTS (
        SELECT 1 FROM public.race_results_ultimate AS rr
        LEFT JOIN public.races_ultimate AS r ON r.race_id = rr.race_id
        WHERE r.race_id IS NULL
    ) THEN
        RAISE EXCEPTION 'phase3n-legacy-orphan-reference-detected';
    END IF;
    IF EXISTS (
        WITH parsed AS (
            SELECT race_id, (data #>> '{}')::JSONB AS data
            FROM public.race_results_ultimate
        )
        SELECT 1 FROM parsed
        WHERE jsonb_typeof(data) <> 'object'
           OR COALESCE(
                NULLIF(btrim(data ->> 'horse_number'), ''),
                NULLIF(btrim(data ->> 'horse_num'), '')
           ) IS NULL
    ) OR EXISTS (
        WITH parsed AS (
            SELECT race_id,
                   COALESCE(
                       NULLIF(btrim(((data #>> '{}')::JSONB) ->> 'horse_number'), ''),
                       NULLIF(btrim(((data #>> '{}')::JSONB) ->> 'horse_num'), '')
                   ) AS horse_number
            FROM public.race_results_ultimate
        )
        SELECT 1 FROM parsed GROUP BY race_id, horse_number HAVING count(*) > 1
    ) THEN
        RAISE EXCEPTION 'phase3n-legacy-ultimate-transform-invalid';
    END IF;
    IF EXISTS (
        SELECT 1 FROM public.profiles AS p
        LEFT JOIN auth.users AS u ON u.id = p.id
        WHERE u.id IS NULL OR p.role IS NULL OR p.role NOT IN ('user', 'admin')
           OR p.subscription_tier IS NULL OR p.subscription_tier NOT IN ('free', 'premium')
    ) OR EXISTS (
        SELECT 1 FROM public.purchase_history AS ph
        LEFT JOIN public.profiles AS p ON p.id = ph.user_id
        WHERE p.id IS NULL
    ) THEN
        RAISE EXCEPTION 'phase3n-legacy-identity-contract-invalid';
    END IF;
    IF NOT EXISTS (
        SELECT 1 FROM storage.buckets WHERE id = 'models' AND name = 'models'
    ) OR (SELECT count(*) FROM storage.objects WHERE bucket_id = 'models') <> 146
       OR EXISTS (
           SELECT 1 FROM public.model_metadata AS m
           LEFT JOIN storage.objects AS o
             ON o.bucket_id = 'models' AND o.name = m.storage_path
           WHERE o.id IS NULL
       ) THEN
        RAISE EXCEPTION 'phase3n-legacy-model-storage-contract-invalid';
    END IF;
END
$phase3n_legacy_preflight$;
SELECT jsonb_build_object('status', 'PASS', 'production_project_ref', 'grfwkutcsavqicaimssn', 'candidate_commit_sha', '86a2d314a641160e852d3597396aadcd03e81347', 'contract_sha256', '4670ee1c174ef3092e7a32c192a9fccc8711982532252576b214c6569fbf3083', 'checked_at', clock_timestamp(), 'transaction_read_only', current_setting('transaction_read_only')) AS phase3n_legacy_production_preflight;
COMMIT;
