import { NextRequest, NextResponse } from 'next/server'
import { ML_API_URL } from '@/lib/backend-url'
import { explicitLocalOptInEnabled, isLoopbackRequest, isLoopbackUrl } from '@/lib/legacy-local-policy'
import { verifyRequestAuth } from '@/lib/server-auth'

const ALLOWED_TRAIN_FIELDS = [
  'target',
  'model_type',
  'test_size',
  'cv_folds',
  'use_sqlite',
  'ultimate_mode',
  'use_optimizer',
  'use_optuna',
  'optuna_trials',
  'optuna_timeout',
  'training_date_from',
  'training_date_to',
] as const

function noStoreJson(body: unknown, status: number) {
  return NextResponse.json(body, {
    status,
    headers: { 'Cache-Control': 'no-store' },
  })
}

export async function POST(request: NextRequest) {
  if (!isLoopbackRequest(request)) {
    return noStoreJson({ detail: 'Local access required' }, 403)
  }
  if (!explicitLocalOptInEnabled('MODEL_TRAINING_LOCAL_ENABLED')) {
    return noStoreJson({
      success: false,
      state: 'fail',
      code: 'approval-bound-model-training-required',
      error: 'Direct model training is disabled until an approval-bound durable job runner is implemented.',
    }, 409)
  }
  if (!isLoopbackUrl(ML_API_URL)) {
    return noStoreJson({
      success: false,
      state: 'fail',
      code: 'local-training-upstream-required',
      error: 'Model training must use a loopback API.',
    }, 409)
  }

  const authz = await verifyRequestAuth(request, { requireAdminMode: true })
  if (!authz.ok) return noStoreJson({ detail: authz.detail }, authz.status)

  let body: unknown
  try {
    body = await request.json()
  } catch {
    return noStoreJson({ detail: 'Invalid JSON body' }, 400)
  }
  if (typeof body !== 'object' || body === null || Array.isArray(body)) {
    return noStoreJson({ detail: 'Invalid training request' }, 400)
  }

  const requestBody = body as Record<string, unknown>
  const safeBody: Record<string, unknown> = { force_sync: false }
  for (const field of ALLOWED_TRAIN_FIELDS) {
    if (Object.prototype.hasOwnProperty.call(requestBody, field)) {
      safeBody[field] = requestBody[field]
    }
  }

  try {
    const response = await fetch(`${ML_API_URL}/api/train/start`, {
      method: 'POST',
      headers: {
        'Content-Type': 'application/json',
        Authorization: `Bearer ${authz.context.token}`,
      },
      body: JSON.stringify(safeBody),
      signal: AbortSignal.timeout(30_000),
      cache: 'no-store',
      redirect: 'error',
    })

    const data: unknown = await response.json().catch(() => null)
    if (data === null) {
      return noStoreJson({ detail: 'Model training service returned an invalid response' }, 502)
    }
    return NextResponse.json(data, {
      status: response.status,
      headers: { 'Cache-Control': 'no-store' },
    })
  } catch {
    return noStoreJson({ detail: 'Model training service unavailable' }, 502)
  }
}
