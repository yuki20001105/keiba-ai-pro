import { afterEach, beforeEach, describe, expect, test } from 'vitest'
import {
  ADMIN_MODE_COOKIE_NAME,
  createAdminModeGrant,
  readSessionBinding,
  verifyAdminModeGrant,
  verifyAdminModeRequest,
} from '@/lib/admin-mode'

const NOW = 1_789_180_800
const USER_ID = 'aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa'
const SESSION_ID = 'bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb'
const SECRET = 'test-admin-mode-secret-that-is-at-least-32-characters'
const PREVIOUS_ADMIN_SECRET = process.env.ADMIN_MODE_SIGNING_SECRET
const PREVIOUS_SERVICE_ROLE_KEY = process.env.SUPABASE_SERVICE_ROLE_KEY
const PREVIOUS_SERVICE_KEY = process.env.SUPABASE_SERVICE_KEY

function jwt(payload: Record<string, unknown>): string {
  const encode = (value: unknown) => Buffer.from(JSON.stringify(value), 'utf8').toString('base64url')
  return `${encode({ alg: 'HS256', typ: 'JWT' })}.${encode(payload)}.signature`
}

function grant() {
  return createAdminModeGrant({
    userId: USER_ID,
    sessionId: SESSION_ID,
    issuedAtSeconds: NOW,
    expiresAtSeconds: NOW + 600,
  })
}

describe('admin mode grant', () => {
  beforeEach(() => {
    process.env.ADMIN_MODE_SIGNING_SECRET = SECRET
  })

  afterEach(() => {
    if (PREVIOUS_ADMIN_SECRET === undefined) delete process.env.ADMIN_MODE_SIGNING_SECRET
    else process.env.ADMIN_MODE_SIGNING_SECRET = PREVIOUS_ADMIN_SECRET
    if (PREVIOUS_SERVICE_ROLE_KEY === undefined) delete process.env.SUPABASE_SERVICE_ROLE_KEY
    else process.env.SUPABASE_SERVICE_ROLE_KEY = PREVIOUS_SERVICE_ROLE_KEY
    if (PREVIOUS_SERVICE_KEY === undefined) delete process.env.SUPABASE_SERVICE_KEY
    else process.env.SUPABASE_SERVICE_KEY = PREVIOUS_SERVICE_KEY
  })

  test('accepts an untampered grant only for the bound user and session', () => {
    const value = grant()
    expect(value).not.toBeNull()
    expect(verifyAdminModeGrant(value!, USER_ID, SESSION_ID, NOW + 30)).toEqual({
      userId: USER_ID,
      sessionId: SESSION_ID,
      expiresAtSeconds: NOW + 600,
    })
    expect(verifyAdminModeGrant(value!, 'different-user', SESSION_ID, NOW + 30)).toBeNull()
    expect(verifyAdminModeGrant(value!, USER_ID, 'different-session', NOW + 30)).toBeNull()
  })

  test('rejects tampering, expiry, and a grant longer than the configured TTL', () => {
    const value = grant()!
    const [payload, signature] = value.split('.')
    expect(verifyAdminModeGrant(`${payload}x.${signature}`, USER_ID, SESSION_ID, NOW + 30)).toBeNull()
    expect(verifyAdminModeGrant(value, USER_ID, SESSION_ID, NOW + 600)).toBeNull()
    expect(createAdminModeGrant({
      userId: USER_ID,
      sessionId: SESSION_ID,
      issuedAtSeconds: NOW,
      expiresAtSeconds: NOW + 901,
    })).toBeNull()
  })

  test('fails closed when no sufficiently strong server secret is configured', () => {
    delete process.env.ADMIN_MODE_SIGNING_SECRET
    delete process.env.SUPABASE_SERVICE_ROLE_KEY
    delete process.env.SUPABASE_SERVICE_KEY
    expect(grant()).toBeNull()

    process.env.ADMIN_MODE_SIGNING_SECRET = 'too-short'
    expect(grant()).toBeNull()
  })

  test('binds a request cookie to the verified access-token session', () => {
    const accessToken = jwt({ sub: USER_ID, session_id: SESSION_ID })
    const value = grant()!
    const request = new Request('http://localhost/api/admin/profiles', {
      headers: { cookie: `${ADMIN_MODE_COOKIE_NAME}=${encodeURIComponent(value)}` },
    })

    expect(readSessionBinding(accessToken, USER_ID)).toBe(SESSION_ID)
    expect(verifyAdminModeRequest(request, USER_ID, accessToken, NOW + 30)).not.toBeNull()
    expect(verifyAdminModeRequest(request, USER_ID, jwt({ sub: USER_ID, session_id: 'new-session' }), NOW + 30)).toBeNull()
  })
})
