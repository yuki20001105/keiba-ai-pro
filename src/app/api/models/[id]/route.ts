import { NextRequest, NextResponse } from 'next/server'
import { ML_API_URL } from '@/lib/backend-url'
import { explicitLocalOptInEnabled } from '@/lib/legacy-local-policy'

export async function GET(
  request: NextRequest,
  { params }: { params: Promise<{ id: string }> }
) {
  try {
    const { id } = await params
    const authHeader = request.headers.get('Authorization') || ''
    const response = await fetch(`${ML_API_URL}/api/models/${id}`, {
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

export async function DELETE(
  request: NextRequest,
  { params }: { params: Promise<{ id: string }> }
) {
  if (!explicitLocalOptInEnabled('MODEL_DELETION_LOCAL_ENABLED')) {
    return NextResponse.json({
      success: false,
      state: 'fail',
      code: 'separate-model-retirement-approval-required',
      error: 'Model deletion is disabled until a separate durable retirement approval is implemented.',
    }, {
      status: 409,
      headers: { 'Cache-Control': 'no-store' },
    })
  }
  try {
    const { id } = await params
    const authHeader = request.headers.get('Authorization') || ''
    const response = await fetch(`${ML_API_URL}/api/models/${id}`, {
      method: 'DELETE',
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
