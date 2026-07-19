import { describe, expect, test } from 'vitest'
import {
  buildReviewOnlyDecisionBody,
  normalizeReviewOnlyDecisionReason,
  parseCorrelatedReviewDecision,
  parseReviewableRequestList,
} from '@/lib/scrape-uncertainty-review-public'
import type { ScrapeUncertaintyReviewRecord } from '@/lib/scrape-uncertainty-review-server'

const REQUEST_ID = '11111111-1111-4111-8111-111111111111'
const CLIENT_REQUEST_ID = '22222222-2222-4222-8222-222222222222'
const REVIEW_NOW = Date.parse('2026-07-18T00:10:00.000Z')

function publicRecord(
  status: ScrapeUncertaintyReviewRecord['status'] = 'pending_review',
  overrides: Record<string, unknown> = {},
) {
  const decided = status !== 'pending_review'
  return {
    request_id: REQUEST_ID,
    client_request_id: CLIENT_REQUEST_ID,
    status,
    version: decided ? 2 : 1,
    request_payload_hash: 'a'.repeat(64),
    failure_kind: 'monitoring',
    request: { start_period: '2026-01', end_period: '2026-02', force_rescrape: false },
    uncertainty_occurred_at: '2026-07-18T00:00:00.000Z',
    reason: 'The server state is unknown and requires an independent review.',
    requested_at: '2026-07-18T00:01:00.000Z',
    expires_at: '2026-07-18T00:31:00.000Z',
    decided_by: decided ? '33333333-3333-4333-8333-333333333333' : null,
    decided_at: decided ? '2026-07-18T00:05:00.000Z' : null,
    decision_reason: decided ? 'Independent Admin recorded a review-only decision without execution.' : null,
    approval_scope: 'review_only',
    authoritative_record: true,
    approval_granted: status === 'approved',
    execution_enabled: false,
    lock_release_allowed: false,
    automatic_action_taken: false,
    ...overrides,
  }
}

function envelope(record: unknown) {
  return { version: 1, request: record }
}

describe('Phase 3G public review contract', () => {
  test('accepts an exact bounded pending-review list', () => {
    const parsed = parseReviewableRequestList({ version: 1, requests: [publicRecord()] }, 20, REVIEW_NOW)
    expect(parsed.ok).toBe(true)
    if (!parsed.ok) return
    expect(parsed.value.requests).toHaveLength(1)
    expect(parsed.value.requests[0]).toMatchObject({
      request_id: REQUEST_ID,
      status: 'pending_review',
      approval_scope: 'review_only',
      execution_enabled: false,
      lock_release_allowed: false,
      automatic_action_taken: false,
    })
  })

  test('rejects unknown envelope fields, overflow, duplicates, and non-reviewable records', () => {
    expect(parseReviewableRequestList({ version: 1, requests: [], extra: true }, 20, REVIEW_NOW).ok).toBe(false)
    expect(parseReviewableRequestList({ version: 1, requests: [publicRecord(), publicRecord()] }, 20, REVIEW_NOW).ok).toBe(false)
    expect(parseReviewableRequestList({
      version: 1,
      requests: [publicRecord(), publicRecord('pending_review', { request_id: '44444444-4444-4444-8444-444444444444' })],
    }, 1, REVIEW_NOW).ok).toBe(false)
    expect(parseReviewableRequestList({ version: 1, requests: [publicRecord('approved')] }, 20, REVIEW_NOW).ok).toBe(false)
    expect(parseReviewableRequestList({
      version: 1,
      requests: [publicRecord('pending_review', { expires_at: '2026-07-17T23:59:00.000Z' })],
    }, 20, REVIEW_NOW).ok).toBe(false)
    expect(parseReviewableRequestList({
      version: 1,
      requests: [publicRecord('pending_review', { expires_at: '2026-07-18T00:10:00.000Z' })],
    }, 20, REVIEW_NOW).ok).toBe(false)
    expect(parseReviewableRequestList({
      version: 1,
      requests: [publicRecord('pending_review', { expires_at: '2026-07-18T00:09:59.999Z' })],
    }, 20, REVIEW_NOW).ok).toBe(false)
    expect(parseReviewableRequestList({ version: 1, requests: [publicRecord()] }, 20, Number.NaN).ok).toBe(false)
  })

  test('rejects unsafe records before they reach the reviewer UI', () => {
    for (const unsafe of [
      publicRecord('pending_review', { execution_enabled: true }),
      publicRecord('pending_review', { lock_release_allowed: true }),
      publicRecord('pending_review', { automatic_action_taken: true }),
      publicRecord('pending_review', { approval_scope: 'execution' }),
      publicRecord('pending_review', { owner_user_id: '55555555-5555-4555-8555-555555555555' }),
    ]) {
      expect(parseReviewableRequestList({ version: 1, requests: [unsafe] }, 20, REVIEW_NOW).ok).toBe(false)
    }
  })

  test('normalizes and builds only the exact review-only decision body', () => {
    expect(normalizeReviewOnlyDecisionReason('  Independent   review confirms no execution is permitted.  '))
      .toBe('Independent review confirms no execution is permitted.')
    const parsed = buildReviewOnlyDecisionBody(
      'approve',
      1,
      '  Independent   review confirms no execution is permitted.  ',
    )
    expect(parsed).toEqual({
      ok: true,
      value: {
        action: 'approve',
        expected_version: 1,
        reason: 'Independent review confirms no execution is permitted.',
      },
    })
    expect(buildReviewOnlyDecisionBody('revoke', 1, 'A sufficiently long decision reason is supplied.').ok).toBe(false)
    expect(buildReviewOnlyDecisionBody('approve', 0, 'A sufficiently long decision reason is supplied.').ok).toBe(false)
    expect(buildReviewOnlyDecisionBody('approve', 1, 'A control character\ninvalidates this decision reason.').ok).toBe(false)
  })

  test('accepts a strictly correlated approve or reject transition', () => {
    const pendingParsed = parseReviewableRequestList({ version: 1, requests: [publicRecord()] }, 20, REVIEW_NOW)
    expect(pendingParsed.ok).toBe(true)
    if (!pendingParsed.ok) return
    const pending = pendingParsed.value.requests[0]

    for (const action of ['approve', 'reject'] as const) {
      const status = action === 'approve' ? 'approved' : 'rejected'
      const parsed = parseCorrelatedReviewDecision(
        envelope(publicRecord(status)),
        pending,
        action,
        'Independent Admin recorded a review-only decision without execution.',
      )
      expect(parsed.ok).toBe(true)
      if (parsed.ok) {
        expect(parsed.value.status).toBe(status)
        expect(parsed.value.version).toBe(2)
        expect(parsed.value.execution_enabled).toBe(false)
        expect(parsed.value.lock_release_allowed).toBe(false)
      }
    }
  })

  test('rejects stale, mismatched, wrong-action, and unsafe transition responses', () => {
    const pendingParsed = parseReviewableRequestList({ version: 1, requests: [publicRecord()] }, 20, REVIEW_NOW)
    expect(pendingParsed.ok).toBe(true)
    if (!pendingParsed.ok) return
    const pending = pendingParsed.value.requests[0]

    for (const response of [
      publicRecord('approved', { version: 3 }),
      publicRecord('approved', { request_id: '66666666-6666-4666-8666-666666666666' }),
      publicRecord('approved', { request_payload_hash: 'b'.repeat(64) }),
      publicRecord('approved', { request: { ...publicRecord().request, end_period: '2026-03' } }),
      publicRecord('approved', { execution_enabled: true }),
    ]) {
      expect(parseCorrelatedReviewDecision(
        envelope(response),
        pending,
        'approve',
        'Independent Admin recorded a review-only decision without execution.',
      ).ok).toBe(false)
    }
    expect(parseCorrelatedReviewDecision(
      envelope(publicRecord('rejected')),
      pending,
      'approve',
      'Independent Admin recorded a review-only decision without execution.',
    ).ok).toBe(false)
    expect(parseCorrelatedReviewDecision(
      envelope(publicRecord('approved')),
      pending,
      'approve',
      'A different but sufficiently long review-only decision reason.',
    ).ok).toBe(false)
  })
})
