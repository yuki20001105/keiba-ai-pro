import { NextRequest, NextResponse } from 'next/server'
import { ML_API_URL } from '@/lib/backend-url'

export async function GET(
  _request: NextRequest,
  { params }: { params: Promise<{ race_id: string }> }
) {
  try {
    const { race_id } = await params
    const response = await fetch(`${ML_API_URL}/api/races/${race_id}/horses`, { signal: AbortSignal.timeout(10_000) })
    const data = await response.json()
    return NextResponse.json(data, { status: response.status })
  } catch (error) {
    const message = error instanceof Error ? error.message : 'Unknown error'
    return NextResponse.json({ detail: message }, { status: 500 })
  }
}
