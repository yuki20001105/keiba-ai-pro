import { NextRequest, NextResponse } from 'next/server'
import { ML_API_URL } from '@/lib/backend-url'
import { verifyRequestAuth } from '@/lib/server-auth'
import {
  validateLiveValidationApiResponse,
  validateLiveValidationRequestBody,
} from '@/lib/targeted-refetch-live-validation-contract'

export const runtime = 'nodejs'
export const maxDuration = 120

const BACKEND_TIMEOUT_MS = 95_000

function noStoreJson(body: unknown, status: number): NextResponse {
  return NextResponse.json(body, {
    status,
    headers: { 'Cache-Control': 'no-store' },
  })
}

function safeDetail(value: unknown, fallback: string): string {
  if (typeof value !== 'string') return fallback
  const detail = value.trim().slice(0, 240)
  if (!detail || /[\x00-\x1f\x7f]/.test(detail)) return fallback
  if (
    /file:\/\//i.test(detail) ||
    /[A-Za-z]:[\\/]/.test(detail) ||
    /\\\\[^\\\s]+\\/.test(detail) ||
    /(^|[^A-Za-z0-9_])\/[A-Za-z0-9._-]/.test(detail) ||
    /(^|[^A-Za-z0-9_])(?:~|\.\.)[\\/]/.test(detail)
  ) return fallback
  return detail
}

export async function POST(request: NextRequest) {
  const authz = await verifyRequestAuth(request, { requireAdmin: true })
  if (!authz.ok) return noStoreJson({ detail: authz.detail || 'forbidden' }, authz.status)

  let rawBody: unknown
  try {
    rawBody = await request.json()
  } catch {
    return noStoreJson({ detail: 'invalid JSON body' }, 400)
  }

  const validated = validateLiveValidationRequestBody(rawBody)
  if (!validated.ok) return noStoreJson({ detail: validated.error }, 400)

  try {
    const response = await fetch(`${ML_API_URL}/api/scrape/live-validation`, {
      method: 'POST',
      headers: {
        Authorization: `Bearer ${authz.context.token}`,
        'Content-Type': 'application/json',
        Accept: 'application/json',
      },
      body: JSON.stringify(validated.value),
      cache: 'no-store',
      signal: AbortSignal.timeout(BACKEND_TIMEOUT_MS),
    })

    const payload = await response.json().catch(() => null)
    if (!response.ok) {
      const message = safeDetail(
        payload && typeof payload === 'object' ? (payload as Record<string, unknown>).detail : null,
        `live validation failed (HTTP ${response.status})`,
      )
      return NextResponse.json({ detail: message }, {
        status: response.status,
        headers: { 'Cache-Control': 'no-store' },
      })
    }

    const parsed = validateLiveValidationApiResponse(payload, validated.value)
    if (!parsed.ok) return noStoreJson({ detail: parsed.error }, 502)
    return noStoreJson(parsed.value, 200)
  } catch (error: unknown) {
    const isTimeout = error instanceof Error && (error.name === 'TimeoutError' || error.name === 'AbortError')
    return noStoreJson(
      { detail: isTimeout ? 'live validation timed out' : 'live validation backend unavailable' },
      isTimeout ? 504 : 503,
    )
  }
}
