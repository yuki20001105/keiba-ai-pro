import { NextResponse } from 'next/server'
import {
  getModelRetrainApprovalViaRpc,
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

export async function GET(
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
  const serviceClient = createSupabaseServiceClient()
  if (!serviceClient) {
    return noStoreJson({ success: false, state: 'fail', code: 'approval-ledger-unavailable' }, 503)
  }
  const result = await getModelRetrainApprovalViaRpc(
    serviceClient,
    authz.context.user.id,
    approvalId.value,
  )
  if (!result.ok) {
    return noStoreJson({ success: false, state: 'fail', code: 'approval-read-failed', error: result.detail }, result.status)
  }
  return noStoreJson({ success: true, state: 'pass', code: 'approval-record', approval: result.value }, 200)
}
