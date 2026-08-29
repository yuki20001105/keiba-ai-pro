import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

const getSessionMock = vi.hoisted(() => vi.fn())

vi.mock('@/lib/supabase', () => ({
  supabase: { auth: { getSession: getSessionMock } },
}))

import { authFetch } from '@/lib/auth-fetch'

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

  it('does not leave the request pending when Supabase session lookup stalls', async () => {
    getSessionMock.mockReturnValue(new Promise(() => {}))

    const request = authFetch('/api/health')
    await vi.advanceTimersByTimeAsync(2_000)

    await expect(request).resolves.toBeInstanceOf(Response)
    expect(fetch).toHaveBeenCalledOnce()
  })

  it('adds the bearer token when the session is available', async () => {
    getSessionMock.mockResolvedValue({ data: { session: { access_token: 'test-token' } } })

    await authFetch('/api/health')

    const init = vi.mocked(fetch).mock.calls[0][1]
    expect(new Headers(init?.headers).get('Authorization')).toBe('Bearer test-token')
  })
})
