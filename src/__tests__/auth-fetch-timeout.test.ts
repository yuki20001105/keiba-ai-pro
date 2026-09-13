import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

const getSessionMock = vi.hoisted(() => vi.fn())

vi.mock('@/lib/supabase', () => ({
  supabase: { auth: { getSession: getSessionMock } },
}))

import { authFetch, AuthSessionUnavailableError } from '@/lib/auth-fetch'

describe('authFetch', () => {
  beforeEach(() => {
    vi.useFakeTimers()
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue(new Response(null, { status: 200 })))
  })

  afterEach(() => {
    vi.useRealTimers()
    vi.unstubAllGlobals()
    vi.clearAllMocks()
  })

  it('reports a transient error instead of sending an unauthenticated request when session lookup stalls', async () => {
    getSessionMock.mockReturnValue(new Promise(() => {}))

    const request = authFetch('/api/health')
    const assertion = expect(request).rejects.toBeInstanceOf(AuthSessionUnavailableError)
    await vi.advanceTimersByTimeAsync(2_000)

    await assertion
    expect(fetch).not.toHaveBeenCalled()
  })

  it('does not turn a failed token refresh into a tokenless 401', async () => {
    getSessionMock.mockResolvedValue({ data: { session: null }, error: { status: 503 } })
    await expect(authFetch('/api/scrape/status')).rejects.toMatchObject({ code: 'AUTH_SESSION_UNAVAILABLE' })
    expect(fetch).not.toHaveBeenCalled()
  })

  it('a confirmed missing session still sends no credentials', async () => {
    getSessionMock.mockResolvedValue({ data: { session: null }, error: null })
    await authFetch('/api/scrape/status')
    const init = vi.mocked(fetch).mock.calls[0][1]
    expect(new Headers(init?.headers).has('Authorization')).toBe(false)
  })

  it('adds the bearer token when the session is available', async () => {
    getSessionMock.mockResolvedValue({ data: { session: { access_token: 'test-token' } } })

    await authFetch('/api/health')

    const init = vi.mocked(fetch).mock.calls[0][1]
    expect(new Headers(init?.headers).get('Authorization')).toBe('Bearer test-token')
  })
})
