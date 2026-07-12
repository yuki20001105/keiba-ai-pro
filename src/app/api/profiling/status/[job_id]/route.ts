import { NextRequest, NextResponse } from 'next/server'
import { SCRAPE_API_URL as ML_API_URL } from '@/lib/backend-url'
import { verifyRequestAuth } from '@/lib/server-auth'

export async function GET(req: NextRequest, { params }: { params: Promise<{ job_id: string }> }) {
  try {
    const authz = await verifyRequestAuth(req, { requireAdmin: true })
    if (!authz.ok) {
      return NextResponse.json({ detail: authz.detail }, { status: authz.status })
    }

    const { job_id } = await params
    const res = await fetch(`${ML_API_URL}/api/profiling/status/${job_id}`, {
      headers: { Authorization: `Bearer ${authz.context.token}` },
      signal: AbortSignal.timeout(10_000),
    })
    const data = await res.json()
    if (!res.ok) return NextResponse.json(data, { status: res.status })
    return NextResponse.json(data)
  } catch (e) {
    return NextResponse.json({ detail: String(e) }, { status: 500 })
  }
}
