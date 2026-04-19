import { NextRequest, NextResponse } from 'next/server'
import { ML_API_URL } from '@/lib/backend-url'

// Proxy for /api/features/importance
export async function GET(request: NextRequest) {
  try {
    const { searchParams } = new URL(request.url)
    const query = searchParams.toString()
    const url = `${ML_API_URL}/api/features/importance${query ? `?${query}` : ''}`
    const authHeader = request.headers.get('Authorization') || ''
    const response = await fetch(url, {
      headers: authHeader ? { Authorization: authHeader } : {},
      signal: AbortSignal.timeout(30_000),
    })
    const data = await response.json()
    return NextResponse.json(data, { status: response.status })
  } catch (error) {
    const message = error instanceof Error ? error.message : 'Unknown error'
    return NextResponse.json({ detail: message }, { status: 500 })
  }
}
