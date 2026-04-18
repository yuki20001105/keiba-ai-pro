import { NextRequest, NextResponse } from 'next/server'
import { ML_API_URL as API_URL } from '@/lib/backend-url'

export async function GET(request: NextRequest) {
  try {
    const res = await fetch(`${API_URL}/api/statistics`, {
      headers: { 'Content-Type': 'application/json' },
      cache: 'no-store',
      signal: AbortSignal.timeout(10_000),
    })
    const data = await res.json()
    return NextResponse.json(data, { status: res.status })
  } catch (error) {
    return NextResponse.json({ detail: String(error) }, { status: 500 })
  }
}
