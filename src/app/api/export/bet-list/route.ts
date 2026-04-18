import { NextRequest, NextResponse } from 'next/server'
import { SCRAPE_API_URL as ML_API_URL } from '@/lib/backend-url'

export async function POST(request: NextRequest) {
  try {
    const body = await request.json()
    // CSV ダウンロードの場合
    const isCsv = request.nextUrl.searchParams.get('format') === 'csv'
    const endpoint = isCsv ? '/api/export/bet-list/csv' : '/api/export/bet-list'

    const res = await fetch(`${ML_API_URL}${endpoint}`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(body),
      signal: AbortSignal.timeout(30_000),
    })

    if (isCsv) {
      const csvText = await res.text()
      return new NextResponse(csvText, {
        status: res.ok ? 200 : res.status,
        headers: {
          'Content-Type': 'text/csv; charset=utf-8',
          'Content-Disposition': res.headers.get('Content-Disposition') || 'attachment; filename=bet_list.csv',
        },
      })
    }

    const data = await res.json()
    return NextResponse.json(data, { status: res.ok ? 200 : res.status })
  } catch (error) {
    const message = error instanceof Error ? error.message : 'Unknown error'
    return NextResponse.json({ error: message }, { status: 500 })
  }
}
