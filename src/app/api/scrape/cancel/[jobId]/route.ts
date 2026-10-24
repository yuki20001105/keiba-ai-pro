import { NextRequest, NextResponse } from 'next/server'
import { SCRAPE_API_URL as ML_API_URL } from '@/lib/backend-url'
import { verifyRequestAuth } from '@/lib/server-auth'

const JOB_ID_PATTERN = /^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/i

function jsonNoStore(body: unknown, status: number) {
  return NextResponse.json(body, {
    status,
    headers: { 'Cache-Control': 'no-store' },
  })
}

export async function POST(
  request: NextRequest,
  { params }: { params: Promise<{ jobId: string }> },
) {
  const authz = await verifyRequestAuth(request, { requireAdminMode: true })
  if (!authz.ok) return jsonNoStore({ detail: authz.detail }, authz.status)

  try {
    const { jobId } = await params
    if (!JOB_ID_PATTERN.test(jobId)) {
      return jsonNoStore({ detail: 'jobId must be a complete UUID' }, 400)
    }

    const response = await fetch(`${ML_API_URL}/api/scrape/cancel/${jobId}`, {
      method: 'POST',
      headers: { Authorization: `Bearer ${authz.context.token}` },
      signal: AbortSignal.timeout(8_000),
      cache: 'no-store',
    })
    const data = await response.json().catch(() => null)
    if (data === null) {
      return jsonNoStore({ detail: 'Scrape cancellation service returned an invalid response' }, 502)
    }
    // Keep the upstream status and bounded JSON payload intact so callers can
    // distinguish accepted cancellation (202), terminal conflicts (409), and
    // fail-closed availability errors without guessing from a normalized 200.
    return NextResponse.json(data, {
      status: response.status,
      headers: { 'Cache-Control': 'no-store' },
    })
  } catch {
    return jsonNoStore({ detail: 'Scrape cancellation service unavailable' }, 502)
  }
}
