import { afterEach, beforeEach, describe, expect, test, vi } from 'vitest'
import { usesLocalAdminSession } from '@/lib/local-admin-session-policy'

describe('local admin session policy', () => {
  beforeEach(() => {
    vi.stubEnv('LOCAL_ADMIN_SESSION_ENABLED', 'true')
    vi.stubEnv('APP_ENV', 'development')
    vi.stubEnv('ML_API_URL', 'http://127.0.0.1:8000')
    vi.stubEnv('SCRAPE_API_URL', 'http://127.0.0.1:8000')
    for (const name of ['VERCEL', 'VERCEL_ENV', 'NETLIFY', 'CONTEXT', 'RENDER', 'RAILWAY_ENVIRONMENT', 'FLY_APP_NAME']) vi.stubEnv(name, '')
  })
  afterEach(() => vi.unstubAllEnvs())

  test.each(['local', 'development', 'test'])('allows explicitly configured %s on loopback', environment => {
    vi.stubEnv('APP_ENV', environment)
    expect(usesLocalAdminSession(new Request('http://localhost:3000/api/admin/unlock', {
      headers: { host: 'localhost:3000', 'x-forwarded-host': 'localhost:3000' },
    }))).toBe(true)
  })

  test.each(['production', 'staging', '', 'unknown'])('rejects %s even with a localhost host', environment => {
    vi.stubEnv('APP_ENV', environment)
    expect(usesLocalAdminSession(new Request('http://localhost/api/admin/unlock'))).toBe(false)
  })

  test.each(['127.0.0.1:3000', '[::1]:3000'])('accepts Next localhost normalization with the real loopback Host %s', host => {
    expect(usesLocalAdminSession(new Request('http://localhost:3000/api/admin/unlock', {
      headers: { host, 'x-forwarded-host': host },
    }))).toBe(true)
  })

  test('uses the request URL when no Host header is available', () => {
    expect(usesLocalAdminSession(new Request('http://localhost:3000/api/admin/unlock', {
      headers: { 'x-forwarded-host': 'localhost:3000' },
    }))).toBe(true)
  })

  test.each([
    { host: '127.0.0.1:3001', 'x-forwarded-host': '127.0.0.1:3001' },
    { host: '127.0.0.1:3000', 'x-forwarded-host': 'localhost:3000' },
    { host: '127.0.0.1:not-a-port', 'x-forwarded-host': '127.0.0.1:not-a-port' },
  ])('rejects port changes and forwarded Host mismatches: %s', headers => {
    expect(usesLocalAdminSession(new Request('http://localhost:3000/api/admin/unlock', { headers }))).toBe(false)
  })

  test.each([
    ['LOCAL_ADMIN_SESSION_ENABLED', ''], ['ML_API_URL', ''], ['SCRAPE_API_URL', ''],
    ['ML_API_URL', 'https://api.example.com'], ['SCRAPE_API_URL', 'https://api.example.com'],
    ['VERCEL', '1'], ['VERCEL_ENV', 'preview'], ['RENDER', 'true'],
  ])('requires safe explicit server config %s=%s', (name, value) => {
    vi.stubEnv(name, value)
    expect(usesLocalAdminSession(new Request('http://localhost/api/admin/unlock'))).toBe(false)
  })

  test.each([
    new Request('https://example.com/api/admin/unlock'),
    new Request('http://localhost/api/admin/unlock', { headers: { host: 'example.com' } }),
    new Request('http://localhost/api/admin/unlock', { headers: { 'x-forwarded-host': 'example.com' } }),
    new Request('http://localhost/api/admin/unlock', { headers: { 'x-forwarded-host': 'localhost,example.com' } }),
    new Request('http://localhost/api/admin/unlock', { headers: { forwarded: 'host=example.com' } }),
  ])('does not trust remote or proxy request %s', request => {
    expect(usesLocalAdminSession(request)).toBe(false)
  })
})
