import { NextResponse } from 'next/server'
import {
  getModelRetrainJobViaRpc,
  validateModelRetrainJobId,
} from '@/lib/model-retrain-job-ledger'
import { createSupabaseServiceClient, verifyRequestAuth } from '@/lib/server-auth'

export const runtime = 'nodejs'

function noStoreJson(body: unknown, status: number): NextResponse {
  return NextResponse.json(body, { status, headers: { 'Cache-Control': 'no-store' } })
}

export async function GET(
  request: Request,
  { params }: { params: Promise<{ job_id: string }> },
) {
  const authz = await verifyRequestAuth(request, { requireAdmin: true })
  if (!authz.ok) {
    return noStoreJson({ success: false, state: 'fail', code: 'authorization-failed', error: authz.detail }, authz.status)
  }
  const jobId = validateModelRetrainJobId((await params).job_id)
  if (!jobId.ok) return noStoreJson({ success: false, state: 'fail', code: 'job-id-invalid' }, 400)
  const serviceClient = createSupabaseServiceClient()
  if (!serviceClient) return noStoreJson({ success: false, state: 'fail', code: 'job-ledger-unavailable' }, 503)
  const result = await getModelRetrainJobViaRpc(serviceClient, authz.context.user.id, jobId.value)
  if (!result.ok) {
    return noStoreJson({ success: false, state: 'fail', code: 'job-read-failed', error: result.detail }, result.status)
  }
  return noStoreJson({ success: true, state: 'pass', code: 'job-record', job: result.value }, 200)
}
