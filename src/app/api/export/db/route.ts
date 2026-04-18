import { NextRequest, NextResponse } from 'next/server'
import { ML_API_URL } from '@/lib/backend-url'

export async function GET(request: NextRequest) {
  try {
    const { searchParams } = new URL(request.url)
    const query = searchParams.toString()
    // StreamingResponse (SQLite binary) をそのままクライアントに転送
    const response = await fetch(`${ML_API_URL}/api/export-db${query ? `?${query}` : ''}`, { signal: AbortSignal.timeout(60_000) })
    if (!response.ok) {
      const text = await response.text()
      return new NextResponse(text, { status: response.status })
    }
    const contentDisposition = response.headers.get('Content-Disposition') || 'attachment; filename="keiba_ultimate.db"'
    return new NextResponse(response.body, {
      status: 200,
      headers: {
        'Content-Type': 'application/octet-stream',
        'Content-Disposition': contentDisposition,
      },
    })
  } catch (error) {
    const message = error instanceof Error ? error.message : 'Unknown error'
    return NextResponse.json({ detail: message }, { status: 500 })
  }
}
