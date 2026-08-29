# Phase 3I Synthetic Saga Failure-Injection Gate

## 1. Status and Decision Boundary

Phase 3I defines and exercises a pure, deterministic cross-store saga state-machine contract. It is intentionally:

- synthetic and non-executable;
- L2 contract evidence only;
- incapable of reserving, consuming, dispatching, retrying, compensating or unlocking a real operation;
- incapable of applying a migration, contacting an external service or mutating an operational database;
- incapable of changing the Phase 3H decision from `production_ready=false` or `l3_eligible=false`.

A successful Phase 3I gate means the offline model rejected unsafe transitions and completed the required failure matrix with `effect_count=0`. That counter records forbidden effectful primitive attempts observed by the harness guard. The model has no executable effect adapter, and emitted intent data is not an executed effect. A zero value therefore proves only that the guarded synthetic run observed no forbidden primitive attempt. It does **not** mean that the production system has a saga, that the review ledger is deployed, or that a scrape may run.

## 2. Immutable Operation Binding

Every modeled transition is bound to one exact tuple:

- canonical `operation_id`;
- predetermined canonical `job_id`;
- canonical `review_id`;
- positive immutable `review_version`;
- verified `owner_user_id`;
- lowercase SHA-256 `request_hash`;
- derived versioned `review_binding_hash` and `execution_binding_hash`;
- a versioned canonical `binding_hash` over both derived hashes.

These fields are immutable after preparation. Owner identity is authoritative input to the model and is never inferred from a decision actor or client body. An idempotency replay with the same key and identical binding returns the same modeled outcome; the same key with a different owner, job, review or payload is a conflict.

The review binding includes owner, review ID/version and request hash. The execution binding includes operation, predetermined job, owner and the same request hash. A future adapter must prove how the Phase 3F review payload hash and the existing scrape-job request hash map to this canonical `request_hash`; similarly named hashes must not be silently treated as interchangeable.

## 3. State and Transition Contract

The exact allowlisted state set is:

- `reserve_pending`;
- `local_prepare_pending`;
- `consume_pending`;
- `dispatch_pending`;
- `running`;
- `succeeded`;
- `compensation_pending`;
- `compensated`;
- `failed_terminal`;
- `manual_intervention`.

The terminal states are exactly `succeeded`, `compensated`, `failed_terminal` and `manual_intervention`. State changes are driven only by the allowlisted actions `reserve_review`, `prepare_local_transaction`, `consume_reservation`, `dispatch_worker` and `release_reservation`. An ambiguous remote outcome is represented by a blocking transition to the appropriate compensation/manual path; it is never converted into implicit success.

The pure API is limited to `create_saga`, `apply_event`, `recover` and `validate_snapshot`. A transition returns only `accepted`, `duplicate`, the next snapshot, modeled `emitted_intents` and a failure code. Emitted intents are data for verification, not executed effects.

The model rejects unknown states, unknown events, invalid schemas, non-positive snapshot versions, invalid state ordering and binding drift. Snapshot versions increment within pure accepted transitions, but events do not carry an expected version, so out-of-order version/CAS behavior is not implemented or proven. It never defaults an unknown value to `error`, `completed`, retryable or compensatable. In particular:

- no worker claim is valid before authoritative consume confirmation;
- no uncertain state advances automatically;
- terminal states never return to a non-terminal state;
- review approval alone is never a reservation, consume result or execution token.

## 4. Idempotency, Lease and Fencing Contract

- Transition/event identifiers are replay-safe only when their complete canonical payload is identical.
- A replayed identifier with different data is rejected as a conflict.
- Modeled reserve, consume, dispatch and compensation intents retain deterministic operation-scoped identifiers across pure recovery replay.
- The model requires a positive worker fencing token above the pending intent's minimum and rejects stale-token completion facts.
- Binding and intent mismatches are rejected with no modeled effect. A worker result presented at or after lease expiry, or with a stale fencing token, is likewise rejected; explicit expiry recovery instead returns the next modeled dispatch intent without executing it.

This synthetic contract models only a worker fencing token and lease expiry. It does not model a lease owner, lease renewal, worker progress/heartbeat, durable compare-and-swap persistence, multiple live processes or an irreversible downstream effect. It therefore does not prove durable recovery or multi-instance safety. It also does not claim that SQLite fencing can protect an external HTTP request or a write to another database. A future executable implementation must persist lease ownership/versioning, define renewal/progress semantics and propagate idempotency/fencing at every irreversible downstream effect boundary.

## 5. Required Failure-Injection Matrix

The version-1 repository contract contains exactly these 14 synthetic scenarios:

1. failure before prepare;
2. failure after prepare;
3. reservation rejection;
4. reservation expiry;
5. consume rejection;
6. ambiguous consume outcome;
7. failure after consume but before outbox acknowledgement;
8. dispatcher crash before claim commit;
9. dispatcher crash after claim;
10. stale fencing token;
11. duplicate dispatcher replay;
12. interrupted compensation followed by replay;
13. recovery replayed twice;
14. concurrent recovery.

The contract also requires the nine invariant checks `stable_operation_job_binding`, `idempotent_prepare`, `consume_before_dispatch`, `deterministic_recovery`, `idempotent_compensation`, `lease_fencing`, `replay_deduplication`, `worker_dispatch_prohibited` and `zero_external_effects`. The version-1 fixture records those scenario and invariant names as exact ordered lists; the runtime report records an exact boolean check map for each list.

Every case must be deterministic, fail closed and report zero real effects. Passing by skipping, coercing or silently normalizing an unknown case is prohibited.

## 6. Evidence and CI Contract

The Phase 3I release-blocking job:

1. waits for Phase 3H;
2. downloads `phase3h-production-readiness-json` from the same workflow run;
3. revalidates that the Phase 3H decision downloaded from the same workflow run is the exact commit-correlated Production NOT_READY result;
4. runs the repository-owned version-1 synthetic failure matrix through the pure state machine;
5. verifies exact schema, case coverage, immutable binding checks, zero guard-observed forbidden primitive attempts and negative readiness claims;
6. uploads `phase3i-saga-failure-injection-json` even on failure.

Expected sanitized outputs are:

- runtime: `reports/phase3i_saga_failure_injection_runtime.json`;
- verification: `reports/phase3i_saga_failure_injection_gate.json`;
- `synthetic=true`;
- `non_executable=true`;
- `effect_count=0` for guard-observed forbidden effectful primitive attempts, with no executable effect adapter in the model;
- `production_ready=false`;
- `l3_eligible=false`.

Malformed, stale, incomplete, secret/path-bearing, commit-mismatched or self-promoting evidence fails the job.

## 7. Prohibited Implementation Shortcuts

Phase 3I must not:

- connect this model to `/api/scrape/start` or any UI action;
- keep the existing direct daemon-thread dispatch as a second path beside a future outbox;
- treat `approved`, `not_found`, timeout or transport failure as execution permission;
- implement a local-only unlock or delete an uncertainty lock;
- claim exactly-once downstream effects from a SQLite token alone;
- apply the Phase 3F migration or create reservation/consume RPCs;
- deploy, use production credentials or contact an external environment.

## 8. Remaining Production Blockers

After Phase 3I, Production remains NOT_READY and L3 remains unclaimed. Remaining work includes an independently audited executable saga/outbox, downstream effect fencing/idempotency, durable persistence/recovery and compensation, an explicitly approved staging migration, non-synthetic multi-instance crash/recovery evidence, a trusted evidence producer, and separate execution-unlock and release approvals.
