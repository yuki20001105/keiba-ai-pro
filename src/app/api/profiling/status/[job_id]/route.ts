import { NextRequest, NextResponse } from 'next/server'

// プロファイリングもローカルFastAPIのみ対応
const ML_API_URL = process.env.SCRAPE_API_URL || 'http://localhost:8000'

export async function GET(req: NextRequest, { params }: { params: Promise<{ job_id: string }> }) {
  try {
    const { job_id } = await params
    const authHeader = req.headers.get('Authorization') || ''
    const res = await fetch(`${ML_API_URL}/api/profiling/status/${job_id}`, {
      headers: authHeader ? { Authorization: authHeader } : {},
    })
    const data = await res.json()
    if (!res.ok) return NextResponse.json(data, { status: res.status })
    return NextResponse.json(data)
  } catch (e) {
    return NextResponse.json({ detail: String(e) }, { status: 500 })
  }
}
