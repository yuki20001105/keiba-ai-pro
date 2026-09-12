import { NextRequest, NextResponse } from 'next/server'
import { ML_API_URL } from '@/lib/backend-url'
import { explicitLocalOptInEnabled, isLoopbackRequest, isLoopbackUrl } from '@/lib/legacy-local-policy'
import { verifyRequestAuth } from '@/lib/server-auth'

function noStoreJson(body: unknown, status: number) {
  return NextResponse.json(body, {
    status,
    headers: { 'Cache-Control': 'no-store' },
  })
}

export async function GET(request: NextRequest) {
  if (!isLoopbackRequest(request)) {
    return noStoreJson({ enabled: false, reason: 'loopback-required' }, 403)
  }
  if (!explicitLocalOptInEnabled('MODEL_TRAINING_LOCAL_ENABLED')) {
    return noStoreJson({ enabled: false, reason: 'local-training-disabled' }, 409)
  }
  if (!isLoopbackUrl(ML_API_URL)) {
    return noStoreJson({ enabled: false, reason: 'backend-not-local' }, 409)
  }

  const authz = await verifyRequestAuth(request, { requireAdminMode: true })
  if (!authz.ok) {
    return noStoreJson({ enabled: false, reason: authz.detail }, authz.status)
  }

  try {
    const response = await fetch(`${ML_API_URL}/api/train/capability`, {
      headers: { Authorization: `Bearer ${authz.context.token}` },
      cache: 'no-store',
      signal: AbortSignal.timeout(5_000),
      redirect: 'error',
    })
    const payload: unknown = await response.json()
    const record = typeof payload === 'object' && payload !== null && !Array.isArray(payload)
      ? payload as Record<string, unknown>
      : null
    const enabled = response.ok && record?.enabled === true

    return noStoreJson({
      enabled,
      reason: enabled ? null : (typeof record?.reason === 'string' ? record.reason : 'backend-unavailable'),
    }, response.ok ? 200 : response.status)
  } catch {
    return noStoreJson({ enabled: false, reason: 'backend-unavailable' }, 503)
  }
}
