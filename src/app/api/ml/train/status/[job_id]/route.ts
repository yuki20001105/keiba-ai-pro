import { NextRequest, NextResponse } from 'next/server'

const ML_API_URL = process.env.ML_API_URL || process.env.NEXT_PUBLIC_API_URL || 'http://localhost:8000'

export async function GET(
  _request: NextRequest,
  { params }: { params: { job_id: string } }
) {
  try {
    const { job_id } = params
    const response = await fetch(`${ML_API_URL}/api/train/status/${job_id}`, {
      method: 'GET',
    })

    const data = await response.json()
    return NextResponse.json(data, { status: response.status })
  } catch (error) {
    console.error('Train status API error:', error)
    return NextResponse.json({ error: '学習ステータス取得中にエラーが発生しました' }, { status: 500 })
  }
}
