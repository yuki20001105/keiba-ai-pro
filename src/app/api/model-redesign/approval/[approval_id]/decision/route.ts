import { NextResponse } from 'next/server'
import {
  readModelRetrainApprovalJsonBody,
  transitionModelRetrainApprovalViaRpc,
  validateModelRetrainApprovalDecisionBody,
  validateModelRetrainApprovalId,
} from '@/lib/model-retrain-approval-ledger'
import { createSupabaseServiceClient, verifyRequestAuth } from '@/lib/server-auth'

export const runtime = 'nodejs'

function noStoreJson(body: unknown, status: number): NextResponse {
  return NextResponse.json(body, {
    status,
    headers: { 'Cache-Control': 'no-store' },
  })
}

export async function POST(
  request: Request,
  { params }: { params: Promise<{ approval_id: string }> },
) {
  const authz = await verifyRequestAuth(request, { requireAdmin: true })
  if (!authz.ok) {
    return noStoreJson({ success: false, state: 'fail', code: 'authorization-failed', error: authz.detail }, authz.status)
  }
  const approvalId = validateModelRetrainApprovalId((await params).approval_id)
  if (!approvalId.ok) {
    return noStoreJson({ success: false, state: 'fail', code: 'approval-id-invalid' }, 400)
  }
  const bounded = await readModelRetrainApprovalJsonBody(request)
  if (!bounded.ok) {
    const status = bounded.detail === 'request body is too large' ? 413 : 400
    return noStoreJson({ success: false, state: 'fail', code: 'request-invalid', error: bounded.detail }, status)
  }
  const parsed = validateModelRetrainApprovalDecisionBody(bounded.value)
  if (!parsed.ok) {
    return noStoreJson({ success: false, state: 'fail', code: 'approval-decision-invalid', error: parsed.detail }, 400)
  }
  const serviceClient = createSupabaseServiceClient()
  if (!serviceClient) {
    return noStoreJson({ success: false, state: 'fail', code: 'approval-ledger-unavailable' }, 503)
  }
  const result = await transitionModelRetrainApprovalViaRpc(
    serviceClient,
    authz.context.user.id,
    approvalId.value,
    parsed.value,
  )
  if (!result.ok) {
    return noStoreJson({ success: false, state: 'fail', code: 'approval-transition-failed', error: result.detail }, result.status)
  }
  return noStoreJson({
    success: true,
    state: 'pass',
    code: `approval-${result.value.approval_record.approval_status}`,
    approval: result.value,
    guard: {
      execution_enabled: false,
      job_created: false,
      model_artifact_written: false,
      active_model_switched: false,
    },
  }, 200)
}
