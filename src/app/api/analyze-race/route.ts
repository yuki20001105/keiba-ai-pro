import { NextRequest, NextResponse } from 'next/server'
import { ML_API_URL } from '@/lib/backend-url'

// Next.js Route Handler のデフォルト30秒制限を解除（過去レース再スクレイプ対応）
export const maxDuration = 300

export async function POST(request: NextRequest) {
  try {
    const body = await request.json()
    const authHeader = request.headers.get('Authorization') || ''

    const response = await fetch(`${ML_API_URL}/api/analyze_race`, {
      method: 'POST',
      headers: {
        'Content-Type': 'application/json',
        ...(authHeader ? { Authorization: authHeader } : {}),
      },
      body: JSON.stringify(body),
      cache: 'no-store',
      signal: AbortSignal.timeout(300_000),  // 300s: 過去レースの再スクレイプ(~60s)含む場合に対応
    })

    const data: unknown = await response.json().catch(() => null)
    if (typeof data !== 'object' || data === null || Array.isArray(data)) {
      return NextResponse.json(
        { detail: '予測サービスから有効な JSON 応答を受け取れませんでした。時間をおいて再実行してください。' },
        { status: response.ok ? 502 : response.status, headers: { 'Cache-Control': 'no-store' } },
      )
    }
    return NextResponse.json(data, { status: response.status, headers: { 'Cache-Control': 'no-store' } })
  } catch (error) {
    const message = error instanceof Error ? error.message : 'Unknown error'
    return NextResponse.json({ detail: message }, { status: 500, headers: { 'Cache-Control': 'no-store' } })
  }
}
