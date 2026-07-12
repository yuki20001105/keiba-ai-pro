import { beforeEach, describe, expect, test, vi } from 'vitest'

const mockCreateClient = vi.fn()

vi.mock('@supabase/supabase-js', () => ({
  createClient: (...args: unknown[]) => mockCreateClient(...args),
}))

function makeAuthRequest(token?: string): Request {
  return new Request('http://localhost/api/test', {
    method: 'GET',
    headers: token ? { Authorization: `Bearer ${token}` } : {},
  })
}

describe('server-auth fail-closed contract', () => {
  beforeEach(() => {
    vi.resetModules()
    vi.clearAllMocks()
    process.env.NEXT_PUBLIC_SUPABASE_URL = 'http://127.0.0.1:54321'
    process.env.NEXT_PUBLIC_SUPABASE_ANON_KEY = 'anon-key'
    delete process.env.SUPABASE_SERVICE_KEY
    process.env.SUPABASE_SERVICE_ROLE_KEY = 'service-role-key'
  })

  test('tokenなしは401', async () => {
    const { verifyRequestAuth } = await import('@/lib/server-auth')
    const res = await verifyRequestAuth(makeAuthRequest())
    expect(res.ok).toBe(false)
    if (!res.ok) {
      expect(res.status).toBe(401)
      expect(res.detail).toBe('Authentication required')
    }
  })

  test('getUser失敗は401', async () => {
    mockCreateClient.mockImplementation((_url: string, key: string) => {
      if (key === 'anon-key') {
        return {
          auth: {
            getUser: vi.fn().mockResolvedValue({ data: { user: null }, error: { message: 'expired' } }),
          },
        }
      }
      return {
        from: vi.fn(),
      }
    })

    const { verifyRequestAuth } = await import('@/lib/server-auth')
    const res = await verifyRequestAuth(makeAuthRequest('tok'))
    expect(res.ok).toBe(false)
    if (!res.ok) {
      expect(res.status).toBe(401)
      expect(res.detail).toBe('Authentication required')
    }
  })

  test('service role未設定は503', async () => {
    delete process.env.SUPABASE_SERVICE_ROLE_KEY
    delete process.env.SUPABASE_SERVICE_KEY

    mockCreateClient.mockImplementation((_url: string, key: string) => {
      if (key === 'anon-key') {
        return {
          auth: {
            getUser: vi.fn().mockResolvedValue({ data: { user: { id: 'u1' } }, error: null }),
          },
        }
      }
      return { from: vi.fn() }
    })

    const { verifyRequestAuth } = await import('@/lib/server-auth')
    const res = await verifyRequestAuth(makeAuthRequest('tok'))
    expect(res.ok).toBe(false)
    if (!res.ok) {
      expect(res.status).toBe(503)
      expect(res.detail).toBe('Supabase service role configuration missing')
    }
  })

  test('profileなしは403', async () => {
    const maybeSingle = vi.fn().mockResolvedValue({ data: null, error: null })
    mockCreateClient.mockImplementation((_url: string, key: string) => {
      if (key === 'anon-key') {
        return {
          auth: {
            getUser: vi.fn().mockResolvedValue({ data: { user: { id: 'u1' } }, error: null }),
          },
        }
      }
      return {
        from: () => ({
          select: () => ({
            eq: () => ({ maybeSingle }),
          }),
        }),
      }
    })

    const { verifyRequestAuth } = await import('@/lib/server-auth')
    const res = await verifyRequestAuth(makeAuthRequest('tok'))
    expect(res.ok).toBe(false)
    if (!res.ok) {
      expect(res.status).toBe(403)
      expect(res.detail).toBe('Access denied')
    }
  })

  test('profile問い合わせ基盤エラーは503', async () => {
    const maybeSingle = vi.fn().mockResolvedValue({ data: null, error: { message: 'db down' } })
    mockCreateClient.mockImplementation((_url: string, key: string) => {
      if (key === 'anon-key') {
        return {
          auth: {
            getUser: vi.fn().mockResolvedValue({ data: { user: { id: 'u1' } }, error: null }),
          },
        }
      }
      return {
        from: () => ({
          select: () => ({
            eq: () => ({ maybeSingle }),
          }),
        }),
      }
    })

    const { verifyRequestAuth } = await import('@/lib/server-auth')
    const res = await verifyRequestAuth(makeAuthRequest('tok'))
    expect(res.ok).toBe(false)
    if (!res.ok) {
      expect(res.status).toBe(503)
      expect(res.detail).toBe('Authorization backend unavailable')
    }
  })

  test('一般ユーザーでrequireAdminは403', async () => {
    const maybeSingle = vi.fn().mockResolvedValue({
      data: { role: 'user', subscription_tier: 'premium' },
      error: null,
    })
    mockCreateClient.mockImplementation((_url: string, key: string) => {
      if (key === 'anon-key') {
        return {
          auth: {
            getUser: vi.fn().mockResolvedValue({ data: { user: { id: 'u1' } }, error: null }),
          },
        }
      }
      return {
        from: () => ({
          select: () => ({
            eq: () => ({ maybeSingle }),
          }),
        }),
      }
    })

    const { verifyRequestAuth } = await import('@/lib/server-auth')
    const res = await verifyRequestAuth(makeAuthRequest('tok'), { requireAdmin: true })
    expect(res.ok).toBe(false)
    if (!res.ok) {
      expect(res.status).toBe(403)
      expect(res.detail).toBe('Admin role required')
    }
  })

  test('FreeユーザーでrequirePremiumOrAdminは403', async () => {
    const maybeSingle = vi.fn().mockResolvedValue({
      data: { role: 'user', subscription_tier: 'free' },
      error: null,
    })
    mockCreateClient.mockImplementation((_url: string, key: string) => {
      if (key === 'anon-key') {
        return {
          auth: {
            getUser: vi.fn().mockResolvedValue({ data: { user: { id: 'u1' } }, error: null }),
          },
        }
      }
      return {
        from: () => ({
          select: () => ({
            eq: () => ({ maybeSingle }),
          }),
        }),
      }
    })

    const { verifyRequestAuth } = await import('@/lib/server-auth')
    const res = await verifyRequestAuth(makeAuthRequest('tok'), { requirePremiumOrAdmin: true })
    expect(res.ok).toBe(false)
    if (!res.ok) {
      expect(res.status).toBe(403)
      expect(res.detail).toBe('Premium or admin role required')
    }
  })

  test('未知role/tierは安全側へ正規化される', async () => {
    const maybeSingle = vi.fn().mockResolvedValue({
      data: { role: 'superuser', subscription_tier: 'vip' },
      error: null,
    })
    mockCreateClient.mockImplementation((_url: string, key: string) => {
      if (key === 'anon-key') {
        return {
          auth: {
            getUser: vi.fn().mockResolvedValue({
              data: {
                user: {
                  id: 'u1',
                  user_metadata: { role: 'admin', subscription_tier: 'premium' },
                },
              },
              error: null,
            }),
          },
        }
      }
      return {
        from: () => ({
          select: () => ({
            eq: () => ({ maybeSingle }),
          }),
        }),
      }
    })

    const { verifyRequestAuth } = await import('@/lib/server-auth')
    const res = await verifyRequestAuth(makeAuthRequest('tok'))
    expect(res.ok).toBe(true)
    if (res.ok) {
      expect(res.context.profile.role).toBe('user')
      expect(res.context.profile.subscription_tier).toBe('free')
    }
  })
})
