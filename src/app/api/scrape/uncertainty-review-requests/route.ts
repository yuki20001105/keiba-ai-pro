import { NextRequest, NextResponse } from 'next/server'
import {
  createReviewViaRpc,
  listReviewsViaRpc,
  readBoundedJsonBody,
  validateCreateReviewBody,
  validateListReviewQuery,
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

export async function POST(request: NextRequest) {
  const authz = await verifyRequestAuth(request, { requireAdmin: true })
  if (!authz.ok) return noStoreJson({ detail: authz.detail }, authz.status)

  const actor = validateVerifiedUserId(authz.context.user.id)
  if (!actor.ok) return noStoreJson({ detail: actor.detail }, 503)

  const bounded = await readBoundedJsonBody(request)
  if (!bounded.ok) {
    const status = bounded.detail === 'request body is too large' ? 413 : 400
    return noStoreJson({ detail: bounded.detail }, status)
  }
  const parsed = validateCreateReviewBody(bounded.value)
  if (!parsed.ok) return noStoreJson({ detail: parsed.detail }, 400)

  const serviceClient = createSupabaseServiceClient()
  if (!serviceClient) return noStoreJson({ detail: 'Review ledger configuration unavailable' }, 503)

  const result = await createReviewViaRpc(serviceClient, actor.value, parsed.value)
  if (!result.ok) return noStoreJson({ detail: result.detail }, result.status)

  return noStoreJson({ version: 1, request: result.value }, 200)
}

export async function GET(request: NextRequest) {
  const authz = await verifyRequestAuth(request, { requireAdmin: true })
  if (!authz.ok) return noStoreJson({ detail: authz.detail }, authz.status)

  const actor = validateVerifiedUserId(authz.context.user.id)
  if (!actor.ok) return noStoreJson({ detail: actor.detail }, 503)
  const query = validateListReviewQuery(request.nextUrl)
  if (!query.ok) return noStoreJson({ detail: query.detail }, 400)

  const serviceClient = createSupabaseServiceClient()
  if (!serviceClient) return noStoreJson({ detail: 'Review ledger configuration unavailable' }, 503)

  const result = await listReviewsViaRpc(serviceClient, actor.value, query.value)
  if (!result.ok) return noStoreJson({ detail: result.detail }, result.status)
  return noStoreJson({ version: 1, requests: result.value }, 200)
}
