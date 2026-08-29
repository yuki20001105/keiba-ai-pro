import { NextRequest, NextResponse } from 'next/server'
import {
  getReviewViaRpc,
  validateReviewRequestId,
  validateVerifiedUserId,
} from '@/lib/scrape-uncertainty-review-server'
import { createSupabaseServiceClient, verifyRequestAuth } from '@/lib/server-auth'

export const runtime = 'nodejs'

function noStoreJson(body: unknown, status: number): NextResponse {
  return NextResponse.json(body, {
    status,
    headers: { 'Cache-Control': 'no-store' },
  })
}

export async function GET(
  request: NextRequest,
  { params }: { params: Promise<{ requestId: string }> },
) {
  const authz = await verifyRequestAuth(request, { requireAdmin: true })
  if (!authz.ok) return noStoreJson({ detail: authz.detail }, authz.status)

  const actor = validateVerifiedUserId(authz.context.user.id)
  if (!actor.ok) return noStoreJson({ detail: actor.detail }, 503)
  const requestId = validateReviewRequestId((await params).requestId)
  if (!requestId.ok) return noStoreJson({ detail: requestId.detail }, 400)

  const serviceClient = createSupabaseServiceClient()
  if (!serviceClient) return noStoreJson({ detail: 'Review ledger configuration unavailable' }, 503)
  const result = await getReviewViaRpc(serviceClient, actor.value, requestId.value)
  if (!result.ok) return noStoreJson({ detail: result.detail }, result.status)
  return noStoreJson({ version: 1, request: result.value }, 200)
}
