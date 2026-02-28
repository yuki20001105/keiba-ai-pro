import { NextRequest, NextResponse } from 'next/server'

const ML_API_URL = process.env.ML_API_URL || process.env.NEXT_PUBLIC_API_URL || 'http://localhost:8000'

export async function GET(_req: NextRequest, { params }: { params: Promise<{ job_id: string }> }) {
  try {
    const { job_id } = await params
    const res = await fetch(`${ML_API_URL}/api/profiling/status/${job_id}`)
    const data = await res.json()
    if (!res.ok) return NextResponse.json(data, { status: res.status })
    return NextResponse.json(data)
  } catch (e) {
    return NextResponse.json({ detail: String(e) }, { status: 500 })
  }
}
