import { describe, expect, test } from 'vitest'
import {
  getAppOrigin,
  buildSupabaseSessionCookie,
  buildTestSession,
  encodeSupabaseSsrCookieValue,
  getSupabaseStorageKey,
} from '../../e2e/helpers/supabase-session'

describe('Supabase SSR cookie contract', () => {
  test('storage key uses project ref host prefix', () => {
    const key = getSupabaseStorageKey('https://grfwkutcsavqicaimssn.supabase.co')
    expect(key).toBe('sb-grfwkutcsavqicaimssn-auth-token')
  })

  test('cookie value uses base64- + base64url(session-json)', () => {
    const session = buildTestSession({ role: 'admin', tier: 'premium', expiresInSeconds: 3600 })
    const encoded = encodeSupabaseSsrCookieValue(session)
    expect(encoded.startsWith('base64-')).toBe(true)

    const decodedJson = Buffer.from(encoded.slice('base64-'.length), 'base64url').toString('utf8')
    const decoded = JSON.parse(decodedJson)
    expect(decoded.user.id).toBe('e2e-user-id')
    expect(decoded.access_token).toContain('.')
    expect(decoded.expires_at).toBeGreaterThan(Math.floor(Date.now() / 1000))
  })

  test('helper builds cookie with computed storage key', () => {
    const cookie = buildSupabaseSessionCookie({ supabaseUrl: 'https://grfwkutcsavqicaimssn.supabase.co' })
    expect(cookie.name).toBe('sb-grfwkutcsavqicaimssn-auth-token')
    expect(cookie.value.startsWith('base64-')).toBe(true)
  })

  test('cookie origin matches appBaseUrl origin', () => {
    expect(getAppOrigin('http://127.0.0.1:3101')).toBe('http://127.0.0.1:3101')
  })

  test('cookie origin is not hardcoded to localhost:3000', () => {
    const origin = getAppOrigin('http://127.0.0.1:3101')
    expect(origin).not.toBe('http://localhost:3000')
  })

  test('falls back to local Supabase URL when env and explicit URL are absent', () => {
    const prevPublic = process.env.NEXT_PUBLIC_SUPABASE_URL
    const prevServer = process.env.SUPABASE_URL
    delete process.env.NEXT_PUBLIC_SUPABASE_URL
    delete process.env.SUPABASE_URL
    try {
      expect(getSupabaseStorageKey()).toBe('sb-127-auth-token')
    } finally {
      if (prevPublic !== undefined) process.env.NEXT_PUBLIC_SUPABASE_URL = prevPublic
      if (prevServer !== undefined) process.env.SUPABASE_URL = prevServer
    }
  })

  test('throws when appBaseUrl is missing', () => {
    expect(() => getAppOrigin(undefined)).toThrow('Playwright baseURL is required for E2E Supabase session setup')
  })
})