export type LiveValidationTarget = 'all' | 'race' | 'horse' | 'result' | 'pedigree' | 'odds'

export type LiveValidationUrlType =
  | 'all'
  | 'race-result'
  | 'race-detail'
  | 'horse-detail'
  | 'pedigree'

export type LiveValidationRequest = {
  target: LiveValidationTarget
  url_type: LiveValidationUrlType
  max_urls: number
  confirm_live_fetch: true
}

export type LiveValidationSample = {
  url: string
  url_type: 'result_page' | 'race_detail' | 'horse_detail' | 'pedigree'
  race_id: string | null
  horse_id: string | null
  http_status: number
  parse_status: 'http_error' | 'parse_success' | 'parse_failed'
  missing_fields_before: string[]
  fields_found_after: string[]
  would_fix_columns: string[]
  action: string
  reason: string
  recommended_next_action: string
}

export type LiveValidationRateLimitPolicy = {
  max_urls: number
  max_supported_urls: number
  min_interval_sec: number
  max_retries: 1
  retry_base_sec: number
  retry_jitter_sec: number
  retry_after_enabled: false
  max_retry_after_sec: number
  per_request_timeout_sec: number
  total_timeout_sec: number
  max_body_bytes: number
  circuit_breaker: {
    threshold: number
    cooldown_sec: number
  }
  parallelism: 1
  fetch_pipeline_used: true
}

export type LiveValidationSafetyFlags = {
  small_live_validation_only: true
  max_urls_limited: true
  no_db_write: true
  no_upsert: true
  no_repair_execute: true
  no_production_table_write: true
  no_force_refresh_execute: true
  no_bulk_refetch: true
  redirects_disabled: true
  bounded_response_body: true
  bounded_total_runtime: true
}

export type LiveValidationResult = {
  target: LiveValidationTarget
  url_type: LiveValidationUrlType
  max_urls_applied: number
  attempted_url_count: number
  http_success_count: number
  http_error_count: number
  parse_success_count: number
  parse_error_count: number
  would_fix_count: number
  would_not_fix_count: number
  no_downgrade_count: number
  repairable_count: number
  elapsed_seconds: number
  estimated_full_refetch_runtime_seconds: number
  excluded_schema_review_count: number
  excluded_domain_allowed_count: number
  excluded_metadata_repair_count: number
  excluded_cache_available_count: number
  sample_results: LiveValidationSample[]
  recommended_next_actions: string[]
  rate_limit_policy: LiveValidationRateLimitPolicy
  safety_flags: LiveValidationSafetyFlags
  verdict: 'pass' | 'warn'
  verdict_reason: 'small-live-validation'
}

export type LiveValidationApiResponse = {
  live_validation: true
  bounded: true
  external_http: true
  read_only: true
  execution_enabled: false
  result: LiveValidationResult
}

const TARGETS: readonly LiveValidationTarget[] = ['all', 'race', 'horse', 'result', 'pedigree', 'odds']
const URL_TYPES: readonly LiveValidationUrlType[] = ['all', 'race-result', 'race-detail', 'horse-detail', 'pedigree']
const REQUEST_KEYS = new Set(['target', 'url_type', 'max_urls', 'confirm_live_fetch'])
const MAX_DISPLAY_LENGTH = 240
const MAX_ACTIONS = 12
const MAX_FIELDS = 32

function isObject(value: unknown): value is Record<string, unknown> {
  return typeof value === 'object' && value !== null && !Array.isArray(value)
}

function isIntegerBetween(value: unknown, min: number, max: number): value is number {
  return typeof value === 'number' && Number.isFinite(value) && Number.isInteger(value) && value >= min && value <= max
}

function isNonNegativeFinite(value: unknown): value is number {
  return typeof value === 'number' && Number.isFinite(value) && value >= 0
}

function containsUnsafeDisplayContent(value: string): boolean {
  if (/\x00|[\x01-\x1f\x7f]/.test(value)) return true
  if (/file:\/\//i.test(value)) return true
  if (/[A-Za-z]:[\\/]/.test(value)) return true
  if (/\\\\[A-Za-z0-9.$_-]+\\/.test(value)) return true
  if (/(^|[^A-Za-z0-9_])\/[A-Za-z0-9._-]/.test(value)) return true
  if (/(^|[^A-Za-z0-9_])(?:~|\.\.)[\\/]/.test(value)) return true
  return false
}

function isSafeDisplayString(value: unknown, maxLength = MAX_DISPLAY_LENGTH): value is string {
  return typeof value === 'string' && value.trim().length > 0 && value.length <= maxLength && !containsUnsafeDisplayContent(value)
}

function isSafeNullableId(value: unknown, length: number): value is string | null {
  return value === null || (typeof value === 'string' && new RegExp(`^\\d{${length}}$`).test(value))
}

function isAllowedTarget(value: unknown): value is LiveValidationTarget {
  return typeof value === 'string' && (TARGETS as readonly string[]).includes(value)
}

function isAllowedUrlType(value: unknown): value is LiveValidationUrlType {
  return typeof value === 'string' && (URL_TYPES as readonly string[]).includes(value)
}

function parseAllowedNetkeibaUrl(raw: unknown, urlType: LiveValidationSample['url_type']): boolean {
  if (typeof raw !== 'string') return false
  if (!raw.startsWith('https://db.netkeiba.com/')) return false
  try {
    const url = new URL(raw)
    if (url.protocol !== 'https:' || url.hostname !== 'db.netkeiba.com') return false
    if (url.username || url.password || url.port || url.search || url.hash) return false
    const patterns: Record<LiveValidationSample['url_type'], RegExp> = {
      result_page: /^\/race\/\d{12}\/$/,
      race_detail: /^\/race\/\d{12}\/$/,
      horse_detail: /^\/horse\/result\/\d{10}\/$/,
      pedigree: /^\/horse\/ped\/\d{10}\/$/,
    }
    return patterns[urlType].test(url.pathname)
  } catch {
    return false
  }
}

function parseStringList(raw: unknown, maxItems: number): string[] | null {
  if (!Array.isArray(raw) || raw.length > maxItems) return null
  if (raw.some(item => !isSafeDisplayString(item, 120))) return null
  if (new Set(raw).size !== raw.length) return null
  return [...raw] as string[]
}

function parseSample(raw: unknown): LiveValidationSample | null {
  if (!isObject(raw)) return null
  const urlType = raw.url_type
  if (urlType !== 'result_page' && urlType !== 'race_detail' && urlType !== 'horse_detail' && urlType !== 'pedigree') return null
  if (!parseAllowedNetkeibaUrl(raw.url, urlType)) return null
  if (!isSafeNullableId(raw.race_id, 12) || !isSafeNullableId(raw.horse_id, 10)) return null
  const pathId = (raw.url as string).split('/').filter(Boolean).at(-1)
  if (urlType === 'result_page' || urlType === 'race_detail') {
    if (raw.race_id !== pathId) return null
  } else if (raw.horse_id !== pathId || raw.race_id !== null) {
    return null
  }
  if (!isIntegerBetween(raw.http_status, 0, 599)) return null
  if (raw.parse_status !== 'http_error' && raw.parse_status !== 'parse_success' && raw.parse_status !== 'parse_failed') return null

  const missingFields = parseStringList(raw.missing_fields_before, MAX_FIELDS)
  const foundFields = parseStringList(raw.fields_found_after, MAX_FIELDS)
  const wouldFixColumns = parseStringList(raw.would_fix_columns, MAX_FIELDS)
  if (!missingFields || !foundFields || !wouldFixColumns) return null
  if (!isSafeDisplayString(raw.action) || !isSafeDisplayString(raw.reason) || !isSafeDisplayString(raw.recommended_next_action)) return null

  const foundFieldSet = new Set(foundFields)
  const expectedWouldFix = raw.reason === 'consistency:race_without_horse_data'
    ? (missingFields.includes('(check)') && foundFieldSet.has('(check)') ? ['(check)'] : [])
    : missingFields.filter(name => foundFieldSet.has(name))
  if (expectedWouldFix.length !== wouldFixColumns.length
    || expectedWouldFix.some((name, index) => wouldFixColumns[index] !== name)) return null

  const httpSucceeded = raw.http_status >= 200 && raw.http_status < 300
  if (httpSucceeded === (raw.parse_status === 'http_error')) return null
  if (raw.parse_status === 'http_error') {
    if (raw.action !== 'http_error' || wouldFixColumns.length !== 0) return null
  } else if (raw.parse_status === 'parse_failed') {
    if (!raw.action.startsWith('parse_failed:') || wouldFixColumns.length !== 0) return null
  } else if (wouldFixColumns.length > 0) {
    if (raw.action !== 'would-fix') return null
  } else if (raw.action !== 'no-downgrade-skip') {
    return null
  }

  return {
    url: raw.url as string,
    url_type: urlType,
    race_id: raw.race_id,
    horse_id: raw.horse_id,
    http_status: raw.http_status,
    parse_status: raw.parse_status,
    missing_fields_before: missingFields,
    fields_found_after: foundFields,
    would_fix_columns: wouldFixColumns,
    action: raw.action,
    reason: raw.reason,
    recommended_next_action: raw.recommended_next_action,
  }
}

export function validateLiveValidationRequestBody(raw: unknown):
  | { ok: true; value: LiveValidationRequest }
  | { ok: false; error: string } {
  if (!isObject(raw)) return { ok: false, error: 'request body must be an object' }
  for (const key of Object.keys(raw)) {
    if (!REQUEST_KEYS.has(key)) return { ok: false, error: `unknown request key: ${key}` }
  }
  if (Object.keys(raw).length !== REQUEST_KEYS.size) return { ok: false, error: 'all request fields are required' }
  if (!isAllowedTarget(raw.target)) return { ok: false, error: 'invalid target' }
  if (!isAllowedUrlType(raw.url_type)) return { ok: false, error: 'invalid url_type' }
  if (!isIntegerBetween(raw.max_urls, 1, 3)) return { ok: false, error: 'max_urls must be an integer between 1 and 3' }
  if (raw.confirm_live_fetch !== true) return { ok: false, error: 'explicit live fetch confirmation is required' }
  return {
    ok: true,
    value: {
      target: raw.target,
      url_type: raw.url_type,
      max_urls: raw.max_urls,
      confirm_live_fetch: true,
    },
  }
}

function parseRateLimitPolicy(raw: unknown, expected: LiveValidationRequest): LiveValidationRateLimitPolicy | null {
  if (!isObject(raw)) return null
  if (raw.max_urls !== expected.max_urls || !isIntegerBetween(raw.max_supported_urls, 3, 10)) return null
  if (!isNonNegativeFinite(raw.min_interval_sec) || raw.min_interval_sec < 1) return null
  if (raw.max_retries !== 1) return null
  if (!isNonNegativeFinite(raw.retry_base_sec) || !isNonNegativeFinite(raw.retry_jitter_sec)) return null
  if (raw.retry_after_enabled !== false) return null
  if (!isNonNegativeFinite(raw.max_retry_after_sec) || raw.max_retry_after_sec > 10) return null
  if (!isNonNegativeFinite(raw.per_request_timeout_sec) || raw.per_request_timeout_sec > 15) return null
  if (!isNonNegativeFinite(raw.total_timeout_sec) || raw.total_timeout_sec > 90) return null
  if (!isIntegerBetween(raw.max_body_bytes, 1, 2 * 1024 * 1024)) return null
  if (raw.parallelism !== 1 || raw.fetch_pipeline_used !== true) return null
  if (!isObject(raw.circuit_breaker)) return null
  if (!isIntegerBetween(raw.circuit_breaker.threshold, 1, 10)) return null
  if (!isNonNegativeFinite(raw.circuit_breaker.cooldown_sec) || raw.circuit_breaker.cooldown_sec > 120) return null
  return {
    max_urls: raw.max_urls,
    max_supported_urls: raw.max_supported_urls,
    min_interval_sec: raw.min_interval_sec,
    max_retries: raw.max_retries,
    retry_base_sec: raw.retry_base_sec,
    retry_jitter_sec: raw.retry_jitter_sec,
    retry_after_enabled: false,
    max_retry_after_sec: raw.max_retry_after_sec,
    per_request_timeout_sec: raw.per_request_timeout_sec,
    total_timeout_sec: raw.total_timeout_sec,
    max_body_bytes: raw.max_body_bytes,
    circuit_breaker: {
      threshold: raw.circuit_breaker.threshold,
      cooldown_sec: raw.circuit_breaker.cooldown_sec,
    },
    parallelism: 1,
    fetch_pipeline_used: true,
  }
}

function parseSafetyFlags(raw: unknown): LiveValidationSafetyFlags | null {
  if (!isObject(raw)) return null
  const required = [
    'small_live_validation_only',
    'max_urls_limited',
    'no_db_write',
    'no_upsert',
    'no_repair_execute',
    'no_production_table_write',
    'no_force_refresh_execute',
    'no_bulk_refetch',
    'redirects_disabled',
    'bounded_response_body',
    'bounded_total_runtime',
  ] as const
  if (required.some(key => raw[key] !== true)) return null
  return Object.fromEntries(required.map(key => [key, true])) as LiveValidationSafetyFlags
}

export function validateLiveValidationApiResponse(
  raw: unknown,
  expected: LiveValidationRequest,
): { ok: true; value: LiveValidationApiResponse } | { ok: false; error: string } {
  if (!isObject(raw)) return { ok: false, error: 'response must be an object' }
  if (raw.live_validation !== true || raw.bounded !== true || raw.external_http !== true || raw.read_only !== true || raw.execution_enabled !== false) {
    return { ok: false, error: 'invalid safety envelope' }
  }
  if (!isObject(raw.result)) return { ok: false, error: 'missing result' }
  const result = raw.result
  if (result.target !== expected.target || result.url_type !== expected.url_type || result.max_urls_applied !== expected.max_urls) {
    return { ok: false, error: 'response request mismatch' }
  }

  const countKeys = [
    'attempted_url_count',
    'http_success_count',
    'http_error_count',
    'parse_success_count',
    'parse_error_count',
    'would_fix_count',
    'would_not_fix_count',
    'no_downgrade_count',
    'repairable_count',
    'excluded_schema_review_count',
    'excluded_domain_allowed_count',
    'excluded_metadata_repair_count',
    'excluded_cache_available_count',
  ] as const
  for (const key of countKeys) {
    if (!isIntegerBetween(result[key], 0, 1000000)) return { ok: false, error: `invalid count: ${key}` }
  }
  const attempted = result.attempted_url_count as number
  if (attempted > expected.max_urls) return { ok: false, error: 'attempted_url_count exceeds max_urls' }
  if ((result.http_success_count as number) + (result.http_error_count as number) !== attempted) return { ok: false, error: 'HTTP counts inconsistent' }
  if ((result.parse_success_count as number) + (result.parse_error_count as number) !== attempted) return { ok: false, error: 'parse counts inconsistent' }
  if ((result.would_fix_count as number) + (result.would_not_fix_count as number) !== attempted) return { ok: false, error: 'would-fix counts inconsistent' }
  if (result.repairable_count !== result.would_fix_count) return { ok: false, error: 'repairable count inconsistent' }
  if (!isNonNegativeFinite(result.elapsed_seconds) || result.elapsed_seconds > 90) return { ok: false, error: 'invalid elapsed_seconds' }
  if (!isNonNegativeFinite(result.estimated_full_refetch_runtime_seconds)) return { ok: false, error: 'invalid estimated runtime' }
  if (!Array.isArray(result.sample_results) || result.sample_results.length !== attempted) return { ok: false, error: 'invalid sample_results count' }
  const samples = result.sample_results.map(parseSample)
  if (samples.some(sample => sample === null)) return { ok: false, error: 'invalid sample result' }
  const approvedSamples = samples as LiveValidationSample[]
  if (new Set(approvedSamples.map(sample => sample.url)).size !== approvedSamples.length) {
    return { ok: false, error: 'duplicate sample URL' }
  }
  const expectedSampleUrlType: Partial<Record<LiveValidationUrlType, LiveValidationSample['url_type']>> = {
    'race-result': 'result_page',
    'race-detail': 'race_detail',
    'horse-detail': 'horse_detail',
    pedigree: 'pedigree',
  }
  const requiredSampleUrlType = expectedSampleUrlType[expected.url_type]
  if (requiredSampleUrlType && approvedSamples.some(sample => sample.url_type !== requiredSampleUrlType)) {
    return { ok: false, error: 'sample URL type does not match request' }
  }
  const derivedHttpSuccess = approvedSamples.filter(sample => sample.http_status >= 200 && sample.http_status < 300).length
  const derivedParseSuccess = approvedSamples.filter(sample => sample.parse_status === 'parse_success').length
  const derivedWouldFix = approvedSamples.filter(sample => sample.would_fix_columns.length > 0).length
  const derivedNoDowngrade = approvedSamples.filter(sample => sample.action === 'no-downgrade-skip').length
  if (
    result.http_success_count !== derivedHttpSuccess ||
    result.http_error_count !== attempted - derivedHttpSuccess ||
    result.parse_success_count !== derivedParseSuccess ||
    result.parse_error_count !== attempted - derivedParseSuccess ||
    result.would_fix_count !== derivedWouldFix ||
    result.would_not_fix_count !== attempted - derivedWouldFix ||
    result.no_downgrade_count !== derivedNoDowngrade ||
    result.repairable_count !== derivedWouldFix
  ) {
    return { ok: false, error: 'sample aggregate counts inconsistent' }
  }
  const actions = parseStringList(result.recommended_next_actions, MAX_ACTIONS)
  if (!actions) return { ok: false, error: 'invalid recommended_next_actions' }
  const ratePolicy = parseRateLimitPolicy(result.rate_limit_policy, expected)
  if (!ratePolicy) return { ok: false, error: 'invalid rate_limit_policy' }
  const safetyFlags = parseSafetyFlags(result.safety_flags)
  if (!safetyFlags) return { ok: false, error: 'invalid safety_flags' }
  const derivedVerdict = attempted > 0 && derivedParseSuccess > 0 ? 'pass' : 'warn'
  if (result.verdict !== derivedVerdict) return { ok: false, error: 'verdict inconsistent with samples' }
  if (result.verdict_reason !== 'small-live-validation') return { ok: false, error: 'invalid verdict_reason' }

  return {
    ok: true,
    value: {
      live_validation: true,
      bounded: true,
      external_http: true,
      read_only: true,
      execution_enabled: false,
      result: {
        target: expected.target,
        url_type: expected.url_type,
        max_urls_applied: result.max_urls_applied,
        attempted_url_count: attempted,
        http_success_count: result.http_success_count as number,
        http_error_count: result.http_error_count as number,
        parse_success_count: result.parse_success_count as number,
        parse_error_count: result.parse_error_count as number,
        would_fix_count: result.would_fix_count as number,
        would_not_fix_count: result.would_not_fix_count as number,
        no_downgrade_count: result.no_downgrade_count as number,
        repairable_count: result.repairable_count as number,
        elapsed_seconds: result.elapsed_seconds,
        estimated_full_refetch_runtime_seconds: result.estimated_full_refetch_runtime_seconds,
        excluded_schema_review_count: result.excluded_schema_review_count as number,
        excluded_domain_allowed_count: result.excluded_domain_allowed_count as number,
        excluded_metadata_repair_count: result.excluded_metadata_repair_count as number,
        excluded_cache_available_count: result.excluded_cache_available_count as number,
        sample_results: approvedSamples,
        recommended_next_actions: actions,
        rate_limit_policy: ratePolicy,
        safety_flags: safetyFlags,
        verdict: derivedVerdict,
        verdict_reason: 'small-live-validation',
      },
    },
  }
}
