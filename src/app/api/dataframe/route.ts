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
    const wantsCsv = searchParams.get('format') === 'csv'
    const wantsFeatures = searchParams.get('dataset') === 'features'
    searchParams.delete('format')
    searchParams.delete('dataset')
    const endpoint = wantsFeatures
      ? wantsCsv ? '/api/dataframe/features/csv' : '/api/dataframe/features'
      : wantsCsv ? '/api/dataframe/csv' : '/api/dataframe'
    const response = await fetch(`${ML_API_URL}${endpoint}?${searchParams.toString()}`, {
      headers: { Authorization: `Bearer ${authz.context.token}` },
      cache: 'no-store',
      signal: AbortSignal.timeout(wantsCsv || wantsFeatures ? 300_000 : 60_000),
    })

    const headers = new Headers({ 'Cache-Control': 'no-store' })
    const contentType = response.headers.get('Content-Type')
    const contentDisposition = response.headers.get('Content-Disposition')
    if (contentType) headers.set('Content-Type', contentType)
    if (contentDisposition) headers.set('Content-Disposition', contentDisposition)
    return new Response(response.body, { status: response.status, headers })
  } catch (error) {
    const message = error instanceof Error ? error.message : 'Unknown error'
    return NextResponse.json({ detail: message }, { status: 500 })
  }
}
