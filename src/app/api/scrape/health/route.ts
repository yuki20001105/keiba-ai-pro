import { NextRequest, NextResponse } from 'next/server'
import { SCRAPE_API_URL as ML_API_URL } from '@/lib/backend-url'

export async function GET(request: NextRequest) {
  const timestamp = new Date().toISOString()
  try {
    const authHeader = request.headers.get('Authorization') || ''
    const response = await fetch(`${ML_API_URL}/api/scrape/health`, {
      headers: authHeader ? { Authorization: authHeader } : {},
      signal: AbortSignal.timeout(5_000),
    })

    const data = await response.json().catch(() => ({
      success: false,
      status: 'unknown',
      service: 'scrape',
      timestamp,
      reason: 'invalid health response payload',
    }))

    return NextResponse.json(data, { status: response.status })
  } catch (error) {
    const reason = error instanceof Error ? error.message : 'unknown error'
    return NextResponse.json(
      {
        success: false,
        status: 'unknown',
        service: 'scrape',
        timestamp,
        reason: `health probe failed: ${reason}`,
      },
      { status: 503 }
    )
  }
}
