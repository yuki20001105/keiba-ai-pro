import { NextRequest, NextResponse } from 'next/server'
import { ML_API_URL } from '@/lib/backend-url'

// Proxy for /api/features/catalog
export async function GET(request: NextRequest) {
  try {
    const authorization = request.headers.get('Authorization')
    const headers: Record<string, string> = {}
    if (authorization) headers.Authorization = authorization
    const response = await fetch(`${ML_API_URL}/api/features/catalog`, {
      headers,
      signal: AbortSignal.timeout(30_000),
    })
    const data = await response.json()
    return NextResponse.json(data, { status: response.status })
  } catch (error) {
    const message = error instanceof Error ? error.message : 'Unknown error'
    return NextResponse.json({ detail: message }, { status: 500 })
  }
}
