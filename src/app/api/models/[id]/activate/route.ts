import { NextRequest, NextResponse } from 'next/server'
import { ML_API_URL } from '@/lib/backend-url'

const LOCAL_ENVIRONMENTS = new Set(['local', 'development', 'dev', 'test', 'ci'])

function legacyLocalActivationEnabled(): boolean {
  const environment = (process.env.APP_ENV || '').trim().toLowerCase()
  return LOCAL_ENVIRONMENTS.has(environment)
    && (process.env.MODEL_ACTIVATION_LOCAL_ENABLED || '').trim().toLowerCase() === 'true'
}

export async function PUT(
  request: NextRequest,
  { params }: { params: Promise<{ id: string }> }
) {
  if (!legacyLocalActivationEnabled()) {
    return NextResponse.json({
      success: false,
      state: 'fail',
      code: 'separate-activation-approval-required',
      error: 'Active model switching is disabled until a separate durable approval is implemented.',
    }, {
      status: 409,
      headers: { 'Cache-Control': 'no-store' },
    })
  }
  try {
    const { id } = await params
    const authHeader = request.headers.get('Authorization') || ''
    const response = await fetch(`${ML_API_URL}/api/models/${id}/activate`, {
      method: 'PUT',
      headers: authHeader ? { Authorization: authHeader } : {},
      signal: AbortSignal.timeout(10_000),
    })
    const data = await response.json()
    return NextResponse.json(data, { status: response.status })
  } catch (error) {
    const message = error instanceof Error ? error.message : 'Unknown error'
    return NextResponse.json({ detail: message }, { status: 500 })
  }
}
