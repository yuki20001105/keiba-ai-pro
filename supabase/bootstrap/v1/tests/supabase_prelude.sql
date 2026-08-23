-- Minimal Supabase-compatible prelude for the disposable Phase 3M gate.
-- This file intentionally creates infrastructure schemas only. Application
-- objects must be created exclusively by the manifest migration chain.

DO $phase3m_roles$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_catalog.pg_roles WHERE rolname = 'anon') THEN
        EXECUTE 'CREATE ROLE anon NOLOGIN NOINHERIT';
    END IF;
    IF NOT EXISTS (SELECT 1 FROM pg_catalog.pg_roles WHERE rolname = 'authenticated') THEN
        EXECUTE 'CREATE ROLE authenticated NOLOGIN NOINHERIT';
    END IF;
    IF NOT EXISTS (SELECT 1 FROM pg_catalog.pg_roles WHERE rolname = 'service_role') THEN
        EXECUTE 'CREATE ROLE service_role NOLOGIN NOINHERIT BYPASSRLS';
    END IF;
END;
$phase3m_roles$;

GRANT anon, authenticated, service_role TO postgres;

CREATE SCHEMA IF NOT EXISTS extensions AUTHORIZATION postgres;
CREATE SCHEMA IF NOT EXISTS auth AUTHORIZATION postgres;
CREATE SCHEMA IF NOT EXISTS storage AUTHORIZATION postgres;

CREATE EXTENSION IF NOT EXISTS pgcrypto WITH SCHEMA extensions;
CREATE EXTENSION IF NOT EXISTS "uuid-ossp" WITH SCHEMA extensions;

-- PostgreSQL 17 exposes gen_random_uuid() through pg_catalog. uuid-ossp and
-- pgcrypto remain in the Supabase-compatible extensions schema and migrations
-- that use their other functions must schema-qualify them.

CREATE TABLE auth.users (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    email TEXT UNIQUE,
    raw_user_meta_data JSONB NOT NULL DEFAULT '{}'::JSONB,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE FUNCTION auth.uid()
RETURNS UUID
LANGUAGE sql
STABLE
SECURITY INVOKER
SET search_path = pg_catalog
AS $$
    SELECT NULLIF(current_setting('request.jwt.claim.sub', TRUE), '')::UUID;
$$;

CREATE FUNCTION auth.role()
RETURNS TEXT
LANGUAGE sql
STABLE
SECURITY INVOKER
SET search_path = pg_catalog
AS $$
    SELECT COALESCE(
        NULLIF(current_setting('request.jwt.claim.role', TRUE), ''),
        NULLIF(current_setting('request.jwt.claims', TRUE), '')::JSONB ->> 'role'
    );
$$;

CREATE FUNCTION auth.jwt()
RETURNS JSONB
LANGUAGE sql
STABLE
SECURITY INVOKER
SET search_path = pg_catalog
AS $$
    SELECT COALESCE(
        NULLIF(current_setting('request.jwt.claims', TRUE), '')::JSONB,
        '{}'::JSONB
    );
$$;

REVOKE ALL ON FUNCTION auth.uid(), auth.role(), auth.jwt() FROM PUBLIC;
GRANT EXECUTE ON FUNCTION auth.uid(), auth.role(), auth.jwt()
    TO anon, authenticated, service_role;

CREATE TABLE storage.buckets (
    id TEXT PRIMARY KEY,
    name TEXT NOT NULL UNIQUE,
    public BOOLEAN NOT NULL DEFAULT FALSE,
    file_size_limit BIGINT,
    allowed_mime_types TEXT[],
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE TABLE storage.objects (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    bucket_id TEXT NOT NULL REFERENCES storage.buckets(id) ON DELETE CASCADE,
    name TEXT NOT NULL,
    owner UUID,
    metadata JSONB,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE (bucket_id, name)
);

ALTER TABLE storage.buckets ENABLE ROW LEVEL SECURITY;
ALTER TABLE storage.objects ENABLE ROW LEVEL SECURITY;

REVOKE CREATE ON SCHEMA extensions FROM PUBLIC, anon, authenticated;
GRANT USAGE ON SCHEMA extensions TO anon, authenticated, service_role;
REVOKE ALL ON SCHEMA auth, storage FROM PUBLIC, anon, authenticated;
GRANT USAGE ON SCHEMA auth TO anon, authenticated, service_role;
GRANT USAGE ON SCHEMA storage TO anon, authenticated, service_role;
REVOKE ALL ON TABLE auth.users, storage.buckets, storage.objects
    FROM PUBLIC, anon, authenticated;
GRANT ALL ON TABLE auth.users, storage.buckets, storage.objects TO service_role;

-- Hosted Supabase Storage gives API roles object privileges and relies on RLS
-- for authorization. The contract proves those grants still cannot reach the
-- private models bucket.
GRANT SELECT ON TABLE storage.buckets TO anon, authenticated;
GRANT SELECT, INSERT, UPDATE, DELETE ON TABLE storage.objects TO anon, authenticated;
