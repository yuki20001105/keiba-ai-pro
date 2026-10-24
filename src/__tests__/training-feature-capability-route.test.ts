import { NextRequest } from 'next/server'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

const { verifyAuth, fetchMock } = vi.hoisted(() => ({ verifyAuth: vi.fn(), fetchMock: vi.fn() }))
vi.mock('@/lib/backend-url', () => ({ ML_API_URL: 'http://127.0.0.1:8000' }))
vi.mock('@/lib/server-auth', () => ({ verifyRequestAuth: verifyAuth }))

import { GET } from '@/app/api/ml/train/capability/route'

const oldAppEnv = process.env.APP_ENV
const oldTrainingFlag = process.env.MODEL_TRAINING_LOCAL_ENABLED
const oldFetch = global.fetch
const profiles = [
  { pipeline: 'local-db-shared-v1', feature_count: 47 },
  { pipeline: 'local-db-shared-optimized-v1', feature_count: 46 },
  { pipeline: 'local-db-context-v1', feature_count: 80, reference_only: true },
]

beforeEach(() => {
  process.env.APP_ENV = 'development'
  process.env.MODEL_TRAINING_LOCAL_ENABLED = 'true'
  global.fetch = fetchMock
  fetchMock.mockReset()
  verifyAuth.mockReset()
  verifyAuth.mockResolvedValue({ ok: true, context: { token: 'verified-admin' } })
})

afterEach(() => {
  global.fetch = oldFetch
  if (oldAppEnv === undefined) delete process.env.APP_ENV
  else process.env.APP_ENV = oldAppEnv
  if (oldTrainingFlag === undefined) delete process.env.MODEL_TRAINING_LOCAL_ENABLED
  else process.env.MODEL_TRAINING_LOCAL_ENABLED = oldTrainingFlag
})

function request() {
  return new NextRequest('http://localhost/api/ml/train/capability', {
    headers: { Authorization: 'Bearer unverified-browser-token' },
  })
}

async function capability(featureProfiles: unknown, changes: Record<string, unknown> = {}, status = 200) {
  fetchMock.mockResolvedValueOnce(Response.json({ enabled: true, feature_profiles: featureProfiles, ...changes }, { status }))
  return GET(request())
}

describe('local training feature capability whitelist', () => {
  it('forwards three exact profiles to the normal UI, preserving candidate-only status', async () => {
    const response = await capability(profiles, {
      acceleration: { default_device: 'auto', gpu_available: true, feature_cache_supported: true },
    })
    expect(response.status).toBe(200)
    expect(response.headers.get('Cache-Control')).toBe('no-store')
    expect(await response.json()).toEqual({ enabled: true, reason: null, feature_profiles: profiles,
      acceleration: { default_device: 'auto', gpu_available: true, feature_cache_supported: true, gpu_reason: null } })
    expect(verifyAuth).toHaveBeenCalledWith(expect.anything(), { requireAdminMode: true })
    expect(fetchMock).toHaveBeenCalledOnce()
    expect(fetchMock).toHaveBeenCalledWith('http://127.0.0.1:8000/api/train/capability',
      expect.objectContaining({ headers: { Authorization: 'Bearer verified-admin' }, cache: 'no-store', redirect: 'error' }))
    expect(JSON.stringify(fetchMock.mock.calls)).not.toContain('unverified-browser-token')
  })

  it('strips paths, probe details and injected authority fields, and de-duplicates known profiles', async () => {
    const polluted = profiles.map(profile => ({ ...profile, python: 'C:\\private\\python.exe',
      eligible_for_ev: true, profitability_verified: true, arbitrary_source_url: 'private-source' }))
    const response = await capability([...polluted, polluted[2], { pipeline: 'untrusted', feature_count: 80, reference_only: true }])
    const payload = await response.json()
    expect(payload.feature_profiles).toEqual(profiles)
    expect(JSON.stringify(payload)).not.toMatch(/private|eligible_for_ev|profitability_verified|untrusted/)
  })

  it.each([undefined, null, {}, 'profiles', true, [null, [], 1, 'profile'],
    [{ pipeline: 'local-db-context-v1', feature_count: 80 }],
    [{ pipeline: 'local-db-context-v1', feature_count: 80, reference_only: false }],
    [{ pipeline: 'local-db-context-v1', feature_count: 80, reference_only: 'true' }],
    [{ pipeline: 'local-db-context-v1', feature_count: '80', reference_only: true }],
    [{ pipeline: 'local-db-context-v1', feature_count: 79, reference_only: true }],
    [{ pipeline: 'local-db-shared-v1', feature_count: 46 }],
    [{ pipeline: 'local-db-shared-optimized-v1', feature_count: 47 }],
    [{ pipeline: '__proto__', feature_count: 80, reference_only: true }],
    Array(33).fill(profiles[2]),
  ])('does not advertise malformed or unknown profile data: %j', async value => {
    const response = await capability(value)
    expect(await response.json()).toEqual({ enabled: true, reason: null })
  })

  it('retains valid old profiles while discarding an invalid new candidate', async () => {
    const response = await capability([profiles[0], { ...profiles[2], reference_only: false }, profiles[1]])
    expect((await response.json()).feature_profiles).toEqual(profiles.slice(0, 2))
  })

  it.each([{ enabled: false }, { enabled: 'true' }])('cannot enable profiles from a disabled or non-boolean backend capability: %j', async changes => {
    const response = await capability(profiles, changes)
    expect(await response.json()).toEqual({ enabled: false, reason: 'backend-unavailable' })
  })

  it('does not advertise supported profiles from an unsuccessful backend response', async () => {
    const response = await capability(profiles, { enabled: true, reason: 'disabled' }, 409)
    expect(response.status).toBe(409)
    expect(await response.json()).toEqual({ enabled: false, reason: 'disabled' })
  })

  it('does not read backend capabilities before administrator verification', async () => {
    verifyAuth.mockResolvedValue({ ok: false, status: 403, detail: 'admin-required' })
    const response = await GET(request())
    expect(response.status).toBe(403)
    expect(fetchMock).not.toHaveBeenCalled()
  })
})
