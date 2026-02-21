import { NextRequest, NextResponse } from 'next/server'

const ML_API_URL = process.env.ML_API_URL || process.env.NEXT_PUBLIC_API_URL || 'http://localhost:8000'

export async function GET(request: NextRequest) {
  try {
    const { searchParams } = new URL(request.url)
    const query = searchParams.toString()
    const url = `${ML_API_URL}/api/data_stats${query ? `?${query}` : ''}`

    const response = await fetch(url)
    const data = await response.json()
    return NextResponse.json(data, { status: response.status })
  } catch (error) {
    const message = error instanceof Error ? error.message : 'Unknown error'
    return NextResponse.json({ detail: message }, { status: 500 })
  }
}
