import { NextResponse } from 'next/server'
import { readModelRetrainApprovalJsonBody } from '@/lib/model-retrain-approval-ledger'
import {
  createModelRetrainJobViaRpc,
  validateCreateModelRetrainJobBody,
} from '@/lib/model-retrain-job-ledger'
import { createSupabaseServiceClient, verifyRequestAuth } from '@/lib/server-auth'

export const runtime = 'nodejs'

function noStoreJson(body: unknown, status: number): NextResponse {
  return NextResponse.json(body, { status, headers: { 'Cache-Control': 'no-store' } })
}

export async function POST(request: Request) {
  const authz = await verifyRequestAuth(request, { requireAdmin: true })
  if (!authz.ok) {
    return noStoreJson({ success: false, state: 'fail', code: 'authorization-failed', error: authz.detail }, authz.status)
  }
  const bounded = await readModelRetrainApprovalJsonBody(request)
  if (!bounded.ok) {
    return noStoreJson({ success: false, state: 'fail', code: 'request-invalid', error: bounded.detail }, bounded.detail === 'request body is too large' ? 413 : 400)
  }
  const parsed = validateCreateModelRetrainJobBody(bounded.value)
  if (!parsed.ok) {
    return noStoreJson({ success: false, state: 'fail', code: 'job-request-invalid', error: parsed.detail }, 400)
  }
  const serviceClient = createSupabaseServiceClient()
  if (!serviceClient) return noStoreJson({ success: false, state: 'fail', code: 'job-ledger-unavailable' }, 503)
  const result = await createModelRetrainJobViaRpc(serviceClient, authz.context.user.id, parsed.value)
  if (!result.ok) {
    return noStoreJson({ success: false, state: 'fail', code: 'job-create-failed', error: result.detail }, result.status)
  }
  return noStoreJson({
    success: true,
    state: 'pass',
    code: 'approval-bound-job-queued',
    job: result.value,
    guard: {
      durable_job_created: true,
      execution_started: false,
      model_artifact_written: false,
      active_model_switched: false,
    },
  }, 201)
}
