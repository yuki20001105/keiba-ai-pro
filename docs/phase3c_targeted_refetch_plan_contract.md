# Phase 3C Targeted Refetch Plan Contract

## Endpoint
- route: /api/scrape/targeted-refetch-plan
- method: POST
- policy: PremiumOrAdmin
- runtime: nodejs
- mode: read-only planner only

## Scope and Safety
This endpoint is a dry-run planner for Phase 3C.
It MUST NOT perform any of the following:
- external HTTP requests
- database writes
- upsert/repair execution
- scrape execution
- force refresh execution
- live validation execution
- approval unlock for uncertainty lock

Existing Phase 3B uncertainty lock behavior remains fail-closed and unchanged.

## Request Contract
Allowed request keys only:
- target: all | race | horse | result | pedigree | odds
- max_targets: integer in [1, 50]

Validation requirements:
- malformed JSON is rejected with 400
- null/array/primitive JSON body is rejected with 400
- only empty object {} may use defaults (target=all, max_targets=10)
- unknown keys are rejected with 400
- path-like input is rejected with 400
- absolute path input is rejected with 400

## Script Invocation Contract
- server-owned fixed script path only: scripts/plan_p0_targeted_refetch.py
- child_process.spawn with shell=false
- single-flight guard in process (second concurrent request is rejected)
- timeout required
- stdout/stderr size cap required
- planner output file size cap required (pre-read)
- unique temp output path per request
- temp output cleanup on success/error/timeout/oversize/parse-failure/spawn-error
- temp directory cleanup with recursive remove on server-owned tmpDir only

## Planner Report Validation (Fail-closed)
A planner report is accepted only if all conditions below are true.

Required object fields:
- target matches request.target
- verdict is pass or warn
- verdict_reason is targeted-refetch-dry-run

Required numeric fields (finite integer, >= 0):
- p0_total_count
- refetch_candidate_count
- unique_url_count
- race_result_url_count
- race_detail_url_count
- horse_detail_url_count
- pedigree_url_count
- excluded_schema_review_count
- excluded_domain_allowed_count
- excluded_metadata_repair_count
- excluded_cache_available_count
- reparse_candidate_count
- estimated_http_request_count

Required numeric field (finite number, >= 0):
- estimated_runtime_seconds

Required sample fields:
- sample_urls buckets: result_page, race_detail, horse_detail, pedigree
- each bucket length <= request.max_targets
- each sample URL must be https://db.netkeiba.com/(race|horse/result|horse/ped)/...

Required actions:
- recommended_next_actions is string[]
- recommended_next_actions length is capped

Required string safety:
- race_id / horse_id: non-empty, [A-Za-z0-9_-], bounded length
- reason / column / priority / source / recommended_next_action / recommended_next_actions[]:
	non-empty, bounded length, no control chars, no NUL
- filesystem path-like strings are rejected (Windows absolute, UNC, Unix absolute, file://, home path)

Required safety_flags (all true):
- read_only
- no_db_write
- no_http_access
- no_scrape_execute
- no_upsert
- no_force_refresh_execute

Any missing/invalid condition returns 502.
If report contains server filesystem path-like values, return 502.

## Response Contract
Success response:
- dry_run: true
- read_only: true
- execution_enabled: false
- plan: validated planner object

Response exclusions:
- no input_audit path
- no db path
- no output file path
- no temp path
- no raw stderr

## HTTP and Cache Behavior
- Cache-Control: no-store
- auth failures map to 401/403/503 from verifyRequestAuth
- invalid input maps to 400
- planner execution failure maps to 500
- planner contract failure maps to 502

## Non-goals in Phase 3C
- no repair/refetch execution endpoint
- no PUT/DELETE endpoint for targeted-refetch-plan
- no live validation endpoint wiring
- no lock-release/approval feature for jobId-less uncertainty
