import { NextRequest, NextResponse } from 'next/server'
import { verifyRequestAuth } from '@/lib/server-auth'
import {
  ADMIN_MODE_COOKIE_NAME,
  ADMIN_MODE_TTL_SECONDS,
  createAdminModeGrant,
  readJwtPayload,
  readSessionBinding,
  verifyAdminModeRequest,
} from '@/lib/admin-mode'

export const runtime = 'nodejs'

const MAX_CLOCK_SKEW_SECONDS = 30

function noStoreJson(body: unknown, status: number): NextResponse {
  return NextResponse.json(body, {
    status,
    headers: { 'Cache-Control': 'no-store' },
  })
}

function readRecentPasswordTimestamp(
  payload: Record<string, unknown>,
  expectedUserId: string,
  nowSeconds: number,
): number | null {
  if (payload.sub !== expectedUserId || !Array.isArray(payload.amr)) return null

  let latestPasswordTimestamp: number | null = null
  for (const entry of payload.amr) {
    if (typeof entry !== 'object' || entry === null || Array.isArray(entry)) continue
    const record = entry as Record<string, unknown>
    if (
      record.method === 'password'
      && typeof record.timestamp === 'number'
      && Number.isSafeInteger(record.timestamp)
      && record.timestamp > 0
    ) {
      latestPasswordTimestamp = latestPasswordTimestamp === null
        ? record.timestamp
        : Math.max(latestPasswordTimestamp, record.timestamp)
    }
  }

  if (latestPasswordTimestamp === null) return null
  if (latestPasswordTimestamp > nowSeconds + MAX_CLOCK_SKEW_SECONDS) return null
  if (nowSeconds - latestPasswordTimestamp >= ADMIN_MODE_TTL_SECONDS) return null
  return latestPasswordTimestamp
}

function cookieOptions(expires?: Date, maxAge?: number) {
  return {
    httpOnly: true,
    secure: process.env.NODE_ENV === 'production',
    sameSite: 'strict' as const,
    path: '/',
    priority: 'high' as const,
    ...(expires ? { expires, maxAge } : { maxAge: 0 }),
  }
}

export async function GET(request: NextRequest) {
  const authz = await verifyRequestAuth(request, { requireAdmin: true })
  if (!authz.ok) return noStoreJson({ detail: authz.detail }, authz.status)

  const grant = verifyAdminModeRequest(request, authz.context.user.id, authz.context.token)
  if (!grant) return noStoreJson({ detail: 'Admin mode is locked' }, 403)

  return noStoreJson({
    version: 1,
    unlocked: true,
    expires_at: new Date(grant.expiresAtSeconds * 1000).toISOString(),
  }, 200)
}

export async function POST(request: NextRequest) {
  const authz = await verifyRequestAuth(request, { requireAdmin: true })
  if (!authz.ok) return noStoreJson({ detail: authz.detail }, authz.status)

  const payload = readJwtPayload(authz.context.token)
  const nowSeconds = Math.floor(Date.now() / 1000)
  const passwordTimestamp = payload
    ? readRecentPasswordTimestamp(payload, authz.context.user.id, nowSeconds)
    : null

  if (passwordTimestamp === null) {
    return noStoreJson({ detail: 'Recent password verification required' }, 403)
  }

  const expiresAtSeconds = Math.min(
    passwordTimestamp + ADMIN_MODE_TTL_SECONDS,
    nowSeconds + ADMIN_MODE_TTL_SECONDS,
  )
  const sessionId = readSessionBinding(authz.context.token, authz.context.user.id)
  const grant = sessionId
    ? createAdminModeGrant({
        userId: authz.context.user.id,
        sessionId,
        issuedAtSeconds: nowSeconds,
        expiresAtSeconds,
      })
    : null
  if (!grant) {
    return noStoreJson({ detail: 'Admin mode session could not be created' }, 503)
  }

  const response = noStoreJson({
    version: 1,
    unlocked: true,
    expires_at: new Date(expiresAtSeconds * 1000).toISOString(),
  }, 200)
  response.cookies.set(
    ADMIN_MODE_COOKIE_NAME,
    grant,
    cookieOptions(new Date(expiresAtSeconds * 1000), expiresAtSeconds - nowSeconds),
  )
  return response
}

export async function DELETE() {
  const response = noStoreJson({ version: 1, unlocked: false }, 200)
  response.cookies.set(ADMIN_MODE_COOKIE_NAME, '', cookieOptions())
  return response
}
