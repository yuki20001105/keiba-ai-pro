import { NextRequest, NextResponse } from 'next/server'
import { randomUUID } from 'node:crypto'
import { createSupabaseServiceClient, verifyRequestAuth } from '@/lib/server-auth'
import {
  projectAdminRoleRpcResult,
  validateAdminTargetUserId,
  validateRoleChangeBody,
} from '../../_contract'

export const runtime = 'nodejs'

const MAX_BODY_BYTES = 256

function noStoreJson(body: unknown, status: number): NextResponse {
  return NextResponse.json(body, {
    status,
    headers: { 'Cache-Control': 'no-store' },
  })
}

function rpcErrorResponse(error: unknown): NextResponse {
  const code = typeof error === 'object' && error !== null && 'code' in error
    ? String((error as { code?: unknown }).code ?? '')
    : ''
  if (code === '42501') return noStoreJson({ detail: 'Admin role required' }, 403)
  if (code === 'P0002') return noStoreJson({ detail: 'Profile not found' }, 404)
  if (code === 'P0001') return noStoreJson({ detail: 'At least one administrator is required' }, 409)
  return noStoreJson({ detail: 'Admin profile service unavailable' }, 503)
}

async function readRoleBody(request: NextRequest): Promise<
  | { ok: true; value: unknown }
  | { ok: false; status: 400 | 413; detail: string }
> {
  const contentLength = request.headers.get('Content-Length')
  if (contentLength) {
    const parsed = Number(contentLength)
    if (!Number.isSafeInteger(parsed) || parsed < 0) {
      return { ok: false, status: 400, detail: 'Invalid Content-Length' }
    }
    if (parsed > MAX_BODY_BYTES) return { ok: false, status: 413, detail: 'Request body is too large' }
  }

  let raw = ''
  try {
    raw = await request.text()
  } catch {
    return { ok: false, status: 400, detail: 'Invalid JSON body' }
  }
  if (new TextEncoder().encode(raw).byteLength > MAX_BODY_BYTES) {
    return { ok: false, status: 413, detail: 'Request body is too large' }
  }
  try {
    return { ok: true, value: JSON.parse(raw) }
  } catch {
    return { ok: false, status: 400, detail: 'Invalid JSON body' }
  }
}

export async function PATCH(
  request: NextRequest,
  { params }: { params: Promise<{ userId: string }> },
) {
  const authz = await verifyRequestAuth(request, { requireAdmin: true })
  if (!authz.ok) return noStoreJson({ detail: authz.detail }, authz.status)

  const target = validateAdminTargetUserId((await params).userId)
  if (!target.ok) return noStoreJson({ detail: target.detail }, 400)

  const actor = validateAdminTargetUserId(authz.context.user.id)
  if (!actor.ok) return noStoreJson({ detail: 'Authorization backend unavailable' }, 503)

  const body = await readRoleBody(request)
  if (!body.ok) return noStoreJson({ detail: body.detail }, body.status)
  const change = validateRoleChangeBody(body.value)
  if (!change.ok) return noStoreJson({ detail: change.detail }, 400)

  const serviceClient = createSupabaseServiceClient()
  if (!serviceClient) return noStoreJson({ detail: 'Admin profile service unavailable' }, 503)

  try {
    const requestId = randomUUID()
    const { data, error } = await serviceClient.rpc('update_admin_profile_role', {
      p_actor_user_id: actor.value,
      p_target_user_id: target.value,
      p_role: change.value.role,
      p_request_id: requestId,
    })

    if (error) return rpcErrorResponse(error)
    const updated = projectAdminRoleRpcResult(data, {
      id: target.value,
      role: change.value.role,
      requestId,
    })
    if (!updated.ok) return noStoreJson({ detail: updated.detail }, 502)

    return noStoreJson({ version: 1, profile: updated.value }, 200)
  } catch {
    return noStoreJson({ detail: 'Admin profile service unavailable' }, 503)
  }
}
