import { NextRequest, NextResponse } from 'next/server'
import { SCRAPE_API_URL as ML_API_URL } from '@/lib/backend-url'
import { verifyRequestAuth } from '@/lib/server-auth'

/**
 * 特定日のレースID一覧を取得
 * FastAPI の read-only プロキシ経由で取得
 */
export async function POST(request: NextRequest) {
  try {
    const authz = await verifyRequestAuth(request, { requireAdmin: true })
    if (!authz.ok) {
      return NextResponse.json({ detail: authz.detail }, { status: authz.status })
    }

    const { date } = await request.json()

    if (!date) {
      return NextResponse.json(
        { error: '日付が必要です' },
        { status: 400 }
      )
    }

    // YYYY-MM-DD → YYYYMMDD
    const dateStr = date.replace(/-/g, '')
    
    console.log(`[race-list] Fetching race list for date: ${dateStr}`)

    const authHeader = `Bearer ${authz.context.token}`
    const response = await fetch(`${ML_API_URL}/api/netkeiba/race-list?date=${encodeURIComponent(dateStr)}`, {
      method: 'GET',
      headers: { Authorization: authHeader },
      signal: AbortSignal.timeout(20_000),
    })

    if (!response.ok) {
      const errorData = await response.json().catch(() => ({}))
      return NextResponse.json(
        { error: errorData.error || errorData.detail || `HTTP ${response.status}` },
        { status: response.status }
      )
    }

    const data = await response.json()
    const raceIds = data.raceIds || data.races || []

    console.log(`[race-list] Response from FastAPI proxy:`, data)
    console.log(`[race-list] Found ${raceIds?.length || 0} races for ${dateStr}`)

    return NextResponse.json({ 
      raceIds,
      count: raceIds.length 
    })
  } catch (error: any) {
    console.error('[race-list] Error:', error)
    return NextResponse.json(
      { error: 'レースIDの取得に失敗しました: ' + error.message },
      { status: 500 }
    )
  }
}
