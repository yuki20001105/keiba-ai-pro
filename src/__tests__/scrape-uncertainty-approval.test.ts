import { describe, expect, it } from 'vitest'
import {
  createPendingUncertaintyReview,
  fingerprintUncertaintyLock,
  parsePendingUncertaintyReview,
  parsePersistedUncertaintyLock,
  reviewMatchesLock,
  validateReviewReason,
  type PersistedUncertaintyLock,
} from '@/lib/scrape-uncertainty-approval'

const lock: PersistedUncertaintyLock = {
  version: 1,
  failureKind: 'monitoring',
  request: { startPeriod: '2026-01', endPeriod: '2026-02', forceRescrape: false },
  occurredAt: '2026-07-18T00:00:00.000Z',
}

function validReview() {
  const review = createPendingUncertaintyReview({
    lock,
    requestId: '11111111-1111-4111-8111-111111111111',
    requestedAt: '2026-07-18T00:01:00.000Z',
    reason: 'サーバー状態を確認できないため、重複実行を避けて監査担当へ確認を依頼します。',
    serverStateUnverified: true,
    noUnlockOrRetry: true,
  })
  if (!review) throw new Error('fixture review must be valid')
  return review
}

describe('Phase 3E non-executable uncertainty review contract', () => {
  it('strictly parses a valid persisted lock and creates pending_review only', () => {
    expect(parsePersistedUncertaintyLock(lock)).toEqual(lock)
    const review = validReview()
    expect(review.status).toBe('pending_review')
    expect(review.authoritative).toBe(false)
    expect(review.executionEnabled).toBe(false)
    expect(review.lockReleaseAllowed).toBe(false)
    expect(parsePendingUncertaintyReview(review)).toEqual(review)
    expect(reviewMatchesLock(review, lock)).toBe(true)
  })

  it('creates a deterministic fingerprint bound to every lock field', () => {
    const original = fingerprintUncertaintyLock(lock)
    expect(fingerprintUncertaintyLock({ ...lock })).toBe(original)
    expect(fingerprintUncertaintyLock({ ...lock, failureKind: 'client_stop' })).not.toBe(original)
    expect(fingerprintUncertaintyLock({ ...lock, occurredAt: '2026-07-18T00:00:01.000Z' })).not.toBe(original)
    expect(fingerprintUncertaintyLock({ ...lock, request: { ...lock.request, forceRescrape: true } })).not.toBe(original)
  })

  it('rejects short, oversized and control-character reasons', () => {
    expect(validateReviewReason('短い理由')).toBeNull()
    expect(validateReviewReason('a'.repeat(501))).toBeNull()
    expect(validateReviewReason(`valid reason with control\u0000data`)).toBeNull()
  })

  it('requires both acknowledgements and a valid UUID request id', () => {
    const base = {
      lock,
      requestId: '11111111-1111-4111-8111-111111111111',
      requestedAt: '2026-07-18T00:01:00.000Z',
      reason: 'サーバー状態が未確認であるため、監査担当者へ状態確認を依頼します。',
    }
    expect(createPendingUncertaintyReview({ ...base, serverStateUnverified: false, noUnlockOrRetry: true })).toBeNull()
    expect(createPendingUncertaintyReview({ ...base, serverStateUnverified: true, noUnlockOrRetry: false })).toBeNull()
    expect(createPendingUncertaintyReview({ ...base, requestId: 'not-a-uuid', serverStateUnverified: true, noUnlockOrRetry: true })).toBeNull()
  })

  it('does not create the jobless scaffold for a job-bound lock', () => {
    expect(createPendingUncertaintyReview({
      lock: { ...lock, jobId: 'job-known' },
      requestId: '11111111-1111-4111-8111-111111111111',
      requestedAt: '2026-07-18T00:01:00.000Z',
      reason: 'サーバー状態が未確認であるため、監査担当者へ状態確認を依頼します。',
      serverStateUnverified: true,
      noUnlockOrRetry: true,
    })).toBeNull()
  })

  it('rejects malformed locks instead of silently normalizing them', () => {
    expect(parsePersistedUncertaintyLock({ ...lock, occurredAt: 'yesterday' })).toBeNull()
    expect(parsePersistedUncertaintyLock({ ...lock, request: { ...lock.request, startPeriod: '2026-13' } })).toBeNull()
    expect(parsePersistedUncertaintyLock({ ...lock, request: { ...lock.request, startPeriod: '2026-03' } })).toBeNull()
    expect(parsePersistedUncertaintyLock({ ...lock, unlock: true })).toBeNull()
  })

  it('rejects approved, unlocked or otherwise unknown review fields', () => {
    const review = validReview()
    expect(parsePendingUncertaintyReview({ ...review, status: 'approved' })).toBeNull()
    expect(parsePendingUncertaintyReview({ ...review, executionEnabled: true })).toBeNull()
    expect(parsePendingUncertaintyReview({ ...review, lockReleaseAllowed: true })).toBeNull()
    expect(parsePendingUncertaintyReview({ ...review, approvedBy: 'admin' })).toBeNull()
  })

  it('rejects stale packets bound to another lock', () => {
    const review = validReview()
    const nextLock = { ...lock, occurredAt: '2026-07-18T00:02:00.000Z' }
    expect(reviewMatchesLock(review, nextLock)).toBe(false)
  })

  it('rejects a review whose request snapshot was changed independently of its fingerprint', () => {
    const review = validReview()
    const tampered = {
      ...review,
      request: { ...review.request, endPeriod: '2026-03' },
    }
    expect(parsePendingUncertaintyReview(tampered)).not.toBeNull()
    expect(reviewMatchesLock(tampered, lock)).toBe(false)
  })
})
