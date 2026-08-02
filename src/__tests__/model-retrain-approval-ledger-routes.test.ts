import { beforeEach, describe, expect, it, vi } from 'vitest'
import {
  canonicalRetrainPayloadHash,
  computeFeatureContractHash,
} from '@/lib/model-retrain-approval-contract'
import type { RetrainDryRunPayload } from '@/lib/model-retrain-approval-types'

const ACTOR = '11111111-1111-4111-8111-111111111111'
const APPROVER = '22222222-2222-4222-8222-222222222222'
const DRY_RUN = '33333333-3333-4333-8333-333333333333'
const APPROVAL = '44444444-4444-4444-8444-444444444444'
const TEST_NOW = Date.now()
const GENERATED_AT = new Date(TEST_NOW - 5 * 60_000).toISOString()
const authMock = vi.hoisted(() => vi.fn())
const serviceClientMock = vi.hoisted(() => vi.fn())
const rpcMock = vi.hoisted(() => vi.fn())

vi.mock('@/lib/server-auth', () => ({
  verifyRequestAuth: authMock,
  createSupabaseServiceClient: serviceClientMock,
}))

import { POST as createApproval } from '@/app/api/model-redesign/approval/route'
import { GET as getApproval } from '@/app/api/model-redesign/approval/[approval_id]/route'
import { POST as decideApproval } from '@/app/api/model-redesign/approval/[approval_id]/decision/route'

function dryRun(): RetrainDryRunPayload {
  const selectedFeatures = ['horse_age', 'jockey_win_rate']
  return {
    dry_run_id: DRY_RUN,
    generated_at: GENERATED_AT,
    target: 'win',
    model_type: 'lightgbm',
    train_period: { start: '20240101', end: '20251231' },
    validation_period: { start: '20260101', end: '20260731' },
    feature_count: selectedFeatures.length,
    selected_features: selectedFeatures,
    removed_features: [],
    expected_outputs: [
      'model-artifact', 'model-metadata', 'acceptance-observations',
      'evaluation-report', 'comparison-report',
    ],
    estimated_runtime: { unit: 'minute', min: 8, max: 25, note: 'bounded staging estimate' },
    safety_checks: [
      { key: 'future_field_exclusion', status: 'pass', note: 'canonical blocklist checked' },
      { key: 'out_of_time_split', status: 'pass', note: 'validation follows training' },
      { key: 'active_model_immutable', status: 'pass', note: 'pointer unchanged' },
      { key: 'production_write_blocked', status: 'pass', note: 'base writes disabled' },
      { key: 'path_input_rejected', status: 'pass', note: 'caller paths unavailable' },
    ],
    source_model_id: 'model-source-v1',
    active_model_id: 'model-active-v1',
    feature_contract_hash: computeFeatureContractHash({
      target: 'win', model_type: 'lightgbm', selected_features: selectedFeatures, removed_features: [],
    })!,
    data_snapshot_id: 'c'.repeat(64),
    code_version: 'keiba-ai-pro-v1',
    git_commit: 'a'.repeat(40),
    created_by: ACTOR,
    state: 'preview-ready',
    warnings: [],
    notes: ['assessment only'],
  }
}

function record(status: 'pending' | 'approved' = 'pending', version = 1) {
  const payload = dryRun()
  const now = TEST_NOW
  return {
    approval_id: APPROVAL,
    dry_run_id: DRY_RUN,
    approved_by: status === 'approved' ? APPROVER : null,
    approved_at: status === 'approved' ? new Date(now - 60_000).toISOString() : null,
    approval_status: status,
    approval_comment: status === 'approved'
      ? 'Independent reviewer approved isolated staging training.'
      : '',
    approved_payload_hash: canonicalRetrainPayloadHash(payload),
    requested_by: ACTOR,
    requested_at: new Date(now - 2 * 60_000).toISOString(),
    expires_at: new Date(now + 28 * 60_000).toISOString(),
    invalidation_reason: null,
    execution_policy: 'staging-train',
    allowed_actions: ['submit_approved_retrain', 'view_approval_status', 'view_job_status'],
    dry_run_payload: payload,
    record_version: version,
    authoritative_record: true,
    execution_enabled: false,
    job_created: false,
  }
}

function jsonRequest(url: string, body: unknown): Request {
  return new Request(url, {
    method: 'POST',
    headers: { Authorization: 'Bearer token', 'Content-Type': 'application/json' },
    body: JSON.stringify(body),
  })
}

describe('model retrain approval ledger routes', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    authMock.mockResolvedValue({
      ok: true,
      context: { user: { id: ACTOR }, profile: { role: 'admin' } },
    })
    serviceClientMock.mockReturnValue({ rpc: rpcMock })
  })

  it('rejects authorization before parsing or contacting the ledger', async () => {
    authMock.mockResolvedValue({ ok: false, status: 403, detail: 'Admin role required' })
    const response = await createApproval(jsonRequest(
      'http://localhost/api/model-redesign/approval',
      { malformed: true },
    ))
    expect(response.status).toBe(403)
    expect((await response.json()).code).toBe('authorization-failed')
    expect(serviceClientMock).not.toHaveBeenCalled()
  })

  it('creates only a durable pending non-executing approval record', async () => {
    const payload = dryRun()
    rpcMock.mockResolvedValue({ data: [record()], error: null })
    const response = await createApproval(jsonRequest(
      'http://localhost/api/model-redesign/approval',
      {
        dry_run_payload: payload,
        execution_policy: 'staging-train',
        allowed_actions: ['submit_approved_retrain', 'view_approval_status', 'view_job_status'],
      },
    ))
    const body = await response.json()
    expect(response.status).toBe(201)
    expect(body.code).toBe('approval-pending')
    expect(body.guard).toEqual(expect.objectContaining({
      durable_record_created: true,
      execution_enabled: false,
      job_created: false,
      model_artifact_written: false,
      active_model_switched: false,
    }))
  })

  it('validates approval ids and decisions before RPC access', async () => {
    const getResponse = await getApproval(
      new Request('http://localhost/api/model-redesign/approval/not-a-uuid'),
      { params: Promise.resolve({ approval_id: 'not-a-uuid' }) },
    )
    expect(getResponse.status).toBe(400)

    const decisionResponse = await decideApproval(
      jsonRequest('http://localhost/api/model-redesign/approval/not-a-uuid/decision', {
        action: 'approve', expected_version: 1, reason: 'Independent approval reason is long enough.',
      }),
      { params: Promise.resolve({ approval_id: 'not-a-uuid' }) },
    )
    expect(decisionResponse.status).toBe(400)
    expect(rpcMock).not.toHaveBeenCalled()
  })

  it('returns an approved record without enabling a job or artifact writer', async () => {
    rpcMock.mockResolvedValue({ data: [record('approved', 2)], error: null })
    const response = await decideApproval(
      jsonRequest(`http://localhost/api/model-redesign/approval/${APPROVAL}/decision`, {
        action: 'approve',
        expected_version: 1,
        reason: 'Independent reviewer approved isolated staging training.',
      }),
      { params: Promise.resolve({ approval_id: APPROVAL }) },
    )
    const body = await response.json()
    expect(response.status).toBe(200)
    expect(body.code).toBe('approval-approved')
    expect(body.guard).toEqual({
      execution_enabled: false,
      job_created: false,
      model_artifact_written: false,
      active_model_switched: false,
    })
  })
})
