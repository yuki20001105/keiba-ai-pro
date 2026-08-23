export const UNCERTAINTY_STORAGE_KEY = 'keiba-ai-pro:phase3b:scrape-uncertainty:v1'
export const UNCERTAINTY_REVIEW_STORAGE_KEY = 'keiba-ai-pro:phase3e:uncertainty-review:v1'

export type UncertaintyFailureKind = 'monitoring' | 'client_stop'

export type BatchRequestSnapshot = {
  startPeriod: string
  endPeriod: string
  forceRescrape: boolean
}

export type PersistedUncertaintyLock = {
  version: 1
  failureKind: UncertaintyFailureKind
  jobId?: string
  request: BatchRequestSnapshot
  occurredAt: string
}

export type ReviewAcknowledgements = {
  serverStateUnverified: true
  noUnlockOrRetry: true
}

export type PendingUncertaintyReview = {
  version: 1
  kind: 'jobless-uncertainty-review'
  status: 'pending_review'
  requestId: string
  lockFingerprint: string
  failureKind: UncertaintyFailureKind
  request: BatchRequestSnapshot
  uncertaintyOccurredAt: string
  requestedAt: string
  reason: string
  acknowledgements: ReviewAcknowledgements
  authoritative: false
  executionEnabled: false
  lockReleaseAllowed: false
}

const PERIOD_PATTERN = /^(\d{4})-(0[1-9]|1[0-2])$/
const JOB_ID_PATTERN = /^[A-Za-z0-9][A-Za-z0-9_-]{0,127}$/
const UUID_PATTERN = /^[0-9a-f]{8}-[0-9a-f]{4}-[1-8][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/i
const LOCK_KEYS = new Set(['version', 'failureKind', 'jobId', 'request', 'occurredAt'])
const SNAPSHOT_KEYS = new Set(['startPeriod', 'endPeriod', 'forceRescrape'])
const REVIEW_KEYS = new Set([
  'version',
  'kind',
  'status',
  'requestId',
  'lockFingerprint',
  'failureKind',
  'request',
  'uncertaintyOccurredAt',
  'requestedAt',
  'reason',
  'acknowledgements',
  'authoritative',
  'executionEnabled',
  'lockReleaseAllowed',
])
const ACK_KEYS = new Set(['serverStateUnverified', 'noUnlockOrRetry'])

function isObject(value: unknown): value is Record<string, unknown> {
  return typeof value === 'object' && value !== null && !Array.isArray(value)
}

function hasOnlyKeys(value: Record<string, unknown>, allowed: Set<string>): boolean {
  return Object.keys(value).every(key => allowed.has(key))
}

function isStrictIsoTimestamp(value: unknown): value is string {
  if (typeof value !== 'string' || value.length < 20 || value.length > 32) return false
  const parsed = new Date(value)
  return Number.isFinite(parsed.getTime()) && parsed.toISOString() === value
}

function parseSnapshot(value: unknown): BatchRequestSnapshot | null {
  if (!isObject(value) || !hasOnlyKeys(value, SNAPSHOT_KEYS) || Object.keys(value).length !== SNAPSHOT_KEYS.size) return null
  if (typeof value.startPeriod !== 'string' || typeof value.endPeriod !== 'string') return null
  if (!PERIOD_PATTERN.test(value.startPeriod) || !PERIOD_PATTERN.test(value.endPeriod)) return null
  if (value.startPeriod > value.endPeriod || typeof value.forceRescrape !== 'boolean') return null
  return {
    startPeriod: value.startPeriod,
    endPeriod: value.endPeriod,
    forceRescrape: value.forceRescrape,
  }
}

export function parsePersistedUncertaintyLock(value: unknown): PersistedUncertaintyLock | null {
  if (!isObject(value) || !hasOnlyKeys(value, LOCK_KEYS)) return null
  if (value.version !== 1) return null
  if (value.failureKind !== 'monitoring' && value.failureKind !== 'client_stop') return null
  const request = parseSnapshot(value.request)
  if (!request || !isStrictIsoTimestamp(value.occurredAt)) return null
  if (value.jobId !== undefined && (typeof value.jobId !== 'string' || !JOB_ID_PATTERN.test(value.jobId))) return null

  return {
    version: 1,
    failureKind: value.failureKind,
    request,
    occurredAt: value.occurredAt,
    ...(typeof value.jobId === 'string' ? { jobId: value.jobId } : {}),
  }
}

export function validateReviewReason(reason: string): string | null {
  if (/[\x00-\x1f\x7f]/.test(reason)) return null
  const normalized = reason.trim().replace(/\s+/g, ' ')
  if (normalized.length < 20) return null
  if (normalized.length > 500) return null
  return normalized
}

export function fingerprintUncertaintyLock(lock: PersistedUncertaintyLock): string {
  const canonical = [
    'v1',
    lock.failureKind,
    lock.request.startPeriod,
    lock.request.endPeriod,
    lock.request.forceRescrape ? '1' : '0',
    lock.occurredAt,
    lock.jobId ?? '',
  ].join('|')
  // This is only a deterministic local binding marker, not a signature or
  // authorization primitive. Two independent 32-bit streams keep it sync and
  // compatible with the project's ES2017 TypeScript target.
  let left = 0x811c9dc5
  let right = 0x9e3779b9
  for (const byte of new TextEncoder().encode(canonical)) {
    left = Math.imul(left ^ byte, 0x01000193) >>> 0
    right = Math.imul(right ^ ((byte + 0x9d) & 0xff), 0x85ebca6b) >>> 0
  }
  return `lock-v1-${left.toString(16).padStart(8, '0')}${right.toString(16).padStart(8, '0')}`
}

export function createPendingUncertaintyReview(input: {
  lock: PersistedUncertaintyLock
  requestId: string
  requestedAt: string
  reason: string
  serverStateUnverified: boolean
  noUnlockOrRetry: boolean
}): PendingUncertaintyReview | null {
  if (input.lock.jobId) return null
  if (!UUID_PATTERN.test(input.requestId) || !isStrictIsoTimestamp(input.requestedAt)) return null
  const reason = validateReviewReason(input.reason)
  if (!reason || !input.serverStateUnverified || !input.noUnlockOrRetry) return null
  return {
    version: 1,
    kind: 'jobless-uncertainty-review',
    status: 'pending_review',
    requestId: input.requestId,
    lockFingerprint: fingerprintUncertaintyLock(input.lock),
    failureKind: input.lock.failureKind,
    request: { ...input.lock.request },
    uncertaintyOccurredAt: input.lock.occurredAt,
    requestedAt: input.requestedAt,
    reason,
    acknowledgements: {
      serverStateUnverified: true,
      noUnlockOrRetry: true,
    },
    authoritative: false,
    executionEnabled: false,
    lockReleaseAllowed: false,
  }
}

export function parsePendingUncertaintyReview(value: unknown): PendingUncertaintyReview | null {
  if (!isObject(value) || !hasOnlyKeys(value, REVIEW_KEYS) || Object.keys(value).length !== REVIEW_KEYS.size) return null
  if (value.version !== 1 || value.kind !== 'jobless-uncertainty-review' || value.status !== 'pending_review') return null
  if (typeof value.requestId !== 'string' || !UUID_PATTERN.test(value.requestId)) return null
  if (typeof value.lockFingerprint !== 'string' || !/^lock-v1-[0-9a-f]{16}$/.test(value.lockFingerprint)) return null
  if (value.failureKind !== 'monitoring' && value.failureKind !== 'client_stop') return null
  const request = parseSnapshot(value.request)
  const reason = typeof value.reason === 'string' ? validateReviewReason(value.reason) : null
  if (!request || !reason || !isStrictIsoTimestamp(value.uncertaintyOccurredAt) || !isStrictIsoTimestamp(value.requestedAt)) return null
  if (!isObject(value.acknowledgements) || !hasOnlyKeys(value.acknowledgements, ACK_KEYS)) return null
  if (value.acknowledgements.serverStateUnverified !== true || value.acknowledgements.noUnlockOrRetry !== true) return null
  if (value.authoritative !== false || value.executionEnabled !== false || value.lockReleaseAllowed !== false) return null
  return {
    version: 1,
    kind: 'jobless-uncertainty-review',
    status: 'pending_review',
    requestId: value.requestId,
    lockFingerprint: value.lockFingerprint,
    failureKind: value.failureKind,
    request,
    uncertaintyOccurredAt: value.uncertaintyOccurredAt,
    requestedAt: value.requestedAt,
    reason,
    acknowledgements: {
      serverStateUnverified: true,
      noUnlockOrRetry: true,
    },
    authoritative: false,
    executionEnabled: false,
    lockReleaseAllowed: false,
  }
}

export function reviewMatchesLock(review: PendingUncertaintyReview, lock: PersistedUncertaintyLock): boolean {
  return !lock.jobId
    && review.lockFingerprint === fingerprintUncertaintyLock(lock)
    && review.failureKind === lock.failureKind
    && review.uncertaintyOccurredAt === lock.occurredAt
    && review.request.startPeriod === lock.request.startPeriod
    && review.request.endPeriod === lock.request.endPeriod
    && review.request.forceRescrape === lock.request.forceRescrape
}
