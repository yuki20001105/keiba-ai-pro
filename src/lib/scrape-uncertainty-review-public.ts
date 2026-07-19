import {
  parsePublicReviewRecord,
  parseReviewResponseEnvelope,
  type ReviewValidationResult,
  type ScrapeUncertaintyReviewAction,
  type ScrapeUncertaintyReviewRecord,
} from '@/lib/scrape-uncertainty-review-server'

export type ReviewableRequestList = {
  version: 1
  requests: ScrapeUncertaintyReviewRecord[]
}

export type ReviewOnlyDecisionBody = {
  action: Extract<ScrapeUncertaintyReviewAction, 'approve' | 'reject'>
  expected_version: number
  reason: string
}

const LIST_ENVELOPE_KEYS = new Set(['version', 'requests'])
const CONTROL_CHARACTER_PATTERN = /[\x00-\x1f\x7f]/

function isObject(value: unknown): value is Record<string, unknown> {
  return typeof value === 'object' && value !== null && !Array.isArray(value)
}

function hasExactKeys(value: Record<string, unknown>, allowed: ReadonlySet<string>): boolean {
  const keys = Object.keys(value)
  return keys.length === allowed.size && keys.every(key => allowed.has(key))
}

function sameRequestSnapshot(
  left: ScrapeUncertaintyReviewRecord,
  right: ScrapeUncertaintyReviewRecord,
): boolean {
  return left.request.start_period === right.request.start_period
    && left.request.end_period === right.request.end_period
    && left.request.force_rescrape === right.request.force_rescrape
}

export function parseReviewableRequestList(
  raw: unknown,
  maximumRequests = 20,
  nowEpochMs = Date.now(),
): ReviewValidationResult<ReviewableRequestList> {
  if (!Number.isSafeInteger(maximumRequests) || maximumRequests < 1 || maximumRequests > 100) {
    return { ok: false, detail: 'invalid reviewable request limit' }
  }
  if (!Number.isFinite(nowEpochMs)) {
    return { ok: false, detail: 'invalid reviewable request clock' }
  }
  if (!isObject(raw) || !hasExactKeys(raw, LIST_ENVELOPE_KEYS) || raw.version !== 1) {
    return { ok: false, detail: 'review list response has an invalid envelope' }
  }
  if (!Array.isArray(raw.requests) || raw.requests.length > maximumRequests) {
    return { ok: false, detail: 'review list response exceeds the requested limit' }
  }

  const requests: ScrapeUncertaintyReviewRecord[] = []
  const requestIds = new Set<string>()
  for (const candidate of raw.requests) {
    const parsed = parsePublicReviewRecord(candidate)
    if (!parsed.ok) return parsed
    const record = parsed.value
    const requestedAt = Date.parse(record.requested_at)
    const expiresAt = Date.parse(record.expires_at)
    if (record.status !== 'pending_review'
      || record.approval_granted
      || !Number.isFinite(requestedAt)
      || !Number.isFinite(expiresAt)
      || expiresAt <= requestedAt
      || expiresAt <= nowEpochMs) {
      return { ok: false, detail: 'reviewable request has invalid pending invariants' }
    }
    if (requestIds.has(record.request_id)) {
      return { ok: false, detail: 'review list response contains a duplicate request' }
    }
    requestIds.add(record.request_id)
    requests.push(record)
  }

  return { ok: true, value: { version: 1, requests } }
}

export function normalizeReviewOnlyDecisionReason(value: unknown): string | null {
  if (typeof value !== 'string' || CONTROL_CHARACTER_PATTERN.test(value)) return null
  const normalized = value.trim().replace(/\s+/g, ' ')
  return normalized.length >= 20 && normalized.length <= 500 ? normalized : null
}

export function buildReviewOnlyDecisionBody(
  action: unknown,
  expectedVersion: unknown,
  reason: unknown,
): ReviewValidationResult<ReviewOnlyDecisionBody> {
  if (action !== 'approve' && action !== 'reject') {
    return { ok: false, detail: 'action must be approve or reject' }
  }
  if (!Number.isSafeInteger(expectedVersion) || (expectedVersion as number) < 1) {
    return { ok: false, detail: 'expected_version must be a positive integer' }
  }
  const normalizedReason = normalizeReviewOnlyDecisionReason(reason)
  if (!normalizedReason) {
    return { ok: false, detail: 'reason must be 20 to 500 characters without control characters' }
  }
  return {
    ok: true,
    value: {
      action,
      expected_version: expectedVersion as number,
      reason: normalizedReason,
    },
  }
}

export function parseCorrelatedReviewDecision(
  raw: unknown,
  previous: ScrapeUncertaintyReviewRecord,
  action: ReviewOnlyDecisionBody['action'],
  expectedDecisionReason: string,
): ReviewValidationResult<ScrapeUncertaintyReviewRecord> {
  const parsed = parseReviewResponseEnvelope(raw)
  if (!parsed.ok) return parsed
  const decided = parsed.value
  const expectedStatus = action === 'approve' ? 'approved' : 'rejected'
  const normalizedExpectedReason = normalizeReviewOnlyDecisionReason(expectedDecisionReason)

  if (!normalizedExpectedReason
    || previous.status !== 'pending_review'
    || decided.status !== expectedStatus
    || decided.version !== previous.version + 1
    || decided.request_id !== previous.request_id
    || decided.client_request_id !== previous.client_request_id
    || decided.request_payload_hash !== previous.request_payload_hash
    || decided.failure_kind !== previous.failure_kind
    || !sameRequestSnapshot(decided, previous)
    || decided.uncertainty_occurred_at !== previous.uncertainty_occurred_at
    || decided.reason !== previous.reason
    || decided.requested_at !== previous.requested_at
    || decided.expires_at !== previous.expires_at
    || decided.decided_by === null
    || decided.decided_at === null
    || decided.decision_reason !== normalizedExpectedReason
    || decided.approval_scope !== 'review_only'
    || decided.execution_enabled
    || decided.lock_release_allowed
    || decided.automatic_action_taken) {
    return { ok: false, detail: 'decision response does not match the selected review' }
  }

  return { ok: true, value: decided }
}
