import { NextRequest } from 'next/server'
import { beforeEach, describe, expect, test, vi } from 'vitest'

const verifyRequestAuthMock = vi.fn()
const createSupabaseServiceClientMock = vi.fn()
const rpcMock = vi.fn()

vi.mock('@/lib/server-auth', () => ({
  verifyRequestAuth: (...args: unknown[]) => verifyRequestAuthMock(...args),
  createSupabaseServiceClient: () => createSupabaseServiceClientMock(),
}))

const ACTOR = 'aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa'
const REQUEST_ID = '22222222-2222-4222-8222-222222222222'
const CLIENT_ID = '11111111-1111-4111-8111-111111111111'

function dbRecord(status = 'pending_review', overrides: Record<string, unknown> = {}) {
  const decided = status !== 'pending_review'
  return {
    review_id: REQUEST_ID,
    client_request_id: CLIENT_ID,
    status,
    version: decided ? 2 : 1,
    request_payload_hash: 'a'.repeat(64),
    failure_kind: 'monitoring',
    start_period: '2026-01',
    end_period: '2026-02',
    force_rescrape: false,
    uncertainty_occurred_at: '2026-07-18T00:00:00.000Z',
    reason: 'Server state is unknown and requires an independent administrative review.',
    requested_at: '2026-07-18T00:02:00.000Z',
    expires_at: '2026-07-18T00:32:00.000Z',
    decided_by: decided && status !== 'expired' ? ACTOR : null,
    decided_at: decided ? '2026-07-18T00:03:00.000Z' : null,
    decision_reason: decided ? 'Independent administrator verified the review-only evidence.' : null,
    approval_scope: 'review_only',
    authoritative: true,
    execution_enabled: false,
    lock_release_allowed: false,
    ...overrides,
  }
}

function createBody(extra: Record<string, unknown> = {}) {
  return {
    client_request_id: CLIENT_ID,
    failure_kind: 'monitoring',
    request: { start_period: '2026-01', end_period: '2026-02', force_rescrape: false },
    uncertainty_occurred_at: '2026-07-18T00:00:00.000Z',
    reason: 'Server state is unknown and requires an independent administrative review.',
    acknowledgements: { server_state_unverified: true, no_unlock_or_retry: true },
    ...extra,
  }
}

function postRequest(path: string, body: unknown) {
  return new NextRequest(`http://localhost${path}`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json', Authorization: 'Bearer browser-token' },
    body: JSON.stringify(body),
  })
}

function getRequest(path: string) {
  return new NextRequest(`http://localhost${path}`, {
    headers: { Authorization: 'Bearer browser-token' },
  })
}

describe('Phase 3F uncertainty review routes', () => {
  beforeEach(() => {
    vi.resetModules()
    vi.clearAllMocks()
    verifyRequestAuthMock.mockResolvedValue({ ok: true, context: { user: { id: ACTOR } } })
    createSupabaseServiceClientMock.mockReturnValue({ rpc: rpcMock })
  })

  test('requires verified Admin and never reaches the ledger on auth failure', async () => {
    verifyRequestAuthMock.mockResolvedValueOnce({ ok: false, status: 403, detail: 'Admin role required' })
    const { POST } = await import('@/app/api/scrape/uncertainty-review-requests/route')
    const response = await POST(postRequest('/api/scrape/uncertainty-review-requests', createBody()))
    expect(response.status).toBe(403)
    expect(response.headers.get('Cache-Control')).toBe('no-store')
    expect(createSupabaseServiceClientMock).not.toHaveBeenCalled()
    expect(rpcMock).not.toHaveBeenCalled()
    expect(verifyRequestAuthMock).toHaveBeenCalledWith(expect.anything(), { requireAdmin: true })
  })

  test('derives owner from verified identity and calls create RPC with an exact allowlist', async () => {
    rpcMock.mockResolvedValueOnce({ data: [dbRecord()], error: null })
    const { POST } = await import('@/app/api/scrape/uncertainty-review-requests/route')
    const response = await POST(postRequest('/api/scrape/uncertainty-review-requests', createBody()))
    expect(response.status).toBe(200)
    expect(response.headers.get('Cache-Control')).toBe('no-store')
    expect(rpcMock).toHaveBeenCalledWith('create_scrape_uncertainty_review', {
      p_actor_user_id: ACTOR,
      p_client_request_id: CLIENT_ID,
      p_failure_kind: 'monitoring',
      p_start_period: '2026-01',
      p_end_period: '2026-02',
      p_force_rescrape: false,
      p_uncertainty_occurred_at: '2026-07-18T00:00:00.000Z',
      p_reason: createBody().reason,
      p_server_state_unverified: true,
      p_no_unlock_or_retry: true,
    })
    const body = await response.json()
    expect(body.request.request_id).toBe(REQUEST_ID)
    expect(body.request).not.toHaveProperty('owner_user_id')
    expect(body.request.execution_enabled).toBe(false)
  })

  test('rejects spoofed fields, oversized bodies, and unavailable service configuration', async () => {
    const { POST } = await import('@/app/api/scrape/uncertainty-review-requests/route')
    let response = await POST(postRequest('/api/scrape/uncertainty-review-requests', createBody({ owner_user_id: ACTOR })))
    expect(response.status).toBe(400)
    expect(rpcMock).not.toHaveBeenCalled()

    response = await POST(new NextRequest('http://localhost/api/scrape/uncertainty-review-requests', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json', 'Content-Length': '9000', Authorization: 'Bearer browser-token' },
      body: '{}',
    }))
    expect(response.status).toBe(413)

    createSupabaseServiceClientMock.mockReturnValueOnce(null)
    response = await POST(postRequest('/api/scrape/uncertainty-review-requests', createBody()))
    expect(response.status).toBe(503)
  })

  test('maps idempotency/version conflicts and malformed backend records safely', async () => {
    const { POST } = await import('@/app/api/scrape/uncertainty-review-requests/route')
    rpcMock.mockResolvedValueOnce({ data: null, error: { code: '23505', message: 'client_request_id payload conflict' } })
    let response = await POST(postRequest('/api/scrape/uncertainty-review-requests', createBody()))
    expect(response.status).toBe(409)

    rpcMock.mockResolvedValueOnce({ data: [{ ...dbRecord(), execution_enabled: true }], error: null })
    response = await POST(postRequest('/api/scrape/uncertainty-review-requests', createBody()))
    expect(response.status).toBe(502)
  })

  test('gets an owner-visible record and hides missing/cross-owner records as 404', async () => {
    const { GET } = await import('@/app/api/scrape/uncertainty-review-requests/[requestId]/route')
    rpcMock.mockResolvedValueOnce({ data: [dbRecord()], error: null })
    let response = await GET(getRequest(`/api/scrape/uncertainty-review-requests/${REQUEST_ID}`), {
      params: Promise.resolve({ requestId: REQUEST_ID }),
    })
    expect(response.status).toBe(200)
    expect(rpcMock).toHaveBeenCalledWith('get_scrape_uncertainty_review', {
      p_actor_user_id: ACTOR,
      p_review_id: REQUEST_ID,
    })

    rpcMock.mockResolvedValueOnce({ data: [], error: null })
    response = await GET(getRequest(`/api/scrape/uncertainty-review-requests/${REQUEST_ID}`), {
      params: Promise.resolve({ requestId: REQUEST_ID }),
    })
    expect(response.status).toBe(404)
  })

  test('lists mine/reviewable with bounded strict query input', async () => {
    const { GET } = await import('@/app/api/scrape/uncertainty-review-requests/route')
    rpcMock.mockResolvedValueOnce({ data: [dbRecord()], error: null })
    let response = await GET(getRequest('/api/scrape/uncertainty-review-requests?scope=reviewable&limit=10'))
    expect(response.status).toBe(200)
    expect(rpcMock).toHaveBeenCalledWith('list_scrape_uncertainty_reviews', {
      p_actor_user_id: ACTOR,
      p_scope: 'reviewable',
      p_limit: 10,
    })

    response = await GET(getRequest('/api/scrape/uncertainty-review-requests?scope=all'))
    expect(response.status).toBe(400)
  })

  test('submits only CAS decision fields; self-review and stale version fail as conflicts', async () => {
    const { POST } = await import('@/app/api/scrape/uncertainty-review-requests/[requestId]/decision/route')
    rpcMock.mockResolvedValueOnce({ data: [dbRecord('approved')], error: null })
    let response = await POST(postRequest(`/api/scrape/uncertainty-review-requests/${REQUEST_ID}/decision`, {
      action: 'approve',
      expected_version: 1,
      reason: 'Independent administrator verified the review-only evidence.',
    }), { params: Promise.resolve({ requestId: REQUEST_ID }) })
    expect(response.status).toBe(200)
    expect((await response.json()).request.execution_enabled).toBe(false)
    expect(rpcMock).toHaveBeenCalledWith('transition_scrape_uncertainty_review', {
      p_actor_user_id: ACTOR,
      p_review_id: REQUEST_ID,
      p_expected_version: 1,
      p_action: 'approve',
      p_reason: 'Independent administrator verified the review-only evidence.',
    })

    rpcMock.mockResolvedValueOnce({ data: null, error: { code: '42501', message: 'requester cannot approve own review' } })
    response = await POST(postRequest(`/api/scrape/uncertainty-review-requests/${REQUEST_ID}/decision`, {
      action: 'approve',
      expected_version: 1,
      reason: 'Independent administrator verified the review-only evidence.',
    }), { params: Promise.resolve({ requestId: REQUEST_ID }) })
    expect(response.status).toBe(409)
  })

  test.each([
    ['review_id', '33333333-3333-4333-8333-333333333333'],
    ['version', 3],
    ['status', 'rejected'],
    ['decided_by', 'bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb'],
    ['decision_reason', 'A different reviewer reason must never be accepted by the route.'],
  ])('rejects an uncorrelated transition field from the RPC: %s', async (field, value) => {
    const { POST } = await import('@/app/api/scrape/uncertainty-review-requests/[requestId]/decision/route')
    rpcMock.mockResolvedValueOnce({ data: [dbRecord('approved', { [field]: value })], error: null })

    const response = await POST(postRequest(`/api/scrape/uncertainty-review-requests/${REQUEST_ID}/decision`, {
      action: 'approve',
      expected_version: 1,
      reason: 'Independent administrator verified the review-only evidence.',
    }), { params: Promise.resolve({ requestId: REQUEST_ID }) })

    expect(response.status).toBe(502)
  })
})
