\set ON_ERROR_STOP on

CREATE SCHEMA IF NOT EXISTS extensions;

DO $$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'anon') THEN
        CREATE ROLE anon NOLOGIN NOINHERIT;
    END IF;
    IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'authenticated') THEN
        CREATE ROLE authenticated NOLOGIN NOINHERIT;
    END IF;
    IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'service_role') THEN
        CREATE ROLE service_role NOLOGIN NOINHERIT BYPASSRLS;
    END IF;
END;
$$;

CREATE TABLE public.profiles (
    id UUID PRIMARY KEY,
    email TEXT UNIQUE NOT NULL,
    full_name TEXT,
    role TEXT NOT NULL DEFAULT 'user',
    subscription_tier TEXT NOT NULL DEFAULT 'free',
    pred_count_remaining INTEGER NOT NULL DEFAULT 10,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp()
);

GRANT USAGE ON SCHEMA public TO anon, authenticated, service_role;
GRANT SELECT, UPDATE ON TABLE public.profiles TO authenticated;
GRANT SELECT ON TABLE public.profiles TO service_role;

INSERT INTO public.profiles (id, email, full_name, role) VALUES
    ('11111111-1111-4111-8111-111111111111', 'requester@example.invalid', 'Requester Admin', 'admin'),
    ('22222222-2222-4222-8222-222222222222', 'reviewer@example.invalid', 'Reviewer Admin', 'admin'),
    ('33333333-3333-4333-8333-333333333333', 'third@example.invalid', 'Third Admin', 'admin'),
    ('44444444-4444-4444-8444-444444444444', 'user@example.invalid', 'Ordinary User', 'user');

CREATE SCHEMA phase3g_test AUTHORIZATION postgres;
CREATE TABLE phase3g_test.runtime_clock (
    singleton BOOLEAN PRIMARY KEY DEFAULT TRUE CHECK (singleton),
    occurred_at TIMESTAMPTZ NOT NULL
);
INSERT INTO phase3g_test.runtime_clock (singleton, occurred_at)
VALUES (TRUE, clock_timestamp());
GRANT USAGE ON SCHEMA phase3g_test TO service_role;
GRANT SELECT ON TABLE phase3g_test.runtime_clock TO service_role;
