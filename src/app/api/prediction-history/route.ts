import { NextRequest, NextResponse } from 'next/server'
import { ML_API_URL } from '@/lib/backend-url'

export async function GET(request: NextRequest) {
  try {
    const { searchParams } = new URL(request.url)
    const authHeader = request.headers.get('Authorization') || ''

    const params = new URLSearchParams()
    const limit = searchParams.get('limit')
    const raceDate = searchParams.get('race_date')
    if (limit) params.set('limit', limit)
    if (raceDate) params.set('race_date', raceDate)

    const url = `${ML_API_URL}/api/prediction-history?${params.toString()}`
    const response = await fetch(url, {
      headers: { ...(authHeader ? { Authorization: authHeader } : {}) },
      signal: AbortSignal.timeout(30_000),
    })

    const data = await response.json()
    return NextResponse.json(data, { status: response.status })
  } catch (error) {
    const message = error instanceof Error ? error.message : 'Unknown error'
    return NextResponse.json({ detail: message }, { status: 500 })
  }
}
