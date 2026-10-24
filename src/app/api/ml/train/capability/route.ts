import { NextRequest, NextResponse } from 'next/server'
import { ML_API_URL } from '@/lib/backend-url'
import { explicitLocalOptInEnabled, isLoopbackRequest, isLoopbackUrl } from '@/lib/legacy-local-policy'
import { verifyRequestAuth } from '@/lib/server-auth'

function noStoreJson(body: unknown, status: number) {
  return NextResponse.json(body, {
    status,
    headers: { 'Cache-Control': 'no-store' },
  })
}

function safeAcceleration(value: unknown) {
  if (typeof value !== 'object' || value === null || Array.isArray(value)) return null
  const record = value as Record<string, unknown>
  if (record.default_device !== 'auto'
    || typeof record.gpu_available !== 'boolean'
    || typeof record.feature_cache_supported !== 'boolean') return null
  // Do not expose interpreter paths or arbitrary runtime/probe output.
  const reason = record.gpu_reason
  const safeReason = typeof reason === 'string'
    && (reason === 'GPU環境を確認できません' || /^[a-z0-9][a-z0-9-]{0,79}$/i.test(reason))
    ? reason : null
  return {
    default_device: 'auto',
    gpu_available: record.gpu_available,
    feature_cache_supported: record.feature_cache_supported,
    gpu_reason: record.gpu_available ? null : safeReason,
  }
}

function safeFeatureProfiles(value: unknown) {
  if (!Array.isArray(value) || value.length > 32) return []
  const expectedCounts = new Map([
    ['local-db-shared-v1', 47],
    ['local-db-shared-optimized-v1', 46],
    ['local-db-context-v1', 80],
  ])
  const seen = new Set<string>()
  const profiles: { pipeline: string; feature_count: number; reference_only?: true }[] = []
  for (const candidate of value) {
    if (typeof candidate !== 'object' || candidate === null || Array.isArray(candidate)) continue
    const record = candidate as Record<string, unknown>
    const pipeline = record.pipeline
    if (typeof pipeline !== 'string' || !expectedCounts.has(pipeline) || seen.has(pipeline)
      || typeof record.feature_count !== 'number' || record.feature_count !== expectedCounts.get(pipeline)) continue
    // The new profile may advertise input availability, never live validation
    // or purchase eligibility. Missing/contradictory reference status is closed.
    if (pipeline === 'local-db-context-v1' && record.reference_only !== true) continue
    seen.add(pipeline)
    profiles.push(pipeline === 'local-db-context-v1'
      ? { pipeline, feature_count: 80, reference_only: true }
      : { pipeline, feature_count: record.feature_count })
  }
  return profiles
}

export async function GET(request: NextRequest) {
  if (!isLoopbackRequest(request)) {
    return noStoreJson({ enabled: false, reason: 'loopback-required' }, 403)
  }
  if (!explicitLocalOptInEnabled('MODEL_TRAINING_LOCAL_ENABLED')) {
    return noStoreJson({ enabled: false, reason: 'local-training-disabled' }, 409)
  }
  if (!isLoopbackUrl(ML_API_URL)) {
    return noStoreJson({ enabled: false, reason: 'backend-not-local' }, 409)
  }

  const authz = await verifyRequestAuth(request, { requireAdminMode: true })
  if (!authz.ok) {
    return noStoreJson({ enabled: false, reason: authz.detail }, authz.status)
  }

  try {
    const response = await fetch(`${ML_API_URL}/api/train/capability`, {
      headers: { Authorization: `Bearer ${authz.context.token}` },
      cache: 'no-store',
      signal: AbortSignal.timeout(5_000),
      redirect: 'error',
    })
    const payload: unknown = await response.json()
    const record = typeof payload === 'object' && payload !== null && !Array.isArray(payload)
      ? payload as Record<string, unknown>
      : null
    const enabled = response.ok && record?.enabled === true
    const acceleration = enabled ? safeAcceleration(record?.acceleration) : null
    const featureProfiles = enabled ? safeFeatureProfiles(record?.feature_profiles) : []

    return noStoreJson({
      enabled,
      reason: enabled ? null : (typeof record?.reason === 'string' ? record.reason : 'backend-unavailable'),
      ...(acceleration ? { acceleration } : {}),
      ...(featureProfiles.length ? { feature_profiles: featureProfiles } : {}),
    }, response.ok ? 200 : response.status)
  } catch {
    return noStoreJson({ enabled: false, reason: 'backend-unavailable' }, 503)
  }
}
