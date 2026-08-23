import { describe, expect, test } from 'vitest'
import {
  buildServerReviewSubmission,
  createServerReviewLocator,
  parsePublicReviewRecord,
  parseReviewResponseEnvelope,
  parseServerReviewLocator,
  serverRecordMatchesLocalReview,
  serverRecordMatchesLocator,
  validateCreateReviewBody,
  validateDecisionBody,
  type ScrapeUncertaintyReviewRecord,
} from '@/lib/scrape-uncertainty-review-server'
import {
  createPendingUncertaintyReview,
  type PersistedUncertaintyLock,
} from '@/lib/scrape-uncertainty-approval'

const LOCK: PersistedUncertaintyLock = {
  version: 1,
  failureKind: 'monitoring',
  request: { startPeriod: '2026-01', endPeriod: '2026-02', forceRescrape: false },
  occurredAt: '2026-07-18T00:00:00.000Z',
}

const LOCAL_REVIEW = createPendingUncertaintyReview({
  lock: LOCK,
  requestId: '11111111-1111-4111-8111-111111111111',
  requestedAt: '2026-07-18T00:01:00.000Z',
  reason: 'Server state is unknown and requires an independent administrative review.',
  serverStateUnverified: true,
  noUnlockOrRetry: true,
})!

function record(status: ScrapeUncertaintyReviewRecord['status'] = 'pending_review') {
  const decided = status !== 'pending_review'
  return {
    request_id: '22222222-2222-4222-8222-222222222222',
    client_request_id: LOCAL_REVIEW.requestId,
    status,
    version: decided ? 2 : 1,
    request_payload_hash: 'a'.repeat(64),
    failure_kind: LOCAL_REVIEW.failureKind,
    request: {
      start_period: LOCAL_REVIEW.request.startPeriod,
      end_period: LOCAL_REVIEW.request.endPeriod,
      force_rescrape: LOCAL_REVIEW.request.forceRescrape,
    },
    uncertainty_occurred_at: LOCAL_REVIEW.uncertaintyOccurredAt,
    reason: LOCAL_REVIEW.reason,
    requested_at: '2026-07-18T00:02:00.000Z',
    expires_at: '2026-07-18T00:32:00.000Z',
    decided_by: decided && status !== 'expired' ? '33333333-3333-4333-8333-333333333333' : null,
    decided_at: decided ? '2026-07-18T00:03:00.000Z' : null,
    decision_reason: decided ? 'Independent review completed without enabling execution.' : null,
    approval_scope: 'review_only',
    authoritative_record: true,
    approval_granted: status === 'approved',
    execution_enabled: false,
    lock_release_allowed: false,
    automatic_action_taken: false,
  }
}

describe('Phase 3F server review contract', () => {
  test('builds an exact allowlisted request without identity, status, hash, or execution fields', () => {
    const submission = buildServerReviewSubmission(LOCAL_REVIEW)
    expect(submission).toEqual({
      client_request_id: LOCAL_REVIEW.requestId,
      failure_kind: 'monitoring',
      request: { start_period: '2026-01', end_period: '2026-02', force_rescrape: false },
      uncertainty_occurred_at: LOCK.occurredAt,
      reason: LOCAL_REVIEW.reason,
      acknowledgements: { server_state_unverified: true, no_unlock_or_retry: true },
    })
    expect(JSON.stringify(submission)).not.toMatch(/owner|actor|approved|execution_enabled|lock_release_allowed|payload_hash/)
  })

  test('rejects owner/status/hash injection and malformed periods', () => {
    const base = buildServerReviewSubmission(LOCAL_REVIEW)
    for (const injected of [
      { ...base, owner_user_id: '44444444-4444-4444-8444-444444444444' },
      { ...base, status: 'approved' },
      { ...base, request_payload_hash: 'a'.repeat(64) },
      { ...base, execution_enabled: true },
      { ...base, request: { ...base.request, start_period: '2026-13' } },
      { ...base, request: { ...base.request, start_period: '2026-03', end_period: '2026-02' } },
    ]) {
      expect(validateCreateReviewBody(injected).ok).toBe(false)
    }
  })

  test('requires complete decision CAS input and rejects unknown fields', () => {
    expect(validateDecisionBody({
      action: 'approve',
      expected_version: 1,
      reason: 'Independent administrator verified the review-only evidence.',
    }).ok).toBe(true)
    expect(validateDecisionBody({ action: 'approve', expected_version: 1, reason: 'too short' }).ok).toBe(false)
    expect(validateDecisionBody({
      action: 'approve',
      expected_version: 1,
      reason: 'Independent administrator verified the review-only evidence.',
      unlock: true,
    }).ok).toBe(false)
  })

  test('accepts all review-only statuses while keeping every execution safety flag false', () => {
    for (const status of ['pending_review', 'approved', 'rejected', 'revoked', 'expired'] as const) {
      const parsed = parseReviewResponseEnvelope({ version: 1, request: record(status) })
      expect(parsed.ok).toBe(true)
      if (parsed.ok) {
        expect(parsed.value.status).toBe(status)
        expect(parsed.value.approval_granted).toBe(status === 'approved')
        expect(parsed.value.execution_enabled).toBe(false)
        expect(parsed.value.lock_release_allowed).toBe(false)
        expect(parsed.value.automatic_action_taken).toBe(false)
      }
    }
  })

  test('fails closed for unknown fields and approved-looking unsafe flags', () => {
    expect(parsePublicReviewRecord({ ...record(), unexpected: true }).ok).toBe(false)
    expect(parsePublicReviewRecord({
      ...record(),
      request: { ...record().request, lock_release_allowed: true },
    }).ok).toBe(false)
    expect(parsePublicReviewRecord({ ...record('approved'), execution_enabled: true }).ok).toBe(false)
    expect(parsePublicReviewRecord({ ...record('approved'), lock_release_allowed: true }).ok).toBe(false)
    expect(parsePublicReviewRecord({ ...record('approved'), automatic_action_taken: true }).ok).toBe(false)
    expect(parsePublicReviewRecord({ ...record(), approval_granted: true }).ok).toBe(false)
    expect(parsePublicReviewRecord({ ...record('approved'), decision_reason: 'short decision text' }).ok).toBe(false)
    expect(parsePublicReviewRecord({ ...record('approved'), decided_by: null }).ok).toBe(false)
    expect(parsePublicReviewRecord({ ...record('approved'), decided_at: null }).ok).toBe(false)
    expect(parsePublicReviewRecord({ ...record('approved'), decision_reason: null }).ok).toBe(false)
    expect(parsePublicReviewRecord({ ...record('expired'), decided_at: null }).ok).toBe(false)
    expect(parsePublicReviewRecord({ ...record('expired'), decision_reason: null }).ok).toBe(false)
  })

  test('binds the server record and strict locator to the Phase 3E local review', () => {
    const parsed = parsePublicReviewRecord(record())
    expect(parsed.ok).toBe(true)
    if (!parsed.ok) return
    expect(serverRecordMatchesLocalReview(parsed.value, LOCAL_REVIEW)).toBe(true)
    const locator = createServerReviewLocator(parsed.value)
    expect(parseServerReviewLocator(locator)).toEqual(locator)
    expect(serverRecordMatchesLocator(parsed.value, locator)).toBe(true)
    expect(parseServerReviewLocator({ ...locator, requestPayloadHash: 'bad' })).toBeNull()
    expect(serverRecordMatchesLocalReview({
      ...parsed.value,
      request: { ...parsed.value.request, end_period: '2026-03' },
    }, LOCAL_REVIEW)).toBe(false)
  })
})
