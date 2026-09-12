import { NextRequest, NextResponse } from 'next/server'
import { ML_API_URL } from '@/lib/backend-url'
import { explicitLocalOptInEnabled, isLoopbackRequest, isLoopbackUrl } from '@/lib/legacy-local-policy'
import { verifyRequestAuth } from '@/lib/server-auth'

const UUID_PATTERN = /^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/i

function noStoreJson(body: unknown, status: number) {
  return NextResponse.json(body, {
    status,
    headers: { 'Cache-Control': 'no-store' },
  })
}

export async function GET(
  request: NextRequest,
  { params }: { params: Promise<{ job_id: string }> }
) {
  if (!isLoopbackRequest(request)) {
    return noStoreJson({ detail: 'Local access required' }, 403)
  }
  if (!explicitLocalOptInEnabled('MODEL_TRAINING_LOCAL_ENABLED')) {
    return noStoreJson({ detail: 'Local model training is disabled' }, 409)
  }
  if (!isLoopbackUrl(ML_API_URL)) {
    return noStoreJson({ detail: 'Local model training API is not loopback' }, 409)
  }

  // Training ledger details remain behind the same current-password Admin
  // grant as job creation, even though the backend projection is read-only.
  const authz = await verifyRequestAuth(request, { requireAdminMode: true })
  if (!authz.ok) return noStoreJson({ detail: authz.detail }, authz.status)

  try {
    const { job_id } = await params
    if (!UUID_PATTERN.test(job_id)) {
      return noStoreJson({ detail: 'Invalid job ID' }, 400)
    }

    const response = await fetch(`${ML_API_URL}/api/train/status/${job_id}`, {
      headers: { Authorization: `Bearer ${authz.context.token}` },
      cache: 'no-store',
      signal: AbortSignal.timeout(8_000),
      redirect: 'error',
    })

    const data: unknown = await response.json().catch(() => null)
    if (data === null) {
      return noStoreJson({ detail: 'Model training status service returned an invalid response' }, 502)
    }
    return NextResponse.json(data, {
      status: response.status,
      headers: { 'Cache-Control': 'no-store' },
    })
  } catch {
    return noStoreJson({ detail: 'Model training status service unavailable' }, 502)
  }
}
