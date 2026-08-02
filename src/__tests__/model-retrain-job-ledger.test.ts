import { beforeEach, describe, expect, it, vi } from 'vitest'
import {
  createModelRetrainJobViaRpc,
  getModelRetrainJobViaRpc,
  validateCreateModelRetrainJobBody,
} from '@/lib/model-retrain-job-ledger'

const ACTOR = '11111111-1111-4111-8111-111111111111'
const APPROVER = '22222222-2222-4222-8222-222222222222'
const DRY_RUN = '33333333-3333-4333-8333-333333333333'
const APPROVAL = '44444444-4444-4444-8444-444444444444'
const JOB = '55555555-5555-4555-8555-555555555555'
const HASH = 'a'.repeat(64)
const authMock = vi.hoisted(() => vi.fn())
const serviceClientMock = vi.hoisted(() => vi.fn())
const rpcMock = vi.hoisted(() => vi.fn())

vi.mock('@/lib/server-auth', () => ({
  verifyRequestAuth: authMock,
  createSupabaseServiceClient: serviceClientMock,
}))

import { POST as createJobRoute } from '@/app/api/model-redesign/jobs/route'
import { GET as getJobRoute } from '@/app/api/model-redesign/jobs/[job_id]/route'

function queuedRecord(overrides: Record<string, unknown> = {}) {
  return {
    job_id: JOB,
    approval_id: APPROVAL,
    dry_run_id: DRY_RUN,
    approved_payload_hash: HASH,
    submitted_by: ACTOR,
    requested_by: ACTOR,
    approved_by: APPROVER,
    execution_policy: 'staging-train',
    job_state: 'queued',
    submitted_at: new Date().toISOString(),
    record_version: 1,
    authoritative_record: true,
    execution_started: false,
    artifact_written: false,
    artifact_uri: null,
    artifact_sha256: null,
    artifact_size_bytes: null,
    artifact_media_type: null,
    artifact_registered_at: null,
    worker_id: null,
    fencing_token: null,
    lease_expires_at: null,
    claimed_at: null,
    started_at: null,
    finished_at: null,
    failure_code: null,
    ...overrides,
  }
}

function request(body: unknown): Request {
  return new Request('http://localhost/api/model-redesign/jobs', {
    method: 'POST',
    headers: { Authorization: 'Bearer token', 'Content-Type': 'application/json' },
    body: JSON.stringify(body),
  })
}

describe('approval-bound model retrain job ledger', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    authMock.mockResolvedValue({
      ok: true,
      context: { user: { id: ACTOR }, profile: { role: 'admin' } },
    })
    serviceClientMock.mockReturnValue({ rpc: rpcMock })
  })

  it('strictly validates approval id, CAS version, hash and unknown keys', () => {
    const valid = { approval_id: APPROVAL, expected_approval_version: 2, approved_payload_hash: HASH }
    expect(validateCreateModelRetrainJobBody(valid).ok).toBe(true)
    expect(validateCreateModelRetrainJobBody({ ...valid, extra: true }).ok).toBe(false)
    expect(validateCreateModelRetrainJobBody({ ...valid, approval_id: 'bad' }).ok).toBe(false)
    expect(validateCreateModelRetrainJobBody({ ...valid, expected_approval_version: 0 }).ok).toBe(false)
    expect(validateCreateModelRetrainJobBody({ ...valid, approved_payload_hash: 'a' }).ok).toBe(false)
  })

  it('submits an actor/hash/version-bound RPC and accepts only a queued non-executing record', async () => {
    rpcMock.mockResolvedValue({ data: [queuedRecord()], error: null })
    const result = await createModelRetrainJobViaRpc({ rpc: rpcMock } as never, ACTOR, {
      approval_id: APPROVAL,
      expected_approval_version: 2,
      approved_payload_hash: HASH,
    })
    expect(result.ok).toBe(true)
    expect(rpcMock).toHaveBeenCalledWith('create_model_retrain_job', {
      p_actor_user_id: ACTOR,
      p_approval_id: APPROVAL,
      p_expected_approval_version: 2,
      p_approved_payload_hash: HASH,
    })
    if (result.ok) {
      expect(result.value.job_state).toBe('queued')
      expect(result.value.execution_started).toBe(false)
      expect(result.value.artifact_written).toBe(false)
    }
  })

  it('rejects backend records that claim execution, artifacts or broken actor binding', async () => {
    for (const record of [
      queuedRecord({ execution_started: true }),
      queuedRecord({ artifact_written: true, artifact_uri: 'models/x' }),
      queuedRecord({ submitted_by: APPROVER }),
    ]) {
      rpcMock.mockResolvedValueOnce({ data: [record], error: null })
      const result = await getModelRetrainJobViaRpc({ rpc: rpcMock } as never, ACTOR, JOB)
      expect(result).toEqual(expect.objectContaining({ ok: false, status: 502 }))
    }
  })

  it('projects a fenced claimed job without treating it as artifact execution', async () => {
    const now = new Date().toISOString()
    rpcMock.mockResolvedValue({ data: [queuedRecord({
      job_state: 'claimed',
      record_version: 2,
      worker_id: 'staging-worker-01',
      fencing_token: 7,
      lease_expires_at: now,
      claimed_at: now,
    })], error: null })
    const result = await getModelRetrainJobViaRpc({ rpc: rpcMock } as never, ACTOR, JOB)
    expect(result.ok).toBe(true)
    if (result.ok) {
      expect(result.value.job_state).toBe('claimed')
      expect(result.value.fencing_token).toBe(7)
      expect(result.value.artifact_written).toBe(false)
    }
  })

  it('projects an immutable private-storage artifact identity without implying evaluation', async () => {
    const now = new Date().toISOString()
    const artifactSha256 = 'c'.repeat(64)
    rpcMock.mockResolvedValue({ data: [queuedRecord({
      job_state: 'artifact-registered',
      record_version: 5,
      execution_started: true,
      worker_id: 'staging-worker-01',
      fencing_token: 7,
      lease_expires_at: now,
      claimed_at: now,
      started_at: now,
      finished_at: now,
      artifact_written: true,
      artifact_uri: `models://retrain/${JOB}/${artifactSha256}.joblib`,
      artifact_sha256: artifactSha256,
      artifact_size_bytes: 4096,
      artifact_media_type: 'application/x-python-serialized-object',
      artifact_registered_at: now,
    })], error: null })
    const result = await getModelRetrainJobViaRpc({ rpc: rpcMock } as never, ACTOR, JOB)
    expect(result.ok).toBe(true)
    if (result.ok) {
      expect(result.value.job_state).toBe('artifact-registered')
      expect(result.value.artifact_written).toBe(true)
      expect(result.value.artifact_sha256).toBe(artifactSha256)
    }
  })

  it('sanitizes approval-state conflicts', async () => {
    rpcMock.mockResolvedValue({
      data: null,
      error: { code: '55000', message: 'not eligible secret backend detail' },
    })
    const result = await createModelRetrainJobViaRpc({ rpc: rpcMock } as never, ACTOR, {
      approval_id: APPROVAL,
      expected_approval_version: 2,
      approved_payload_hash: HASH,
    })
    expect(result).toEqual({
      ok: false,
      status: 409,
      detail: 'model retrain job conflicts with approval state',
    })
  })

  it('authorizes before parsing and queues without claiming execution', async () => {
    authMock.mockResolvedValueOnce({ ok: false, status: 403, detail: 'Admin role required' })
    const denied = await createJobRoute(request({ malformed: true }))
    expect(denied.status).toBe(403)
    expect(serviceClientMock).not.toHaveBeenCalled()

    authMock.mockResolvedValueOnce({
      ok: true,
      context: { user: { id: ACTOR }, profile: { role: 'admin' } },
    })
    rpcMock.mockResolvedValueOnce({ data: [queuedRecord()], error: null })
    const response = await createJobRoute(request({
      approval_id: APPROVAL,
      expected_approval_version: 2,
      approved_payload_hash: HASH,
    }))
    const body = await response.json()
    expect(response.status).toBe(201)
    expect(body.code).toBe('approval-bound-job-queued')
    expect(body.guard).toEqual({
      durable_job_created: true,
      execution_started: false,
      model_artifact_written: false,
      active_model_switched: false,
    })
  })

  it('rejects an invalid job id before reading the backend', async () => {
    const response = await getJobRoute(
      new Request('http://localhost/api/model-redesign/jobs/not-a-uuid'),
      { params: Promise.resolve({ job_id: 'not-a-uuid' }) },
    )
    expect(response.status).toBe(400)
    expect(rpcMock).not.toHaveBeenCalled()
  })
})
