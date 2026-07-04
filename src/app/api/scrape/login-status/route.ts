import { NextRequest, NextResponse } from 'next/server'
import { SCRAPE_API_URL as ML_API_URL } from '@/lib/backend-url'

export async function GET(request: NextRequest) {
  try {
    const authHeader = request.headers.get('Authorization') || ''
    const response = await fetch(`${ML_API_URL}/api/scrape/login-status`, {
      method: 'GET',
      headers: { ...(authHeader ? { Authorization: authHeader } : {}) },
      signal: AbortSignal.timeout(5_000),
    })
    const data = await response.json()
    return NextResponse.json(data, { status: response.ok ? 200 : response.status })
  } catch (error) {
    const message = error instanceof Error ? error.message : 'Unknown error'
    return NextResponse.json({ detail: message }, { status: 500 })
  }
}
