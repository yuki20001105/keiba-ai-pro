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

export async function GET(
  request: NextRequest,
  { params }: { params: Promise<{ jobId: string }> }
) {
  const authz = await verifyRequestAuth(request, { requireAdmin: true })
  if (!authz.ok) return jsonNoStore({ detail: authz.detail }, authz.status)

  try {
    const { jobId } = await params
    if (!JOB_ID_PATTERN.test(jobId)) {
      return jsonNoStore({ detail: 'jobId must be a complete UUID' }, 400)
    }
    const response = await fetch(`${ML_API_URL}/api/scrape/status/${jobId}`, {
      headers: { Authorization: `Bearer ${authz.context.token}` },
      signal: AbortSignal.timeout(8_000),
      cache: 'no-store',
    })
    const data = await response.json().catch(() => null)
    if (data === null) return jsonNoStore({ detail: 'Scrape status service returned an invalid response' }, 502)
    return NextResponse.json(data, {
      status: response.status,
      headers: { 'Cache-Control': 'no-store' },
    })
  } catch {
    return jsonNoStore({ detail: 'Scrape status service unavailable' }, 502)
  }
}
