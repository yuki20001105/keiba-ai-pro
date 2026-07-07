import { NextRequest, NextResponse } from 'next/server'
import { SCRAPE_API_URL as ML_API_URL } from '@/lib/backend-url'

export async function GET(request: NextRequest) {
  try {
    const authHeader = request.headers.get('Authorization') || ''
    const limit = request.nextUrl.searchParams.get('limit') || '20'
    const response = await fetch(`${ML_API_URL}/api/scrape/history?limit=${encodeURIComponent(limit)}`, {
      headers: authHeader ? { Authorization: authHeader } : {},
      signal: AbortSignal.timeout(8_000),
    })
    const data = await response.json()
    return NextResponse.json(data, { status: response.status })
  } catch (error) {
    const message = error instanceof Error ? error.message : 'Unknown error'
    return NextResponse.json({ detail: message }, { status: 500 })
  }
}
