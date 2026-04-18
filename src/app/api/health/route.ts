import { NextResponse } from 'next/server'
import { ML_API_URL } from '@/lib/backend-url'

export async function GET() {
  try {
    const response = await fetch(`${ML_API_URL}/health`, { signal: AbortSignal.timeout(4000) })
    const data = await response.json()
    return NextResponse.json(data, { status: response.status })
  } catch {
    return NextResponse.json({ status: 'offline' }, { status: 503 })
  }
}
