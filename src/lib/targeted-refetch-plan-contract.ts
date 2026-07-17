export type TargetedRefetchTarget = 'all' | 'race' | 'horse' | 'result' | 'pedigree' | 'odds'

export type TargetedRefetchRequest = {
  target: TargetedRefetchTarget
  max_targets: number
}

export type TargetedRefetchSafetyFlags = {
  read_only: true
  no_db_write: true
  no_http_access: true
  no_scrape_execute: true
  no_upsert: true
  no_force_refresh_execute: true
}

export type TargetedRefetchSample = {
  url: string
  url_type: 'result_page' | 'race_detail' | 'horse_detail' | 'pedigree'
  race_id: string | null
  horse_id: string | null
  reason: string
  column: string
  priority: string
  source: string
  recommended_next_action: string
}

export type TargetedRefetchPlan = {
  target: TargetedRefetchTarget
  verdict: 'pass' | 'warn'
  verdict_reason: 'targeted-refetch-dry-run'
  p0_total_count: number
  refetch_candidate_count: number
  unique_url_count: number
  race_result_url_count: number
  race_detail_url_count: number
  horse_detail_url_count: number
  pedigree_url_count: number
  excluded_schema_review_count: number
  excluded_domain_allowed_count: number
  excluded_metadata_repair_count: number
  excluded_cache_available_count: number
  reparse_candidate_count: number
  estimated_http_request_count: number
  estimated_runtime_seconds: number
  sample_urls: {
    result_page: TargetedRefetchSample[]
    race_detail: TargetedRefetchSample[]
    horse_detail: TargetedRefetchSample[]
    pedigree: TargetedRefetchSample[]
  }
  recommended_next_actions: string[]
  safety_flags: TargetedRefetchSafetyFlags
}

export type TargetedRefetchApiResponse = {
  dry_run: true
  read_only: true
  execution_enabled: false
  plan: TargetedRefetchPlan
}

const ALLOWED_TARGETS: TargetedRefetchTarget[] = ['all', 'race', 'horse', 'result', 'pedigree', 'odds']
const REQUEST_KEYS = new Set(['target', 'max_targets'])

const REQUIRED_SAFETY_FLAGS: Array<keyof TargetedRefetchSafetyFlags> = [
  'read_only',
  'no_db_write',
  'no_http_access',
  'no_scrape_execute',
  'no_upsert',
  'no_force_refresh_execute',
]

const REQUIRED_INTEGER_FIELDS = [
  'p0_total_count',
  'refetch_candidate_count',
  'unique_url_count',
  'race_result_url_count',
  'race_detail_url_count',
  'horse_detail_url_count',
  'pedigree_url_count',
  'excluded_schema_review_count',
  'excluded_domain_allowed_count',
  'excluded_metadata_repair_count',
  'excluded_cache_available_count',
  'reparse_candidate_count',
  'estimated_http_request_count',
] as const

const URL_BUCKETS = ['result_page', 'race_detail', 'horse_detail', 'pedigree'] as const
const MAX_ID_LENGTH = 32
const MAX_DISPLAY_LENGTH = 160
const MAX_ACTION_LENGTH = 200
const MAX_NEXT_ACTIONS = 20

function isObject(value: unknown): value is Record<string, unknown> {
  return typeof value === 'object' && value !== null && !Array.isArray(value)
}

function isIntegerNonNegative(value: unknown): value is number {
  return typeof value === 'number' && Number.isFinite(value) && Number.isInteger(value) && value >= 0
}

function isFiniteNonNegative(value: unknown): value is number {
  return typeof value === 'number' && Number.isFinite(value) && value >= 0
}

function hasControlChars(value: string): boolean {
  return /[\x00-\x1F\x7F]/.test(value)
}

function isForbiddenPathString(value: string): boolean {
  const text = value.trim()
  if (!text) return false
  // Reject absolute path/file URI patterns even when embedded in a sentence.
  if (/(^|\s)file:\/\//i.test(text)) return true
  if (/(^|\s)[A-Za-z]:[\\/]/.test(text)) return true
  if (/(^|\s)\\\\[^\s]+/.test(text)) return true
  if (/(^|\s)\/(?:[^\s/]+\/)*[^\s/]+/.test(text)) return true
  if (/(^|\s)~[\\/]/.test(text)) return true
  return false
}

function isSafeDisplayString(value: unknown, maxLen: number): value is string {
  if (typeof value !== 'string') return false
  if (!value.trim()) return false
  if (value.length > maxLen) return false
  if (hasControlChars(value)) return false
  if (isForbiddenPathString(value)) return false
  return true
}

function isSafeId(value: unknown): value is string {
  if (typeof value !== 'string') return false
  if (!value.trim()) return false
  if (value.length > MAX_ID_LENGTH) return false
  if (!/^[A-Za-z0-9_-]+$/.test(value)) return false
  return true
}

function isAllowedTarget(value: unknown): value is TargetedRefetchTarget {
  return typeof value === 'string' && (ALLOWED_TARGETS as string[]).includes(value)
}

function isAllowedSampleUrl(urlText: string): boolean {
  try {
    const url = new URL(urlText)
    if (url.protocol !== 'https:') return false
    if (url.hostname !== 'db.netkeiba.com') return false
    return /^\/(race|horse\/result|horse\/ped)\//.test(url.pathname)
  } catch {
    return false
  }
}

function parseSample(raw: unknown): TargetedRefetchSample | null {
  if (!isObject(raw)) return null
  if (typeof raw.url !== 'string' || !isAllowedSampleUrl(raw.url)) return null
  if (!URL_BUCKETS.includes(raw.url_type as (typeof URL_BUCKETS)[number])) return null

  const raceId = raw.race_id == null ? null : isSafeId(raw.race_id) ? raw.race_id : null
  const horseId = raw.horse_id == null ? null : isSafeId(raw.horse_id) ? raw.horse_id : null
  if (raw.race_id != null && raceId === null) return null
  if (raw.horse_id != null && horseId === null) return null

  if (!isSafeDisplayString(raw.reason, MAX_DISPLAY_LENGTH)) return null
  if (!isSafeDisplayString(raw.column, MAX_DISPLAY_LENGTH)) return null
  if (!isSafeDisplayString(raw.priority, MAX_DISPLAY_LENGTH)) return null
  if (!isSafeDisplayString(raw.source, MAX_DISPLAY_LENGTH)) return null
  if (!isSafeDisplayString(raw.recommended_next_action, MAX_ACTION_LENGTH)) return null

  return {
    url: raw.url,
    url_type: raw.url_type as TargetedRefetchSample['url_type'],
    race_id: raceId,
    horse_id: horseId,
    reason: raw.reason,
    column: raw.column,
    priority: raw.priority,
    source: raw.source,
    recommended_next_action: raw.recommended_next_action,
  }
}

export function validateTargetedRefetchRequestBody(raw: unknown):
  | { ok: true; value: TargetedRefetchRequest }
  | { ok: false; error: string } {
  if (!isObject(raw)) {
    return { ok: false, error: 'request body must be an object' }
  }

  for (const key of Object.keys(raw)) {
    if (!REQUEST_KEYS.has(key)) {
      return { ok: false, error: `unknown request key: ${key}` }
    }
  }

  if (Object.keys(raw).length === 0) {
    return { ok: true, value: { target: 'all', max_targets: 10 } }
  }

  if (raw.target !== undefined && typeof raw.target === 'string' && isForbiddenPathString(raw.target)) {
    return { ok: false, error: 'path-like input is not allowed' }
  }

  const target = raw.target ?? 'all'
  if (!isAllowedTarget(target)) {
    return { ok: false, error: 'invalid target' }
  }

  const maxTargets = raw.max_targets ?? 10
  if (!isIntegerNonNegative(maxTargets) || maxTargets < 1 || maxTargets > 50) {
    return { ok: false, error: 'invalid max_targets' }
  }

  return {
    ok: true,
    value: {
      target,
      max_targets: maxTargets,
    },
  }
}

export function validateTargetedRefetchPlanReport(
  raw: unknown,
  expected: TargetedRefetchRequest
):
  | { ok: true; plan: TargetedRefetchPlan }
  | { ok: false; error: string } {
  if (!isObject(raw)) {
    return { ok: false, error: 'planner report must be an object' }
  }

  if (raw.target !== expected.target) {
    return { ok: false, error: 'planner report target mismatch' }
  }

  if (raw.verdict !== 'pass' && raw.verdict !== 'warn') {
    return { ok: false, error: 'invalid verdict' }
  }

  if (raw.verdict_reason !== 'targeted-refetch-dry-run') {
    return { ok: false, error: 'invalid verdict_reason' }
  }

  for (const key of REQUIRED_INTEGER_FIELDS) {
    if (!isIntegerNonNegative(raw[key])) {
      return { ok: false, error: `invalid numeric field: ${key}` }
    }
  }

  if (!isFiniteNonNegative(raw.estimated_runtime_seconds)) {
    return { ok: false, error: 'invalid numeric field: estimated_runtime_seconds' }
  }

  const safetyFlagsRaw = raw.safety_flags
  if (!isObject(safetyFlagsRaw)) {
    return { ok: false, error: 'missing safety_flags' }
  }
  for (const key of REQUIRED_SAFETY_FLAGS) {
    if (safetyFlagsRaw[key] !== true) {
      return { ok: false, error: `safety flag must be true: ${key}` }
    }
  }

  if (!Array.isArray(raw.recommended_next_actions)) {
    return { ok: false, error: 'invalid recommended_next_actions' }
  }
  if (raw.recommended_next_actions.length > MAX_NEXT_ACTIONS) {
    return { ok: false, error: 'recommended_next_actions exceeds limit' }
  }
  if (raw.recommended_next_actions.some(v => !isSafeDisplayString(v, MAX_ACTION_LENGTH))) {
    return { ok: false, error: 'invalid recommended_next_actions' }
  }

  if (!isObject(raw.sample_urls)) {
    return { ok: false, error: 'invalid sample_urls' }
  }

  const parsedBuckets: TargetedRefetchPlan['sample_urls'] = {
    result_page: [],
    race_detail: [],
    horse_detail: [],
    pedigree: [],
  }

  for (const bucket of URL_BUCKETS) {
    const rawList = raw.sample_urls[bucket]
    if (!Array.isArray(rawList)) {
      return { ok: false, error: `invalid sample_urls.${bucket}` }
    }
    if (rawList.length > expected.max_targets) {
      return { ok: false, error: `sample_urls.${bucket} exceeds max_targets` }
    }

    for (const item of rawList) {
      const parsed = parseSample(item)
      if (!parsed) {
        return { ok: false, error: `invalid sample in bucket: ${bucket}` }
      }
      if (parsed.url_type !== bucket) {
        return { ok: false, error: `url_type mismatch in bucket: ${bucket}` }
      }
      parsedBuckets[bucket].push(parsed)
    }
  }

  const plan: TargetedRefetchPlan = {
    target: expected.target,
    verdict: raw.verdict,
    verdict_reason: raw.verdict_reason,
    p0_total_count: raw.p0_total_count as number,
    refetch_candidate_count: raw.refetch_candidate_count as number,
    unique_url_count: raw.unique_url_count as number,
    race_result_url_count: raw.race_result_url_count as number,
    race_detail_url_count: raw.race_detail_url_count as number,
    horse_detail_url_count: raw.horse_detail_url_count as number,
    pedigree_url_count: raw.pedigree_url_count as number,
    excluded_schema_review_count: raw.excluded_schema_review_count as number,
    excluded_domain_allowed_count: raw.excluded_domain_allowed_count as number,
    excluded_metadata_repair_count: raw.excluded_metadata_repair_count as number,
    excluded_cache_available_count: raw.excluded_cache_available_count as number,
    reparse_candidate_count: raw.reparse_candidate_count as number,
    estimated_http_request_count: raw.estimated_http_request_count as number,
    estimated_runtime_seconds: raw.estimated_runtime_seconds as number,
    sample_urls: parsedBuckets,
    recommended_next_actions: raw.recommended_next_actions as string[],
    safety_flags: {
      read_only: true,
      no_db_write: true,
      no_http_access: true,
      no_scrape_execute: true,
      no_upsert: true,
      no_force_refresh_execute: true,
    },
  }

  return { ok: true, plan }
}
