import { NextRequest, NextResponse } from 'next/server'
import { ML_API_URL } from '@/lib/backend-url'
import { explicitLocalOptInEnabled } from '@/lib/legacy-local-policy'

export async function POST(request: NextRequest) {
  if (!explicitLocalOptInEnabled('MODEL_TRAINING_LOCAL_ENABLED')) {
    return NextResponse.json({
      success: false,
      state: 'fail',
      code: 'approval-bound-model-training-required',
      error: 'Direct model training is disabled until an approval-bound durable job runner is implemented.',
    }, {
      status: 409,
      headers: { 'Cache-Control': 'no-store' },
    })
  }
  try {
    const body = await request.json()
    const authHeader = request.headers.get('Authorization') || ''

    const response = await fetch(`${ML_API_URL}/api/train/start`, {
      method: 'POST',
      headers: {
        'Content-Type': 'application/json',
        ...(authHeader ? { Authorization: authHeader } : {}),
      },
      body: JSON.stringify(body),
      signal: AbortSignal.timeout(30_000),
    })

    const data = await response.json()
    return NextResponse.json(data, { status: response.status })
  } catch (error) {
    const message = error instanceof Error ? error.message : 'Unknown error'
    return NextResponse.json({ detail: message }, { status: 500 })
  }
}
