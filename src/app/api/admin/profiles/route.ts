import { NextRequest, NextResponse } from 'next/server'
import { createSupabaseServiceClient, verifyRequestAuth } from '@/lib/server-auth'
import { projectAdminProfiles } from './_contract'

export const runtime = 'nodejs'

const PROFILE_PROJECTION = 'id, email, role, full_name, subscription_tier, created_at'

function noStoreJson(body: unknown, status: number): NextResponse {
  return NextResponse.json(body, {
    status,
    headers: { 'Cache-Control': 'no-store' },
  })
}

export async function GET(request: NextRequest) {
  const authz = await verifyRequestAuth(request, { requireAdmin: true })
  if (!authz.ok) return noStoreJson({ detail: authz.detail }, authz.status)

  const serviceClient = createSupabaseServiceClient()
  if (!serviceClient) return noStoreJson({ detail: 'Admin profile service unavailable' }, 503)

  try {
    const { data, error } = await serviceClient
      .from('profiles')
      .select(PROFILE_PROJECTION)
      .order('created_at', { ascending: false })
      .limit(500)

    if (error) return noStoreJson({ detail: 'Admin profile service unavailable' }, 503)
    const profiles = projectAdminProfiles(data)
    if (!profiles.ok) return noStoreJson({ detail: profiles.detail }, 502)

    return noStoreJson({ version: 1, profiles: profiles.value }, 200)
  } catch {
    return noStoreJson({ detail: 'Admin profile service unavailable' }, 503)
  }
}
