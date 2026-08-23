# Phase 2 Quota Contract

## Unit definition
- 1 analysis request for /api/analyze_race = 1 quota unit.
- 1 prediction request for /api/predict = 1 quota unit.
- /api/analyze_races_batch reserves quota by unique race_id count (deduplicated before reservation).

## Atomicity
- Batch quota is pre-reserved before any per-race analysis begins.
- If remaining quota is insufficient, API returns 429 and processes zero races.
- Partial consume/partial success behavior is not allowed.

## Response semantics (RPC)
Allowed values only:
- -1: premium unlimited.
- -999: quota exceeded.
- >= 0: remaining count after consume.

Backend failure handling:
- 503 fail-closed when: None, bool, unparsable value, invalid negative value (<0 except -1/-999), RPC exception, client unavailable.
- Local/test bypass is disabled by default and only enabled explicitly via PRED_LIMIT_ALLOW_FAIL_OPEN=true.

## Batch RPC migration
- Added Supabase function: public.consume_pred_count_batch(UUID, INT).
- SECURITY DEFINER + SET search_path = public.
- Input guard: p_units must be 1..100.
- Uses row-level lock (SELECT ... FOR UPDATE) and single UPDATE to keep atomicity.
- Existing consume_pred_count remains for backward compatibility.
