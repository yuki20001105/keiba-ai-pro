import { NextRequest, NextResponse } from 'next/server'

// スクレイピングはローカルFastAPIのみ対応（クラウドIPブロック・タイムアウト回避）
const ML_API_URL = process.env.SCRAPE_API_URL || 'http://localhost:8000'

export async function POST(request: NextRequest) {
  try {
    const body = await request.json()

    const authHeader = request.headers.get('Authorization') || ''
    const response = await fetch(`${ML_API_URL}/api/scrape/start`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json', ...(authHeader ? { Authorization: authHeader } : {}) },
      body: JSON.stringify(body),
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
