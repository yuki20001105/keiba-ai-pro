import { NextRequest, NextResponse } from 'next/server'
import { ML_API_URL } from '@/lib/backend-url'
import { explicitLocalOptInEnabled } from '@/lib/legacy-local-policy'

const LEGACY_TRUE_VALUES = new Set(['1', 'true', 'yes', 'on'])

export async function POST(request: NextRequest) {
  if (!explicitLocalOptInEnabled('PHASE3N_ALLOW_LEGACY_SCRAPE_WRITES', LEGACY_TRUE_VALUES)) {
    return NextResponse.json({
      success: false,
      state: 'fail',
      code: 'legacy-rescrape-disabled',
      error: 'Direct rescrape is disabled; use the approval-bound operational saga path.',
    }, {
      status: 503,
      headers: { 'Cache-Control': 'no-store' },
    })
  }
  try {
    const { searchParams } = new URL(request.url)
    const limit = searchParams.get('limit') || '50'
    const authHeader = request.headers.get('Authorization') || ''
    const response = await fetch(`${ML_API_URL}/api/rescrape_incomplete?limit=${encodeURIComponent(limit)}`, {
      method: 'POST',
      headers: authHeader ? { Authorization: authHeader } : {},
      signal: AbortSignal.timeout(180_000),
    })
    const data = await response.json()
    return NextResponse.json(data, { status: response.status })
  } catch (error) {
    const message = error instanceof Error ? error.message : 'Unknown error'
    return NextResponse.json({ detail: message }, { status: 500 })
  }
}
