# Phase 3D Bounded Live Validation Contract

## Status

- Phase: 3D
- Capability level after code/CI completion: L2 (contract-ready)
- L3 status: not reached until a controlled staging run provides auditable live evidence
- Production repair/refetch unlock: not included

## Endpoints

### Browser-facing Next route

- `POST /api/scrape/live-validation`
- server-side authorization: Admin only
- forwards the verified bearer token to FastAPI
- does not start Python or access local report/database paths
- all responses include `Cache-Control: no-store`

### FastAPI route

- `POST /api/scrape/live-validation`
- runtime authorization: `require_admin`
- performs one server-owned plan generation followed by one bounded live validation
- all planner, cache, fixture, report and output paths are server-owned
- all temporary files and directories are removed on success, failure, timeout and cancellation paths

### Runtime input provisioning

- The container image never embeds operational reports or databases.
- The main SQLite database is supplied through the existing `/app/keiba/data` runtime volume.
- The three planner reports are supplied read-only at `/app/keiba/data/live-validation-inputs` (or an absolute server-configured `LIVE_VALIDATION_INPUT_DIR`).
- Docker Compose mounts `./reports` read-only at that input directory; cloud environments must provide the equivalent server-owned mount.
- Every request performs a fail-closed preflight and snapshots the bounded JSON reports into its private temporary workspace before starting a subprocess.
- Missing or malformed required reports/main DB return `503` before planner or external HTTP work begins.
- A missing HTTP cache is represented by an empty request-scoped temporary cache; no persistent cache file is created. The pedigree cache remains optional.

## Request Contract

The request must contain exactly these keys:

```json
{
  "target": "all",
  "url_type": "all",
  "max_urls": 1,
  "confirm_live_fetch": true
}
```

Allowed values:

- `target`: `all`, `race`, `horse`, `result`, `pedigree`, `odds`
- `url_type`: `all`, `race-result`, `race-detail`, `horse-detail`, `pedigree`
- `max_urls`: integer from 1 through 3
- `confirm_live_fetch`: literal `true`

The client must never provide any URL, plan JSON, filesystem path, cache path, output path, script path, fixture path or command-line argument.

## Authorization and Concurrency

- Next and FastAPI independently enforce Admin authorization.
- One live-validation execution may run in a service process at a time.
- Concurrent execution is rejected without starting planner or validator work.
- Per-user cooldown is enforced after a started execution.
- The UI never runs automatically and requires an explicit confirmation checkbox.

## Network Boundary

- Only exact `https://db.netkeiba.com` targets are accepted.
- Userinfo, explicit ports, query strings and fragments are rejected.
- Paths must match the declared URL type and validated race/horse identifier.
- Redirect following is disabled.
- Requests are sequential (`parallelism=1`) with at least one second between requests.
- One selected URL permits exactly one outbound attempt (`max_retries=1` in the shared fetch pipeline means one total attempt, not one retry).
- Automatic retry and `Retry-After` handling are disabled for this live-validation path, so total outbound attempts never exceed `max_urls` (maximum 3).
- `max_urls`, per-request timeout, total timeout and response-body size are bounded.
- The browser never connects to Netkeiba directly.

## No-Write Boundary

- Importing `fetch_pipeline` must not create or mutate SQLite files.
- Live validation uses no cache write, resume write, DB repair, upsert or production table write.
- The optional existing fetch cache is opened read-only and only non-expired rows may exclude a target.
- Both the planner and validator enforce the same expiry rule; an expired cache row remains eligible for bounded live validation.
- A missing cache DB is treated as an empty read-only cache, not created.
- The only permitted writes are server-owned temporary planner/validation reports, removed on all paths.

## Response Contract

Successful HTTP responses use this fixed safety envelope:

```json
{
  "live_validation": true,
  "bounded": true,
  "external_http": true,
  "read_only": true,
  "execution_enabled": false,
  "result": {}
}
```

`result` is an explicit allowlist projection. It includes:

- selected `target`, `url_type`, `max_urls_applied`
- attempted, HTTP, parse, would-fix, no-downgrade and repairable counts
- bounded elapsed/runtime estimates
- exclusion counts
- at most three validated sample results
- bounded recommended actions
- runtime/rate-limit policy
- required safety flags
- `verdict` and `verdict_reason`

It does not include timestamps, input/output/cache paths, raw stdout/stderr, subprocess commands, internal planner provenance, fixture data, fetch metrics with internal state, or unknown fields.

Aggregate counts and the verdict are recomputed from the validated sample rows at both the FastAPI and Next boundaries. HTTP status, parse status and action must agree. For normal samples, `would_fix_columns` must exactly equal the ordered intersection of `missing_fields_before` and `fields_found_after`; the race-without-horse-data case requires explicit `(check)` evidence in all three lists. A zero-attempt `pass`, a sample/aggregate contradiction, or any other malformed success payload is converted to a fail-closed `502` response and is not rendered as validation evidence.

Horse results require response-derived Netkeiba title/Open Graph identity whose horse ID matches the requested URL. Pedigree results additionally require a real `blood_table` with extracted pedigree fields. A requested URL/ID, generic HTML, a maintenance page, or title-only pedigree response can never count as parse evidence.

## Local Bounded Evidence (not L3)

- On 2026-07-18, a local server-owned-input run selected one URL and observed one HTTP success and one parse success with zero errors.
- SHA-256, size and modification time for the main DB, HTTP cache and pedigree cache were unchanged before and after that run.
- This is local bounded evidence only. L3 remains unclaimed until the same contract is exercised in the controlled deployed staging environment and its artifact is retained.

## Result Semantics

- `pass`: at least one bounded target produced parse evidence; partial HTTP/parse failures remain separately visible.
- `warn`: zero eligible targets or no parse evidence.
- HTTP success and validation success are not treated as equivalent.
- `attempted_url_count=0` is a valid warn result and must not fabricate a zero-result success card.

## Status Mapping

- `400` or `422`: malformed/unknown/unconfirmed input
- `401`: unauthenticated
- `403`: non-Admin
- `409`: global execution already in flight
- `429`: per-user cooldown
- `500`: fixed planner/validator execution or local filesystem failure
- `502`: malformed, oversized or mismatched planner/validator report
- `503`: authorization/backend service unavailable
- `504`: bounded subprocess/backend timeout

No error response may expose a server path, secret, command, raw stderr or internal report body.

## Phase 3D Exit Gate

- focused Python safety and service tests pass
- TypeScript contract/proxy tests pass
- Phase 3D Playwright mock suite passes with zero unexpected external/write requests
- release-blocking combined Playwright suite includes Phase 3D
- authz matrix has zero unclassified/duplicate/failure counters
- secret and test-weakening scanners pass
- container build includes only the fixed safe scripts required by the FastAPI service
- the release-blocking Python job executes the service and network-safety regression suites
- the clean image fails closed without runtime inputs, while the documented read-only mount contract is covered by regression tests
- controlled staging evidence is still required before claiming L3
