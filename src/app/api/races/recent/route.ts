import { NextRequest, NextResponse } from 'next/server'
import { ML_API_URL } from '@/lib/backend-url'
import { verifyRequestAuth } from '@/lib/server-auth'

export async function GET(request: NextRequest) {
  try {
    const authz = await verifyRequestAuth(request)
    if (!authz.ok) {
      return NextResponse.json({ detail: authz.detail }, { status: authz.status })
    }

    const { searchParams } = new URL(request.url)
    const limit = searchParams.get('limit') || '50'
    const response = await fetch(`${ML_API_URL}/api/races/recent?limit=${encodeURIComponent(limit)}`, {
      headers: { Authorization: `Bearer ${authz.context.token}` },
      signal: AbortSignal.timeout(10_000),
    })
    const data = await response.json()
    return NextResponse.json(data, { status: response.status })
  } catch (error) {
    const message = error instanceof Error ? error.message : 'Unknown error'
    return NextResponse.json({ detail: message }, { status: 500 })
  }
}
