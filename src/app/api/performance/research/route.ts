import { NextRequest, NextResponse } from 'next/server'
import { ML_API_URL } from '@/lib/backend-url'
import { verifyRequestAuth } from '@/lib/server-auth'

const noStore = { 'Cache-Control': 'no-store' }

export async function GET(request: NextRequest) {
  const auth = await verifyRequestAuth(request)
  if (!auth.ok) return NextResponse.json({ detail: auth.detail }, { status: auth.status, headers: noStore })
  const query = new URLSearchParams()
  for (const key of ['policy', 'scope', 'date_from', 'date_to']) {
    const value = request.nextUrl.searchParams.get(key)
    if (value !== null) query.set(key, value)
  }
  try {
    const response = await fetch(`${ML_API_URL}/api/performance/research?${query}`, {
      headers: { Authorization: `Bearer ${auth.context.token}` },
      cache: 'no-store', signal: AbortSignal.timeout(15_000),
    })
    return NextResponse.json(await response.json(), { status: response.status, headers: noStore })
  } catch {
    return NextResponse.json({ detail: '研究成績を読み込めませんでした' }, { status: 502, headers: noStore })
  }
}
