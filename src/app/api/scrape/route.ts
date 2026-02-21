import { NextRequest, NextResponse } from 'next/server'

const ML_API_URL = process.env.ML_API_URL || process.env.NEXT_PUBLIC_API_URL || 'http://localhost:8000'

// Vercel の最大実行時間（Pro: 300秒、Hobby: 60秒）
export const maxDuration = 300

export async function POST(request: NextRequest) {
  try {
    const body = await request.json()

    const response = await fetch(`${ML_API_URL}/api/scrape`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(body),
      // タイムアウトなし（スクレイピングは長時間かかる）
    })

    const data = await response.json()

    if (!response.ok) {
      return NextResponse.json(data, { status: response.status })
    }

    return NextResponse.json(data)
  } catch (error) {
    const message = error instanceof Error ? error.message : 'Unknown error'
    return NextResponse.json({ detail: message }, { status: 500 })
  }
}
