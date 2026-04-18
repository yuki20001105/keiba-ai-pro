import { NextRequest, NextResponse } from 'next/server'
import { ML_API_URL } from '@/lib/backend-url'

export async function POST(
  request: NextRequest,
  { params }: { params: Promise<{ race_id: string }> }
) {
  try {
    const { race_id } = await params
    const authHeader = request.headers.get('Authorization') || ''
    const response = await fetch(`${ML_API_URL}/api/scrape/repair/${race_id}`, {
      method: 'POST',
      headers: authHeader ? { Authorization: authHeader } : {},
    })
    const data = await response.json()
    return NextResponse.json(data, { status: response.status })
  } catch (error) {
    const message = error instanceof Error ? error.message : 'Unknown error'
    return NextResponse.json({ detail: message }, { status: 500 })
  }
}
