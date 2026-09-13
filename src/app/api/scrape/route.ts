import { NextRequest, NextResponse } from 'next/server'
import { SCRAPE_API_URL as ML_API_URL } from '@/lib/backend-url'
import { verifyRequestAuth } from '@/lib/server-auth'

function jsonNoStore(body: unknown, status: number) {
  return NextResponse.json(body, {
    status,
    headers: { 'Cache-Control': 'no-store' },
  })
}

export async function POST(request: NextRequest) {
  // Revalidate the current role on every new submission. A long-lived local
  // screen must never turn a stale browser role into execution authorization.
  const authz = await verifyRequestAuth(request, { requireAdmin: true })
  if (!authz.ok) return jsonNoStore({ detail: authz.detail }, authz.status)

  let body: unknown
  try {
    body = await request.json()
  } catch {
    return jsonNoStore({ detail: 'Scrape request must be valid JSON' }, 400)
  }
  if (typeof body !== 'object' || body === null || Array.isArray(body)) {
    return jsonNoStore({ detail: 'Scrape request must be an object' }, 400)
  }

  try {
    const response = await fetch(`${ML_API_URL}/api/scrape/start`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json', Authorization: `Bearer ${authz.context.token}` },
      body: JSON.stringify(body),
      signal: AbortSignal.timeout(10_000),
      cache: 'no-store',
    })

    const data: unknown = await response.json().catch(() => null)
    if (typeof data !== 'object' || data === null || Array.isArray(data)) {
      return jsonNoStore({ detail: 'Scrape start service returned an invalid response' }, 502)
    }
    return jsonNoStore(data, response.status)
  } catch {
    // The server may have accepted the durable job before transport failed.
    // Keep this ambiguous: the client reconciles its pre-persisted job ID.
    return jsonNoStore({ detail: 'Scrape start service unavailable; check the submitted job status' }, 502)
  }
}
