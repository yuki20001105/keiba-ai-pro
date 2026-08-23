import { NextRequest, NextResponse } from 'next/server'
import { ML_API_URL } from '@/lib/backend-url'
import { verifyRequestAuth } from '@/lib/server-auth'

export async function GET(req: NextRequest, { params }: { params: Promise<{ job_id: string }> }) {
  try {
    const authz = await verifyRequestAuth(req, { requireAdmin: true })
    if (!authz.ok) {
      return NextResponse.json({ detail: authz.detail }, { status: authz.status })
    }

    const { job_id } = await params
    const res = await fetch(`${ML_API_URL}/api/profiling/html/${job_id}`, {
      headers: { Authorization: `Bearer ${authz.context.token}` },
    })
    if (!res.ok) {
      const data = await res.json().catch(() => ({}))
      return NextResponse.json(data, { status: res.status })
    }
    const html = await res.text()
    return new Response(html, {
      headers: { 'Content-Type': 'text/html; charset=utf-8' },
    })
  } catch (e) {
    return NextResponse.json({ detail: String(e) }, { status: 500 })
  }
}
