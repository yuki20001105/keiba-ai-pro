import { NextResponse } from 'next/server'
import {
  createModelRetrainApprovalViaRpc,
  readModelRetrainApprovalJsonBody,
  validateCreateModelRetrainApprovalBody,
} from '@/lib/model-retrain-approval-ledger'
import { createSupabaseServiceClient, verifyRequestAuth } from '@/lib/server-auth'

export const runtime = 'nodejs'

function noStoreJson(body: unknown, status: number): NextResponse {
  return NextResponse.json(body, {
    status,
    headers: { 'Cache-Control': 'no-store' },
  })
}

export async function POST(request: Request) {
  const authz = await verifyRequestAuth(request, { requireAdmin: true })
  if (!authz.ok) {
    return noStoreJson({ success: false, state: 'fail', code: 'authorization-failed', error: authz.detail }, authz.status)
  }

  const bounded = await readModelRetrainApprovalJsonBody(request)
  if (!bounded.ok) {
    const status = bounded.detail === 'request body is too large' ? 413 : 400
    return noStoreJson({ success: false, state: 'fail', code: 'request-invalid', error: bounded.detail }, status)
  }
  const parsed = validateCreateModelRetrainApprovalBody(bounded.value, authz.context.user.id)
  if (!parsed.ok) {
    return noStoreJson({ success: false, state: 'fail', code: 'approval-request-invalid', error: parsed.detail }, 400)
  }

  const serviceClient = createSupabaseServiceClient()
  if (!serviceClient) {
    return noStoreJson({ success: false, state: 'fail', code: 'approval-ledger-unavailable' }, 503)
  }
  const result = await createModelRetrainApprovalViaRpc(
    serviceClient,
    authz.context.user.id,
    parsed.value,
  )
  if (!result.ok) {
    return noStoreJson({ success: false, state: 'fail', code: 'approval-create-failed', error: result.detail }, result.status)
  }
  return noStoreJson({
    success: true,
    state: 'pass',
    code: 'approval-pending',
    approval: result.value,
    guard: {
      durable_record_created: true,
      execution_enabled: false,
      job_created: result.value.job_created,
      model_artifact_written: false,
      active_model_switched: false,
    },
  }, 201)
}
