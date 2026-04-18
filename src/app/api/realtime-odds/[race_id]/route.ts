import { NextRequest, NextResponse } from 'next/server'
import { SCRAPE_API_URL as ML_API_URL } from '@/lib/backend-url'

export async function GET(
  request: NextRequest,
  { params }: { params: Promise<{ race_id: string }> }
) {
  const { race_id } = await params
  const types = request.nextUrl.searchParams.get('types') || 'tansho,umaren'
  try {
    const res = await fetch(
      `${ML_API_URL}/api/realtime-odds/${race_id}?types=${encodeURIComponent(types)}`,
      { signal: AbortSignal.timeout(20_000) }
    )
    const data = await res.json()
    return NextResponse.json(data, { status: res.ok ? 200 : res.status })
  } catch (error) {
    const message = error instanceof Error ? error.message : 'Unknown error'
    return NextResponse.json({ error: message }, { status: 500 })
  }
}
