import { NextRequest, NextResponse } from 'next/server'
import { ML_API_URL } from '@/lib/backend-url'
import { verifyRequestAuth } from '@/lib/server-auth'

const noStore = (body: unknown, status: number) => NextResponse.json(body, {
  status, headers: { 'Cache-Control': 'no-store' },
})

/** Metadata only: never run predictions, download models or start acquisition. */
export async function GET(request: NextRequest) {
  const auth = await verifyRequestAuth(request)
  if (!auth.ok) return noStore({ detail: auth.detail }, auth.status)
  const values = request.nextUrl.searchParams.getAll('model_id')
  const modelId = values[0]
  if (values.length > 1 || (modelId !== undefined && (
    modelId.length > 200 || !/^[A-Za-z0-9_-]+$/.test(modelId)
  ))) return noStore({ detail: 'Invalid model_id' }, 400)
  try {
    const query = modelId ? `?model_id=${encodeURIComponent(modelId)}` : ''
    const response = await fetch(`${ML_API_URL}/api/prediction-freshness${query}`, {
      headers: { Authorization: `Bearer ${auth.context.token}` },
      cache: 'no-store', signal: AbortSignal.timeout(8_000),
    })
    const data: unknown = await response.json().catch(() => null)
    if (!data || typeof data !== 'object' || Array.isArray(data)) {
      return noStore({ detail: 'Prediction freshness service returned an invalid response' }, 502)
    }
    return NextResponse.json(data, { status: response.status, headers: { 'Cache-Control': 'no-store' } })
  } catch {
    return noStore({ detail: 'Prediction freshness is unconfirmed; retain the saved prediction' }, 502)
  }
}
