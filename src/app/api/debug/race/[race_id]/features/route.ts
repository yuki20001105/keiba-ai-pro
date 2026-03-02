import { NextRequest, NextResponse } from 'next/server'

const ML_API_URL = process.env.ML_API_URL || process.env.NEXT_PUBLIC_API_URL || 'http://localhost:8000'

export async function GET(
  request: NextRequest,
  { params }: { params: Promise<{ race_id: string }> }
) {
  try {
    const { race_id } = await params
    const authHeader = request.headers.get('Authorization') || ''
    const response = await fetch(`${ML_API_URL}/api/debug/race/${race_id}/features`, {
      headers: authHeader ? { Authorization: authHeader } : {},
    })
    const data = await response.json()
    return NextResponse.json(data, { status: response.status })
  } catch (error) {
    const message = error instanceof Error ? error.message : 'Unknown error'
    return NextResponse.json({ detail: message }, { status: 500 })
  }
}
