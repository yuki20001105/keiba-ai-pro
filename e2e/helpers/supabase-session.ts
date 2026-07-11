type SessionRole = 'admin' | 'user'
type SessionTier = 'free' | 'premium'

export type SupabaseSessionOptions = {
  userId?: string
  email?: string
  role?: SessionRole
  tier?: SessionTier
  expiresInSeconds?: number
  supabaseUrl?: string
  appBaseUrl?: string
}

type TestUser = {
  id: string
  email: string
  role: string
  aud: string
}

type TestSession = {
  access_token: string
  refresh_token: string
  token_type: 'bearer'
  expires_in: number
  expires_at: number
  user: TestUser
}

function base64UrlJson(value: unknown): string {
  return Buffer.from(JSON.stringify(value), 'utf8').toString('base64url')
}

export function getSupabaseStorageKey(supabaseUrl?: string): string {
  const url =
    supabaseUrl ||
    process.env.NEXT_PUBLIC_SUPABASE_URL ||
    process.env.SUPABASE_URL ||
    'http://127.0.0.1:54321'
  if (!url) {
    throw new Error('NEXT_PUBLIC_SUPABASE_URL is required for E2E Supabase session setup')
  }
  const hostPrefix = new URL(url).hostname.split('.')[0]
  return `sb-${hostPrefix}-auth-token`
}

export function getAppOrigin(appBaseUrl?: string): string {
  if (!appBaseUrl) {
    throw new Error('Playwright baseURL is required for E2E Supabase session setup')
  }
  return new URL(appBaseUrl).origin
}

export function buildTestJwt(opts: SupabaseSessionOptions = {}): string {
  const now = Math.floor(Date.now() / 1000)
  const exp = now + (opts.expiresInSeconds ?? 3600)
  const role = opts.role ?? 'user'
  const tier = opts.tier ?? 'free'
  const payload = {
    aud: 'authenticated',
    sub: opts.userId ?? 'e2e-user-id',
    email: opts.email ?? 'e2e@example.com',
    role: 'authenticated',
    iat: now,
    exp,
    app_metadata: {
      role,
      subscription_tier: tier,
    },
    user_metadata: {
      role,
      subscription_tier: tier,
    },
  }
  return `${base64UrlJson({ alg: 'HS256', typ: 'JWT' })}.${base64UrlJson(payload)}.e2e-signature`
}

export function buildTestSession(opts: SupabaseSessionOptions = {}): TestSession {
  const now = Math.floor(Date.now() / 1000)
  const expiresIn = opts.expiresInSeconds ?? 3600
  const expiresAt = now + expiresIn
  const userId = opts.userId ?? 'e2e-user-id'
  const email = opts.email ?? 'e2e@example.com'
  return {
    access_token: buildTestJwt(opts),
    refresh_token: `refresh-${userId}`,
    token_type: 'bearer',
    expires_in: expiresIn,
    expires_at: expiresAt,
    user: {
      id: userId,
      email,
      role: opts.role ?? 'user',
      aud: 'authenticated',
    },
  }
}

export function encodeSupabaseSsrCookieValue(session: TestSession): string {
  // @supabase/ssr uses base64url cookie encoding with the base64- prefix.
  return `base64-${Buffer.from(JSON.stringify(session), 'utf8').toString('base64url')}`
}

export function buildSupabaseSessionCookie(opts: SupabaseSessionOptions = {}): { name: string; value: string } {
  const name = getSupabaseStorageKey(opts.supabaseUrl)
  const value = encodeSupabaseSsrCookieValue(buildTestSession(opts))
  return { name, value }
}