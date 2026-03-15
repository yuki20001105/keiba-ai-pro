import { NextRequest, NextResponse } from 'next/server'

// プロファイリングもローカルFastAPIのみ対応
const ML_API_URL = process.env.SCRAPE_API_URL || 'http://localhost:8000'

export async function POST(request: NextRequest) {
  try {
    const { use_optimized = true } = await request.json().catch(() => ({}))
    const url = `${ML_API_URL}/api/profiling/start?use_optimized=${use_optimized}`
    const res = await fetch(url, { method: 'POST' })
    const data = await res.json()
    if (!res.ok) return NextResponse.json(data, { status: res.status })
    return NextResponse.json(data)
  } catch (e) {
    return NextResponse.json({ detail: String(e) }, { status: 500 })
  }
}
