import { NextRequest, NextResponse } from 'next/server'

const ML_API_URL = process.env.ML_API_URL || process.env.NEXT_PUBLIC_API_URL || 'http://localhost:8000'

export async function GET(req: NextRequest, { params }: { params: Promise<{ job_id: string }> }) {
  try {
    const { job_id } = await params
    const authHeader = req.headers.get('Authorization') || ''
    const res = await fetch(`${ML_API_URL}/api/profiling/html/${job_id}`, {
      headers: authHeader ? { Authorization: authHeader } : {},
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
