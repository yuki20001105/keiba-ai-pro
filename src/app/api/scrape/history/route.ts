import { NextRequest, NextResponse } from 'next/server'
import { SCRAPE_API_URL as ML_API_URL } from '@/lib/backend-url'
import { verifyRequestAuth } from '@/lib/server-auth'

function jsonNoStore(body: unknown, status: number) {
  return NextResponse.json(body, {
    status,
    headers: { 'Cache-Control': 'no-store' },
  })
}

export async function GET(request: NextRequest) {
  const authz = await verifyRequestAuth(request, { requireAdmin: true })
  if (!authz.ok) return jsonNoStore({ detail: authz.detail }, authz.status)

  try {
    const limitRaw = request.nextUrl.searchParams.get('limit') || '20'
    if (!/^\d{1,3}$/.test(limitRaw)) return jsonNoStore({ detail: 'limit must be an integer from 1 to 100' }, 400)
    const limit = Number(limitRaw)
    if (!Number.isInteger(limit) || limit < 1 || limit > 100) {
      return jsonNoStore({ detail: 'limit must be an integer from 1 to 100' }, 400)
    }
    const response = await fetch(`${ML_API_URL}/api/scrape/history?limit=${limit}`, {
      headers: { Authorization: `Bearer ${authz.context.token}` },
      signal: AbortSignal.timeout(8_000),
      cache: 'no-store',
    })
    const data = await response.json().catch(() => null)
    if (data === null) return jsonNoStore({ detail: 'Scrape history service returned an invalid response' }, 502)
    return NextResponse.json(data, {
      status: response.status,
      headers: { 'Cache-Control': 'no-store' },
    })
  } catch {
    return jsonNoStore({ detail: 'Scrape history service unavailable' }, 502)
  }
}
