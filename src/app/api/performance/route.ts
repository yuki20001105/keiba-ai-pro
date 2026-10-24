import { NextRequest, NextResponse } from 'next/server'
import { ML_API_URL } from '@/lib/backend-url'
import { verifyRequestAuth } from '@/lib/server-auth'

const noStore = { 'Cache-Control': 'no-store' }

function validDate(value: string): boolean {
  if (!/^\d{8}$/.test(value)) return false
  const iso = `${value.slice(0, 4)}-${value.slice(4, 6)}-${value.slice(6, 8)}`
  const date = new Date(`${iso}T00:00:00Z`)
  return Number.isFinite(date.getTime()) && date.toISOString().slice(0, 10) === iso
}

export async function GET(request: NextRequest) {
  const auth = await verifyRequestAuth(request)
  if (!auth.ok) return NextResponse.json({ detail: auth.detail }, { status: auth.status, headers: noStore })
  const params = request.nextUrl.searchParams
  const mode = params.get('mode') || 'paper'
  const limit = params.get('limit') || '100'
  const start = params.get('date_from')
  const end = params.get('date_to')
  if (!['paper', 'actual'].includes(mode) || !/^\d+$/.test(limit) || Number(limit) < 1 || Number(limit) > 200
    || (start !== null && !validDate(start)) || (end !== null && !validDate(end)) || (start && end && start > end)) {
    return NextResponse.json({ detail: '表示条件が不正です' }, { status: 400, headers: noStore })
  }
  const query = new URLSearchParams({ mode, limit })
  if (start) query.set('date_from', start)
  if (end) query.set('date_to', end)
  try {
    const response = await fetch(`${ML_API_URL}/api/performance?${query}`, {
      headers: { Authorization: `Bearer ${auth.context.token}` },
      cache: 'no-store', signal: AbortSignal.timeout(15_000),
    })
    const data = await response.json()
    return NextResponse.json(data, { status: response.status, headers: noStore })
  } catch {
    return NextResponse.json({ detail: '成績を読み込めませんでした。再試行してください' }, { status: 502, headers: noStore })
  }
}
