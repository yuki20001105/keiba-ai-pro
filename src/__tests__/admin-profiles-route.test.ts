import { NextRequest } from 'next/server'
import { beforeEach, describe, expect, test, vi } from 'vitest'

const verifyRequestAuthMock = vi.fn()
const createSupabaseServiceClientMock = vi.fn()

vi.mock('@/lib/server-auth', () => ({
  verifyRequestAuth: (...args: unknown[]) => verifyRequestAuthMock(...args),
  createSupabaseServiceClient: () => createSupabaseServiceClientMock(),
}))

const ADMIN_ID = 'aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa'
const TARGET_ID = 'bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb'

const validProfile = {
  id: TARGET_ID,
  email: 'user@example.com',
  role: 'user',
  full_name: 'Test User',
  subscription_tier: 'free',
  created_at: '2026-07-20T00:00:00.000Z',
}

function getRequest() {
  return new NextRequest('http://localhost/api/admin/profiles', {
    headers: { Authorization: 'Bearer browser-token' },
  })
}

function patchRequest(body: unknown) {
  return new NextRequest(`http://localhost/api/admin/profiles/${TARGET_ID}/role`, {
    method: 'PATCH',
    headers: { Authorization: 'Bearer browser-token', 'Content-Type': 'application/json' },
    body: JSON.stringify(body),
  })
}

describe('Admin profile server routes', () => {
  beforeEach(() => {
    vi.resetModules()
    vi.clearAllMocks()
    verifyRequestAuthMock.mockResolvedValue({
      ok: true,
      context: {
        user: { id: ADMIN_ID },
        token: 'browser-token',
        profile: { role: 'admin', subscription_tier: 'premium' },
      },
    })
  })

  test('requires verified Admin before listing profiles or creating a service client', async () => {
    verifyRequestAuthMock.mockResolvedValueOnce({ ok: false, status: 403, detail: 'Admin role required' })
    const { GET } = await import('@/app/api/admin/profiles/route')

    const response = await GET(getRequest())

    expect(response.status).toBe(403)
    expect(response.headers.get('Cache-Control')).toBe('no-store')
    expect(await response.json()).toEqual({ detail: 'Admin role required' })
    expect(verifyRequestAuthMock).toHaveBeenCalledWith(expect.anything(), { requireAdmin: true })
    expect(createSupabaseServiceClientMock).not.toHaveBeenCalled()
  })

  test('lists only the explicit safe profile projection and strips unselected fields', async () => {
    const limit = vi.fn().mockResolvedValue({
      data: [{ ...validProfile, stripe_customer_id: 'must-not-leak', internal_note: 'must-not-leak' }],
      error: null,
    })
    const order = vi.fn(() => ({ limit }))
    const select = vi.fn(() => ({ order }))
    const from = vi.fn(() => ({ select }))
    createSupabaseServiceClientMock.mockReturnValue({ from })
    const { GET } = await import('@/app/api/admin/profiles/route')

    const response = await GET(getRequest())
    const body = await response.json()

    expect(response.status).toBe(200)
    expect(from).toHaveBeenCalledWith('profiles')
    expect(select).toHaveBeenCalledWith('id, email, role, full_name, subscription_tier, created_at')
    expect(order).toHaveBeenCalledWith('created_at', { ascending: false })
    expect(limit).toHaveBeenCalledWith(500)
    expect(body).toEqual({ version: 1, profiles: [validProfile] })
    expect(JSON.stringify(body)).not.toContain('stripe_customer_id')
    expect(JSON.stringify(body)).not.toContain('internal_note')
  })

  test('fails closed on missing service configuration, backend errors, and malformed rows', async () => {
    const { GET } = await import('@/app/api/admin/profiles/route')

    createSupabaseServiceClientMock.mockReturnValueOnce(null)
    let response = await GET(getRequest())
    expect(response.status).toBe(503)

    createSupabaseServiceClientMock.mockReturnValueOnce({
      from: () => ({
        select: () => ({ order: () => ({ limit: async () => ({ data: null, error: { message: 'db detail' } }) }) }),
      }),
    })
    response = await GET(getRequest())
    expect(response.status).toBe(503)
    expect(await response.json()).toEqual({ detail: 'Admin profile service unavailable' })

    createSupabaseServiceClientMock.mockReturnValueOnce({
      from: () => ({
        select: () => ({ order: () => ({ limit: async () => ({ data: [{ ...validProfile, role: 'owner' }], error: null }) }) }),
      }),
    })
    response = await GET(getRequest())
    expect(response.status).toBe(502)
    expect(await response.json()).toEqual({ detail: 'Invalid profiles response' })
  })

  test('rejects non-admin, invalid target IDs, spoofed fields, and roles outside user/admin before update', async () => {
    const rpc = vi.fn()
    createSupabaseServiceClientMock.mockReturnValue({ rpc })
    const { PATCH } = await import('@/app/api/admin/profiles/[userId]/role/route')

    verifyRequestAuthMock.mockResolvedValueOnce({ ok: false, status: 403, detail: 'Admin role required' })
    let response = await PATCH(patchRequest({ role: 'admin' }), {
      params: Promise.resolve({ userId: TARGET_ID }),
    })
    expect(response.status).toBe(403)
    expect(createSupabaseServiceClientMock).not.toHaveBeenCalled()

    response = await PATCH(patchRequest({ role: 'admin' }), {
      params: Promise.resolve({ userId: '../other-user' }),
    })
    expect(response.status).toBe(400)

    response = await PATCH(patchRequest({ role: 'owner' }), {
      params: Promise.resolve({ userId: TARGET_ID }),
    })
    expect(response.status).toBe(400)

    response = await PATCH(patchRequest({ role: 'admin', user_id: ADMIN_ID }), {
      params: Promise.resolve({ userId: TARGET_ID }),
    })
    expect(response.status).toBe(400)
    expect(rpc).not.toHaveBeenCalled()
  })

  test('fails closed before the RPC when the verified actor identifier is malformed', async () => {
    verifyRequestAuthMock.mockResolvedValueOnce({
      ok: true,
      context: {
        user: { id: 'not-a-uuid' },
        token: 'browser-token',
        profile: { role: 'admin', subscription_tier: 'premium' },
      },
    })
    const { PATCH } = await import('@/app/api/admin/profiles/[userId]/role/route')

    const response = await PATCH(patchRequest({ role: 'admin' }), {
      params: Promise.resolve({ userId: TARGET_ID }),
    })

    expect(response.status).toBe(503)
    expect(await response.json()).toEqual({ detail: 'Authorization backend unavailable' })
    expect(createSupabaseServiceClientMock).not.toHaveBeenCalled()
  })

  test('passes the verified actor to the atomic RPC and returns an exact correlated result', async () => {
    const from = vi.fn()
    const rpc = vi.fn(async (_name: string, args: Record<string, string>) => ({
      data: [{ id: TARGET_ID, role: 'admin', request_id: args.p_request_id }],
      error: null,
    }))
    createSupabaseServiceClientMock.mockReturnValue({ from, rpc })
    const { PATCH } = await import('@/app/api/admin/profiles/[userId]/role/route')

    const response = await PATCH(patchRequest({ role: 'admin' }), {
      params: Promise.resolve({ userId: TARGET_ID }),
    })

    expect(response.status).toBe(200)
    expect(from).not.toHaveBeenCalled()
    expect(rpc).toHaveBeenCalledWith('update_admin_profile_role', {
      p_actor_user_id: ADMIN_ID,
      p_target_user_id: TARGET_ID,
      p_role: 'admin',
      p_request_id: expect.stringMatching(/^[0-9a-f-]{36}$/i),
    })
    expect(await response.json()).toEqual({ version: 1, profile: { id: TARGET_ID, role: 'admin' } })
  })

  test('maps atomic boundary failures without exposing database detail', async () => {
    const rpc = vi.fn()
      .mockResolvedValueOnce({ data: null, error: { code: '42501', message: 'private actor detail' } })
      .mockResolvedValueOnce({ data: null, error: { code: 'P0002', message: 'private target detail' } })
      .mockResolvedValueOnce({ data: null, error: { code: 'P0001', message: 'private invariant detail' } })
      .mockResolvedValueOnce({ data: null, error: { code: 'XX000', message: 'private backend detail' } })
    createSupabaseServiceClientMock.mockReturnValue({ rpc })
    const { PATCH } = await import('@/app/api/admin/profiles/[userId]/role/route')

    let response = await PATCH(patchRequest({ role: 'admin' }), {
      params: Promise.resolve({ userId: TARGET_ID }),
    })
    expect(response.status).toBe(403)
    expect(await response.json()).toEqual({ detail: 'Admin role required' })

    response = await PATCH(patchRequest({ role: 'admin' }), {
      params: Promise.resolve({ userId: TARGET_ID }),
    })
    expect(response.status).toBe(404)
    expect(await response.json()).toEqual({ detail: 'Profile not found' })

    response = await PATCH(patchRequest({ role: 'admin' }), {
      params: Promise.resolve({ userId: TARGET_ID }),
    })
    expect(response.status).toBe(409)
    expect(await response.json()).toEqual({ detail: 'At least one administrator is required' })

    response = await PATCH(patchRequest({ role: 'admin' }), {
      params: Promise.resolve({ userId: TARGET_ID }),
    })
    expect(response.status).toBe(503)
    expect(await response.json()).toEqual({ detail: 'Admin profile service unavailable' })
  })

  test('rejects missing, extra, or uncorrelated RPC response rows', async () => {
    const rpc = vi.fn()
      .mockResolvedValueOnce({ data: [], error: null })
      .mockImplementationOnce(async (_name: string, args: Record<string, string>) => ({
        data: [{ id: ADMIN_ID, role: 'admin', request_id: args.p_request_id }],
        error: null,
      }))
      .mockImplementationOnce(async (_name: string, args: Record<string, string>) => ({
        data: [{ id: TARGET_ID, role: 'admin', request_id: args.p_request_id, email: 'must-not-leak' }],
        error: null,
      }))
    createSupabaseServiceClientMock.mockReturnValue({ rpc })
    const { PATCH } = await import('@/app/api/admin/profiles/[userId]/role/route')

    for (let attempt = 0; attempt < 3; attempt += 1) {
      const response = await PATCH(patchRequest({ role: 'admin' }), {
        params: Promise.resolve({ userId: TARGET_ID }),
      })
      expect(response.status).toBe(502)
      expect(await response.json()).toEqual({ detail: 'Invalid role update response' })
    }
  })
})
