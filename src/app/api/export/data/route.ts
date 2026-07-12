import { NextRequest, NextResponse } from 'next/server'
import { ML_API_URL } from '@/lib/backend-url'

export async function GET(request: NextRequest) {
  try {
    const { searchParams } = new URL(request.url)
    const query = searchParams.toString()
    const authorization = request.headers.get('Authorization')
    const headers: Record<string, string> = {}
    if (authorization) headers.Authorization = authorization
    const response = await fetch(`${ML_API_URL}/api/export-data${query ? `?${query}` : ''}`, {
      headers,
      signal: AbortSignal.timeout(60_000),
    })
    const data = await response.json()
    return NextResponse.json(data, { status: response.status })
  } catch (error) {
    const message = error instanceof Error ? error.message : 'Unknown error'
    return NextResponse.json({ detail: message }, { status: 500 })
  }
}
