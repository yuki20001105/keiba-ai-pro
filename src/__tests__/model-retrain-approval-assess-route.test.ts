import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { readFileSync } from 'node:fs'
import path from 'node:path'
import {
  canonicalRetrainPayloadHash,
  computeFeatureContractHash,
} from '@/lib/model-retrain-approval-contract'
import type { RetrainDryRunPayload } from '@/lib/model-retrain-approval-types'

const ACTOR = '11111111-1111-4111-8111-111111111111'
const APPROVER = '22222222-2222-4222-8222-222222222222'
const COMMIT = 'a'.repeat(40)
const ACTIVE_MODEL = 'model_speed_deviation_lightgbm_20160101_20260322_20260418_1928'
const authMock = vi.hoisted(() => vi.fn())

vi.mock('@/lib/server-auth', () => ({ verifyRequestAuth: authMock }))

import { POST } from '@/app/api/model-redesign/approval/assess/route'

function fixture() {
  const now = Date.now()
  const selected = ['horse_age', 'jockey_win_rate']
  const payload: RetrainDryRunPayload = {
    dry_run_id: '33333333-3333-4333-8333-333333333333',
    generated_at: new Date(now - 5 * 60_000).toISOString(),
    target: 'win',
    model_type: 'lightgbm',
    train_period: { start: '20240101', end: '20251231' },
    validation_period: { start: '20260101', end: '20260731' },
    feature_count: selected.length,
    selected_features: selected,
    removed_features: [],
    expected_outputs: [
      'model-artifact',
      'model-metadata',
      'acceptance-observations',
      'evaluation-report',
      'comparison-report',
    ],
    estimated_runtime: { unit: 'minute', min: 8, max: 25, note: 'bounded staging estimate' },
    safety_checks: [
      { key: 'future_field_exclusion', status: 'pass', note: 'canonical blocklist checked' },
      { key: 'out_of_time_split', status: 'pass', note: 'validation follows training' },
      { key: 'active_model_immutable', status: 'pass', note: 'pointer unchanged' },
      { key: 'production_write_blocked', status: 'pass', note: 'base writes disabled' },
      { key: 'path_input_rejected', status: 'pass', note: 'caller paths unavailable' },
    ],
    source_model_id: ACTIVE_MODEL,
    active_model_id: ACTIVE_MODEL,
    feature_contract_hash: computeFeatureContractHash({
      target: 'win',
      model_type: 'lightgbm',
      selected_features: selected,
      removed_features: [],
    })!,
    data_snapshot_id: 'c'.repeat(64),
    code_version: '0.1.0',
    git_commit: COMMIT,
    created_by: ACTOR,
    state: 'preview-ready',
    warnings: [],
    notes: ['assessment only'],
  }
  return {
    dry_run_payload: payload,
    approval_record: {
      approval_id: '44444444-4444-4444-8444-444444444444',
      dry_run_id: payload.dry_run_id,
      approved_by: APPROVER,
      approved_at: new Date(now - 3 * 60_000).toISOString(),
      approval_status: 'approved',
      approval_comment: 'Approved for isolated staging evaluation only.',
      approved_payload_hash: canonicalRetrainPayloadHash(payload),
      requested_by: ACTOR,
      requested_at: new Date(now - 4 * 60_000).toISOString(),
      expires_at: new Date(now + 60 * 60_000).toISOString(),
      invalidation_reason: null,
      execution_policy: 'staging-train',
      allowed_actions: ['submit_approved_retrain', 'view_approval_status', 'view_job_status'],
    },
  }
}

function request(body: unknown): Request {
  return new Request('http://localhost/api/model-redesign/approval/assess', {
    method: 'POST',
    headers: {
      Authorization: 'Bearer test-token',
      'Content-Type': 'application/json',
    },
    body: JSON.stringify(body),
  })
}

describe('model retrain approval assessment route', () => {
  beforeEach(() => {
    process.env.APP_COMMIT_SHA = COMMIT
    process.env.MODEL_RETRAIN_ARTIFACT_WRITE_POLICY = 'staging-train'
    delete process.env.MODEL_RETRAIN_PRODUCTION_WRITE_ENABLED
    authMock.mockResolvedValue({
      ok: true,
      context: {
        user: { id: ACTOR },
        token: 'test-token',
        profile: { role: 'admin', subscription_tier: 'premium' },
      },
    })
  })

  afterEach(() => {
    vi.clearAllMocks()
    delete process.env.APP_COMMIT_SHA
    delete process.env.MODEL_RETRAIN_ARTIFACT_WRITE_POLICY
    delete process.env.MODEL_RETRAIN_PRODUCTION_WRITE_ENABLED
  })

  it('assesses an exact approved payload but never executes a job or switch', async () => {
    const result = await POST(request(fixture()))
    const body = await result.json()

    expect(result.status).toBe(200)
    expect(result.headers.get('Cache-Control')).toBe('no-store')
    expect(body.execution_eligible).toBe(true)
    expect(body.execution_performed).toBe(false)
    expect(body.failure_codes).toEqual([])
    expect(body.guard).toEqual({
      assessment_only: true,
      job_submission: 'not-implemented',
      model_artifact_written: false,
      active_model_switched: false,
      production_write: false,
    })
  })

  it('fails closed when the protected runtime policy is not staging-train', async () => {
    process.env.MODEL_RETRAIN_ARTIFACT_WRITE_POLICY = 'disabled'
    const result = await POST(request(fixture()))
    const body = await result.json()

    expect(body.execution_eligible).toBe(false)
    expect(body.failure_codes).toContain('artifact-write-policy-invalid')
    expect(body.execution_performed).toBe(false)
  })

  it('detects payload mutation without reflecting raw payload data', async () => {
    const input = fixture()
    input.dry_run_payload.data_snapshot_id = 'd'.repeat(64)
    const result = await POST(request(input))
    const body = await result.json()
    const serialized = JSON.stringify(body)

    expect(body.execution_eligible).toBe(false)
    expect(body.failure_codes).toContain('approved-payload-hash-mismatch')
    expect(serialized).not.toContain('assessment only')
    expect(serialized).not.toContain('horse_age')
  })

  it('rejects unknown request fields and malformed JSON', async () => {
    const unknown = await POST(request({ ...fixture(), modelPath: '../model.joblib' }))
    expect(unknown.status).toBe(400)
    expect((await unknown.json()).code).toBe('request-schema-invalid')

    const malformed = await POST(new Request('http://localhost/api/model-redesign/approval/assess', {
      method: 'POST',
      headers: { Authorization: 'Bearer test-token' },
      body: '{',
    }))
    expect(malformed.status).toBe(400)
    expect((await malformed.json()).code).toBe('invalid-json')
  })

  it('returns the centralized Admin authorization failure before parsing input', async () => {
    authMock.mockResolvedValue({ ok: false, status: 403, detail: 'Admin role required' })
    const result = await POST(request(fixture()))
    const body = await result.json()

    expect(result.status).toBe(403)
    expect(body.code).toBe('authorization-failed')
  })

  it('has no job, process, network, artifact-write, or active-pointer mutation primitive', () => {
    const source = readFileSync(
      path.join(process.cwd(), 'src', 'app', 'api', 'model-redesign', 'approval', 'assess', 'route.ts'),
      'utf8',
    )
    for (const forbidden of [
      'child_process',
      'spawn(',
      'exec(',
      'fetch(',
      'writeFile',
      'appendFile',
      'rename(',
      'unlink(',
    ]) {
      expect(source).not.toContain(forbidden)
    }
  })
})
