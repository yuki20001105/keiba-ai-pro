import { NextRequest, NextResponse } from 'next/server'
import { SCRAPE_API_URL as ML_API_URL } from '@/lib/backend-url'

export async function POST(request: NextRequest) {
  try {
    const body = await request.json()
    const authorization = request.headers.get('Authorization')
    // CSV ダウンロードの場合
    const isCsv = request.nextUrl.searchParams.get('format') === 'csv'

    const headers: Record<string, string> = { 'Content-Type': 'application/json' }
    if (authorization) headers.Authorization = authorization

    const res = isCsv
      ? await fetch(`${ML_API_URL}/api/export/bet-list/csv`, {
          method: 'POST',
          headers,
          body: JSON.stringify(body),
          signal: AbortSignal.timeout(30_000),
        })
      : await fetch(`${ML_API_URL}/api/export/bet-list`, {
          method: 'POST',
          headers,
          body: JSON.stringify(body),
          signal: AbortSignal.timeout(30_000),
        })

    if (isCsv) {
      const csvText = await res.text()
      return new NextResponse(csvText, {
        status: res.status,
        headers: {
          'Content-Type': 'text/csv; charset=utf-8',
          'Content-Disposition': res.headers.get('Content-Disposition') || 'attachment; filename=bet_list.csv',
        },
      })
    }

    const data = await res.json()
    return NextResponse.json(data, { status: res.status })
  } catch (error) {
    const message = error instanceof Error ? error.message : 'Unknown error'
    return NextResponse.json({ detail: message }, { status: 500 })
  }
}
