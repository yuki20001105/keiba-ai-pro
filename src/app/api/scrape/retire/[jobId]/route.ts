import { NextRequest, NextResponse } from 'next/server'
import { SCRAPE_API_URL } from '@/lib/backend-url'
import { verifyRequestAuth } from '@/lib/server-auth'

const UUID = /^[0-9a-f]{8}-[0-9a-f]{4}-[1-8][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/i
const FIELDS = new Set(['operation_id', 'start_date', 'end_date', 'force_rescrape', 'server_batch', 'race_scope'])
const isRecord = (value: unknown): value is Record<string, unknown> =>
  typeof value === 'object' && value !== null && !Array.isArray(value)

function isDate(value: unknown): value is string {
  if (typeof value !== 'string' || !/^\d{8}$/.test(value)) return false
  const iso = `${value.slice(0, 4)}-${value.slice(4, 6)}-${value.slice(6, 8)}`
  const parsed = new Date(`${iso}T00:00:00Z`)
  return Number.isFinite(parsed.getTime()) && parsed.toISOString().slice(0, 10) === iso
}

function jsonNoStore(body: unknown, status: number) {
  return NextResponse.json(body, { status, headers: { 'Cache-Control': 'no-store' } })
}

/** Retire an absent saved request, never enqueue or cancel a running scrape. */
export async function POST(request: NextRequest, { params }: { params: Promise<{ jobId: string }> }) {
  const authz = await verifyRequestAuth(request, { requireAdminMode: true })
  if (!authz.ok) return jsonNoStore({ detail: authz.detail }, authz.status)
  const { jobId } = await params
  if (!UUID.test(jobId)) return jsonNoStore({ detail: 'jobId must be a complete UUID' }, 400)
  let body: unknown
  try { body = await request.json() } catch {
    return jsonNoStore({ detail: 'Saved request must be valid JSON' }, 400)
  }
  if (!isRecord(body) || Object.keys(body).some(key => !FIELDS.has(key))
    || typeof body.operation_id !== 'string' || !UUID.test(body.operation_id)
    || !isDate(body.start_date) || !isDate(body.end_date) || body.start_date > body.end_date
    || typeof body.force_rescrape !== 'boolean' || body.server_batch !== true
    || (body.race_scope !== undefined && body.race_scope !== 'all' && body.race_scope !== 'jra')) {
    return jsonNoStore({ detail: 'Original saved request binding is required' }, 400)
  }
  try {
    const response = await fetch(`${SCRAPE_API_URL}/api/scrape/retire/${jobId}`, {
      method: 'POST', headers: { 'Content-Type': 'application/json', Authorization: `Bearer ${authz.context.token}` },
      body: JSON.stringify(body), signal: AbortSignal.timeout(8_000), cache: 'no-store',
    })
    const data: unknown = await response.json().catch(() => null)
    if (!isRecord(data)) return jsonNoStore({ detail: 'Saved request retirement service returned an invalid response' }, 502)
    return NextResponse.json(data, { status: response.status, headers: { 'Cache-Control': 'no-store' } })
  } catch {
    // A receipt may have been durably recorded; retain identity until a retry
    // confirms the same tombstone. Transport failure is never release proof.
    return jsonNoStore({ detail: 'Saved request retirement is unconfirmed; retain the saved request' }, 502)
  }
}
