import { NextResponse } from 'next/server'
import { ML_API_URL } from '@/lib/backend-url'

export async function GET() {
  try {
    const response = await fetch(`${ML_API_URL}/health`, { signal: AbortSignal.timeout(4000), cache: 'no-store' })
    const data = await response.json()
    return NextResponse.json(data, { status: response.status, headers: { 'Cache-Control': 'no-store' } })
  } catch {
    return NextResponse.json({ status: 'offline' }, { status: 503, headers: { 'Cache-Control': 'no-store' } })
  }
}
