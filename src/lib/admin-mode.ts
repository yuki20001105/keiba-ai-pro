import { createHmac, timingSafeEqual } from 'node:crypto'

export const ADMIN_MODE_COOKIE_NAME = 'keiba_admin_mode'
export const ADMIN_MODE_TTL_SECONDS = 15 * 60

const ADMIN_MODE_VERSION = 1
const MAX_CLOCK_SKEW_SECONDS = 30
const MAX_COOKIE_LENGTH = 4_096
const MAX_JWT_PAYLOAD_LENGTH = 32_768

type AdminModePayload = {
  v: typeof ADMIN_MODE_VERSION
  sub: string
  sid: string
  iat: number
  exp: number
}

type AdminModeGrantInput = {
  userId: string
  sessionId: string
  issuedAtSeconds: number
  expiresAtSeconds: number
}

type AdminModeGrant = {
  userId: string
  sessionId: string
  expiresAtSeconds: number
}

function encode(value: string | Buffer): string {
  return Buffer.from(value).toString('base64url')
}

function getSigningSecret(): string | null {
  const secret = process.env.ADMIN_MODE_SIGNING_SECRET
    || process.env.SUPABASE_SERVICE_ROLE_KEY
    || process.env.SUPABASE_SERVICE_KEY
    || ''
  return secret.length >= 32 ? secret : null
}

export function isAdminModeConfigured(): boolean {
  return getSigningSecret() !== null
}

function sign(encodedPayload: string, secret: string): string {
  return createHmac('sha256', secret).update(encodedPayload).digest('base64url')
}

function safeString(value: unknown, maxLength: number): value is string {
  return typeof value === 'string'
    && value.length > 0
    && value.length <= maxLength
    && !/[\u0000-\u001f\u007f]/.test(value)
}

function isPayload(value: unknown): value is AdminModePayload {
  if (typeof value !== 'object' || value === null || Array.isArray(value)) return false
  const record = value as Record<string, unknown>
  return record.v === ADMIN_MODE_VERSION
    && safeString(record.sub, 128)
    && safeString(record.sid, 256)
    && typeof record.iat === 'number'
    && Number.isSafeInteger(record.iat)
    && typeof record.exp === 'number'
    && Number.isSafeInteger(record.exp)
}

function readCookie(request: Request, name: string): string | null {
  const cookieHeader = request.headers.get('cookie')
  if (!cookieHeader) return null

  for (const part of cookieHeader.split(';')) {
    const separator = part.indexOf('=')
    if (separator < 0 || part.slice(0, separator).trim() !== name) continue
    const value = part.slice(separator + 1).trim()
    try {
      return decodeURIComponent(value)
    } catch {
      return null
    }
  }
  return null
}

export function readJwtPayload(token: string): Record<string, unknown> | null {
  const parts = token.split('.')
  if (parts.length !== 3 || parts[1].length === 0 || parts[1].length > MAX_JWT_PAYLOAD_LENGTH) return null

  try {
    const value: unknown = JSON.parse(Buffer.from(parts[1], 'base64url').toString('utf8'))
    return typeof value === 'object' && value !== null && !Array.isArray(value)
      ? value as Record<string, unknown>
      : null
  } catch {
    return null
  }
}

export function readSessionBinding(token: string, expectedUserId: string): string | null {
  const payload = readJwtPayload(token)
  if (!payload || payload.sub !== expectedUserId || !safeString(payload.session_id, 256)) return null
  return payload.session_id
}

export function createAdminModeGrant(input: AdminModeGrantInput): string | null {
  const secret = getSigningSecret()
  if (
    !secret
    || !safeString(input.userId, 128)
    || !safeString(input.sessionId, 256)
    || !Number.isSafeInteger(input.issuedAtSeconds)
    || !Number.isSafeInteger(input.expiresAtSeconds)
    || input.expiresAtSeconds <= input.issuedAtSeconds
    || input.expiresAtSeconds - input.issuedAtSeconds > ADMIN_MODE_TTL_SECONDS
  ) {
    return null
  }

  const payload: AdminModePayload = {
    v: ADMIN_MODE_VERSION,
    sub: input.userId,
    sid: input.sessionId,
    iat: input.issuedAtSeconds,
    exp: input.expiresAtSeconds,
  }
  const encodedPayload = encode(JSON.stringify(payload))
  return `${encodedPayload}.${sign(encodedPayload, secret)}`
}

export function verifyAdminModeGrant(
  value: string,
  expectedUserId: string,
  expectedSessionId: string,
  nowSeconds = Math.floor(Date.now() / 1000),
): AdminModeGrant | null {
  const secret = getSigningSecret()
  if (!secret || value.length === 0 || value.length > MAX_COOKIE_LENGTH) return null

  const parts = value.split('.')
  if (parts.length !== 2 || parts[0].length === 0 || parts[1].length === 0) return null

  const expectedSignature = Buffer.from(sign(parts[0], secret), 'base64url')
  let suppliedSignature: Buffer
  try {
    suppliedSignature = Buffer.from(parts[1], 'base64url')
  } catch {
    return null
  }
  if (
    expectedSignature.length !== suppliedSignature.length
    || !timingSafeEqual(expectedSignature, suppliedSignature)
  ) return null

  let decoded: unknown
  try {
    decoded = JSON.parse(Buffer.from(parts[0], 'base64url').toString('utf8'))
  } catch {
    return null
  }
  if (!isPayload(decoded)) return null
  if (decoded.sub !== expectedUserId || decoded.sid !== expectedSessionId) return null
  if (decoded.iat > nowSeconds + MAX_CLOCK_SKEW_SECONDS) return null
  if (decoded.exp <= nowSeconds || decoded.exp - decoded.iat > ADMIN_MODE_TTL_SECONDS) return null

  return {
    userId: decoded.sub,
    sessionId: decoded.sid,
    expiresAtSeconds: decoded.exp,
  }
}

export function verifyAdminModeRequest(
  request: Request,
  expectedUserId: string,
  accessToken: string,
  nowSeconds = Math.floor(Date.now() / 1000),
): AdminModeGrant | null {
  const sessionId = readSessionBinding(accessToken, expectedUserId)
  const cookie = readCookie(request, ADMIN_MODE_COOKIE_NAME)
  if (!sessionId || !cookie) return null
  return verifyAdminModeGrant(cookie, expectedUserId, sessionId, nowSeconds)
}
