-- Netkeiba sandbox tables migration plan (P1-14.5)
-- Scope: sandbox tables only. Do NOT modify production/base tables.
-- Safety: manual apply only. No auto-apply path is introduced by this file.

BEGIN TRANSACTION;

CREATE TABLE IF NOT EXISTS sandbox_netkeiba_races (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    race_id TEXT NOT NULL,
    data TEXT,
    payload TEXT,
    idempotency_key TEXT NOT NULL,
    payload_hash TEXT NOT NULL,
    audit_payload TEXT NOT NULL,
    created_at TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS sandbox_netkeiba_race_results (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    race_id TEXT NOT NULL,
    data TEXT,
    payload TEXT,
    idempotency_key TEXT NOT NULL,
    payload_hash TEXT NOT NULL,
    audit_payload TEXT NOT NULL,
    created_at TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS sandbox_netkeiba_race_payouts (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    race_id TEXT NOT NULL,
    data TEXT,
    payload TEXT,
    idempotency_key TEXT NOT NULL,
    payload_hash TEXT NOT NULL,
    audit_payload TEXT NOT NULL,
    created_at TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE INDEX IF NOT EXISTS idx_sbx_races_race_id ON sandbox_netkeiba_races (race_id);
CREATE INDEX IF NOT EXISTS idx_sbx_results_race_id ON sandbox_netkeiba_race_results (race_id);
CREATE INDEX IF NOT EXISTS idx_sbx_payouts_race_id ON sandbox_netkeiba_race_payouts (race_id);

CREATE INDEX IF NOT EXISTS idx_sbx_races_idempotency ON sandbox_netkeiba_races (idempotency_key);
CREATE INDEX IF NOT EXISTS idx_sbx_results_idempotency ON sandbox_netkeiba_race_results (idempotency_key);
CREATE INDEX IF NOT EXISTS idx_sbx_payouts_idempotency ON sandbox_netkeiba_race_payouts (idempotency_key);

COMMIT;

-- Rollback / drop plan (manual execution only):
-- BEGIN TRANSACTION;
-- DROP TABLE IF EXISTS sandbox_netkeiba_race_payouts;
-- DROP TABLE IF EXISTS sandbox_netkeiba_race_results;
-- DROP TABLE IF EXISTS sandbox_netkeiba_races;
-- COMMIT;
