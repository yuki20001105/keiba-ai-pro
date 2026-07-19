# Phase 3J Durable Saga/Outbox Disposable Runtime Gate

## 1. Decision boundary

Phase 3J introduces an executable saga/outbox implementation only for repository tests and the release-blocking disposable CI gate. It is not connected to a Next/FastAPI route, the existing scrape worker, `jobs.py`, a browser action, or an operational database. The PostgreSQL reservation migration is applied only inside a randomly named, digest-pinned disposable container. Code integration does not apply that migration to Supabase or any external environment.

A successful gate therefore means:

- the temporary SQLite store and disposable PostgreSQL reservation contract satisfied the versioned failure matrix;
- all observed application-level worker, network, thread and non-disposable write counts remained zero;
- the disposable database mutations were counted separately and then destroyed;
- the evidence was bound to the tested commit plus exact contract, SQLite schema, runtime asset and migration hashes;
- the same-run Phase 3H and Phase 3I artifacts were still valid Production NOT_READY evidence.

It does **not** mean the production topology is ready. The only valid successful result is `verdict=not-ready`, `production_ready=false` and `l3_eligible=false`.

## 2. Disposable execution topology

The CI producer uses two isolated stores:

1. a temporary SQLite file under a randomized workspace, with the Phase 3J schema initialized through the repository runtime;
2. `postgres:17.6-bookworm` pinned by digest, started with `--network none`, no published host port and a generated container-local password.

No Supabase URL, service key, external DSN, production credential or staging credential is accepted. Docker CLI commands run with a gate-owned empty `DOCKER_CONFIG` and isolated `HOME` under the disposable workspace, so a developer or runner credential helper cannot be inherited. The PostgreSQL image can be pulled anonymously by the CI host before the container starts; the running container itself has no network. Cleanup always attempts `docker rm --force` for the random gate-owned name, including a client timeout during `docker run`, and must prove both the randomized workspace and container are absent. Any unverified cleanup fails the gate.

The sanitized report separates:

- `operational_effect_count` and `worker_dispatch_count`, which must both be exactly zero;
- exact zero counters for `worker_dispatch`, `network_call`, `thread_start` and `operational_write`;
- `disposable_database_effect_count`, split into positive temporary SQLite and disposable PostgreSQL mutation counts.

Intent/outbox records and disposable contract mutations are not counted as operational effects. They are evidence-only disposable database effects and cannot dispatch a real worker.

## 3. Runtime contract

The repository runtime persists the Phase 3I binding and transition model in SQLite. Preparation must atomically create the predetermined local job record, saga snapshot, immutable event and outbox intent. Recovery and replay use stable identifiers. Outbox claims require a lease owner, expiry and fencing token; stale acknowledgements and stale fencing tokens are rejected. An ambiguous remote outcome does not become success or permission to dispatch. Compensation is replay-safe. Malformed/corrupt/unavailable storage fails closed.

The PostgreSQL migration separates review approval from execution authority. An approved Phase 3G review alone cannot reserve or consume execution. A separately bootstrapped immutable authorization with matching owner, operation, predetermined job, review/version and execution hash is required. Reservation, consume, release and expiry operations use version/fencing checks, idempotency keys and service-role-only RPC access.

This disposable proof does not remove the current direct scrape worker path and does not claim that path is protected by the new outbox. Connecting both paths simultaneously is prohibited.

## 4. Required failure matrix

The version-1 repository contract requires all of the following:

- SQLite prepare rollback leaves no partial job/saga/event/outbox state;
- crash followed by recovery/replay retains stable binding and intent identity;
- competing claims have one winner;
- lease expiry advances fencing and rejects stale completion;
- stale acknowledgement is rejected;
- ambiguous remote status stops without worker dispatch;
- compensation replay is idempotent;
- corrupt or unavailable SQLite fails closed;
- an approved review without independent execution authorization is denied;
- PostgreSQL reservation replay is idempotent;
- consume uses version/CAS semantics;
- release replay is idempotent;
- expiry preserves monotonic fencing.

Every required scenario and invariant is an exact allowlist. Missing, extra, false, malformed or coerced checks fail closed.

## 5. Evidence and CI contract

The Phase 3J job depends on the same workflow run's Phase 3H and Phase 3I jobs and downloads both artifacts by exact artifact name. The verifier independently checks:

- exact report schemas and all positive checks from both prerequisite gates;
- the same tested commit in Phase 3H, Phase 3I and Phase 3J, with the producer and verifier independently matching it to the actual checkout `HEAD`;
- exact contract, migration, SQLite schema and five runtime-module hashes;
- evidence freshness and strict JSON without duplicate keys or non-finite values;
- exact scenario/invariant coverage;
- no host port, no external credentials and no external migration application;
- zero operational effects and positive, separately counted disposable DB effects;
- successful container/workspace cleanup;
- absence of paths, credentials, raw errors and other prohibited evidence content.

The expected CI total after integration is 11 jobs and 8 artifacts. The Phase 3J artifact is `phase3j-saga-outbox-runtime-json` and contains only the sanitized runtime and verifier reports.

## 6. Explicit non-goals and remaining blockers

Phase 3J remains L2 code/CI evidence. Production remains NOT_READY and L3 remains unclaimed because:

- the runtime is not wired to the scrape API or worker;
- the current direct daemon-thread dispatch has not been replaced by one audited outbox-only path;
- the new PostgreSQL migration is not applied in an approved staging environment;
- real multi-process crash/recovery, lease renewal/progress and downstream idempotency/fencing are not proven in staging;
- operational observability, alerts, runbooks and rollback drills are incomplete;
- migration, execution unlock and release approvals remain separate external controls;
- main/release governance and trusted deployment evidence remain external prerequisites.

No Phase 3J repository result may be used to promote, deploy, unlock, retry or execute a real scrape operation.
