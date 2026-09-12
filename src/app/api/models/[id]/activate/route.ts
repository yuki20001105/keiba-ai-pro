import { NextRequest, NextResponse } from 'next/server'
import { ML_API_URL } from '@/lib/backend-url'
import { explicitLocalOptInEnabled, isLoopbackRequest, isLoopbackUrl } from '@/lib/legacy-local-policy'
import { verifyRequestAuth } from '@/lib/server-auth'

const MODEL_ID_PATTERN = /^[A-Za-z0-9][A-Za-z0-9_-]{0,199}$/

function noStoreJson(body: unknown, status: number) {
  return NextResponse.json(body, {
    status,
    headers: { 'Cache-Control': 'no-store' },
  })
}

export async function PUT(
  request: NextRequest,
  { params }: { params: Promise<{ id: string }> }
) {
  if (!isLoopbackRequest(request)) {
    return noStoreJson({ detail: 'Local access required' }, 403)
  }
  if (!explicitLocalOptInEnabled('MODEL_ACTIVATION_LOCAL_ENABLED')) {
    return noStoreJson({
      success: false,
      state: 'fail',
      code: 'separate-activation-approval-required',
      error: 'Active model switching is disabled until a separate durable approval is implemented.',
    }, 409)
  }
  if (!isLoopbackUrl(ML_API_URL)) {
    return noStoreJson({ detail: 'Local model API required' }, 409)
  }

  const authz = await verifyRequestAuth(request, { requireAdminMode: true })
  if (!authz.ok) return noStoreJson({ detail: authz.detail }, authz.status)

  const { id } = await params
  if (!MODEL_ID_PATTERN.test(id)) {
    return noStoreJson({ detail: 'Invalid model ID' }, 400)
  }

  try {
    const response = await fetch(`${ML_API_URL}/api/models/${encodeURIComponent(id)}/activate`, {
      method: 'PUT',
      headers: { Authorization: `Bearer ${authz.context.token}` },
      signal: AbortSignal.timeout(10_000),
      cache: 'no-store',
      redirect: 'error',
    })
    const data: unknown = await response.json().catch(() => null)
    if (data === null) {
      return noStoreJson({ detail: 'Model service returned an invalid response' }, 502)
    }
    return noStoreJson(data, response.status)
  } catch {
    return noStoreJson({ detail: 'Model service unavailable' }, 502)
  }
}
