import { NextRequest } from 'next/server'
import { afterEach, beforeEach, describe, expect, test, vi } from 'vitest'

const verifyRequestAuthMock = vi.hoisted(() => vi.fn())

vi.mock('@/lib/server-auth', () => ({
  verifyRequestAuth: verifyRequestAuthMock,
}))

const NOW = new Date('2026-09-12T03:00:00.000Z')
const USER_ID = 'aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa'
const SESSION_ID = 'bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb'

function token(payload: Record<string, unknown>): string {
  const encode = (value: unknown) => Buffer.from(JSON.stringify(value), 'utf8').toString('base64url')
  return `${encode({ alg: 'HS256', typ: 'JWT' })}.${encode(payload)}.verified-signature`
}

function request(cookie?: string): NextRequest {
  return new NextRequest('http://localhost/api/admin/unlock', {
    method: 'POST',
    headers: {
      Authorization: 'Bearer verified-token',
      ...(cookie ? { cookie } : {}),
    },
  })
}

function allowWith(payload: Record<string, unknown>) {
  verifyRequestAuthMock.mockResolvedValue({
    ok: true,
    context: {
      user: { id: USER_ID },
      token: token({ sub: USER_ID, session_id: SESSION_ID, ...payload }),
      profile: { role: 'admin', subscription_tier: 'premium' },
    },
  })
}

describe('POST /api/admin/unlock', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    vi.useFakeTimers()
    vi.setSystemTime(NOW)
    process.env.ADMIN_MODE_SIGNING_SECRET = 'test-admin-mode-secret-that-is-at-least-32-characters'
  })

  afterEach(() => {
    vi.useRealTimers()
    delete process.env.ADMIN_MODE_SIGNING_SECRET
  })

  test('requires a currently authorized Admin', async () => {
    verifyRequestAuthMock.mockResolvedValue({ ok: false, status: 403, detail: 'Admin role required' })
    const { POST } = await import('@/app/api/admin/unlock/route')

    const response = await POST(request())

    expect(response.status).toBe(403)
    expect(await response.json()).toEqual({ detail: 'Admin role required' })
    expect(response.headers.get('Cache-Control')).toBe('no-store')
    expect(verifyRequestAuthMock).toHaveBeenCalledWith(expect.any(NextRequest), { requireAdmin: true })
  })

  test('unlocks for fifteen minutes after a fresh password authentication', async () => {
    const passwordTimestamp = Math.floor(NOW.getTime() / 1000) - 30
    allowWith({ amr: [{ method: 'password', timestamp: passwordTimestamp }] })
    const { POST } = await import('@/app/api/admin/unlock/route')

    const response = await POST(request())

    expect(response.status).toBe(200)
    expect(await response.json()).toEqual({
      version: 1,
      unlocked: true,
      expires_at: new Date((passwordTimestamp + 15 * 60) * 1000).toISOString(),
    })
    expect(response.headers.get('Cache-Control')).toBe('no-store')
    const setCookie = response.headers.get('set-cookie') || ''
    expect(setCookie).toContain('keiba_admin_mode=')
    expect(setCookie).toContain('HttpOnly')
    expect(setCookie).toContain('Max-Age=870')
    expect(setCookie).toContain('Priority=high')
    expect(setCookie).toContain('SameSite=strict')
    expect(setCookie).toContain('Path=/')
  })

  test('caps a clock-skewed password timestamp at fifteen minutes from the server clock', async () => {
    const nowSeconds = Math.floor(NOW.getTime() / 1000)
    allowWith({ amr: [{ method: 'password', timestamp: nowSeconds + 30 }] })
    const { POST } = await import('@/app/api/admin/unlock/route')

    const response = await POST(request())

    expect(response.status).toBe(200)
    expect(await response.json()).toMatchObject({
      expires_at: new Date((nowSeconds + 15 * 60) * 1000).toISOString(),
    })
  })

  test.each([
    ['missing AMR', {}],
    ['string-only AMR', { amr: ['password'] }],
    ['wrong method', { amr: [{ method: 'oauth', timestamp: Math.floor(NOW.getTime() / 1000) }] }],
    ['stale password', { amr: [{ method: 'password', timestamp: Math.floor(NOW.getTime() / 1000) - 900 }] }],
    ['future password', { amr: [{ method: 'password', timestamp: Math.floor(NOW.getTime() / 1000) + 31 }] }],
    ['different subject', { sub: 'different-user', amr: [{ method: 'password', timestamp: Math.floor(NOW.getTime() / 1000) }] }],
  ])('rejects %s claims', async (_label, payload) => {
    allowWith(payload)
    const { POST } = await import('@/app/api/admin/unlock/route')

    const response = await POST(request())

    expect(response.status).toBe(403)
    expect(await response.json()).toEqual({ detail: 'Recent password verification required' })
    expect(response.headers.get('Cache-Control')).toBe('no-store')
  })

  test('rejects a token without a session binding', async () => {
    const passwordTimestamp = Math.floor(NOW.getTime() / 1000) - 30
    allowWith({ session_id: undefined, amr: [{ method: 'password', timestamp: passwordTimestamp }] })
    const { POST } = await import('@/app/api/admin/unlock/route')

    const response = await POST(request())

    expect(response.status).toBe(503)
    expect(await response.json()).toEqual({ detail: 'Admin mode session could not be created' })
  })

  test('GET restores a valid cookie and DELETE clears it', async () => {
    const passwordTimestamp = Math.floor(NOW.getTime() / 1000) - 30
    allowWith({ amr: [{ method: 'password', timestamp: passwordTimestamp }] })
    const route = await import('@/app/api/admin/unlock/route')
    const postResponse = await route.POST(request())
    const setCookie = postResponse.headers.get('set-cookie') || ''
    const cookie = setCookie.split(';', 1)[0]

    const getResponse = await route.GET(request(cookie))
    expect(getResponse.status).toBe(200)
    expect(await getResponse.json()).toMatchObject({ version: 1, unlocked: true })

    const deleteResponse = await route.DELETE()
    expect(deleteResponse.status).toBe(200)
    expect(await deleteResponse.json()).toEqual({ version: 1, unlocked: false })
    expect(deleteResponse.headers.get('set-cookie')).toContain('keiba_admin_mode=;')
    expect(deleteResponse.headers.get('set-cookie')).toContain('Max-Age=0')
  })
})
