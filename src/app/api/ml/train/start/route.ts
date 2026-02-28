import { NextRequest, NextResponse } from 'next/server'

const ML_API_URL = process.env.ML_API_URL || process.env.NEXT_PUBLIC_API_URL || 'http://localhost:8000'

export async function POST(request: NextRequest) {
  try {
    const body = await request.json()
    const authHeader = request.headers.get('Authorization') || ''

    const response = await fetch(`${ML_API_URL}/api/train/start`, {
      method: 'POST',
      headers: {
        'Content-Type': 'application/json',
        ...(authHeader ? { Authorization: authHeader } : {}),
      },
      body: JSON.stringify(body),
    })

    const data = await response.json()
    return NextResponse.json(data, { status: response.status })
  } catch (error) {
    console.error('Train start API error:', error)
    if (error instanceof TypeError && (error as TypeError).message.includes('fetch')) {
      return NextResponse.json(
        { error: 'Python APIサーバーに接続できません。FastAPIを起動してください。' },
        { status: 503 }
      )
    }
    return NextResponse.json({ error: '学習ジョブ起動中にエラーが発生しました' }, { status: 500 })
  }
}
