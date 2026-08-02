import { describe, expect, it, vi } from 'vitest'
import {
  canonicalRetrainPayloadHash,
  computeFeatureContractHash,
} from '@/lib/model-retrain-approval-contract'
import {
  createModelRetrainApprovalViaRpc,
  getModelRetrainApprovalViaRpc,
  transitionModelRetrainApprovalViaRpc,
  validateCreateModelRetrainApprovalBody,
  validateModelRetrainApprovalDecisionBody,
} from '@/lib/model-retrain-approval-ledger'
import type { RetrainDryRunPayload } from '@/lib/model-retrain-approval-types'

const ACTOR = '11111111-1111-4111-8111-111111111111'
const APPROVER = '22222222-2222-4222-8222-222222222222'
const DRY_RUN = '33333333-3333-4333-8333-333333333333'
const APPROVAL = '44444444-4444-4444-8444-444444444444'
const TEST_NOW = Date.now()
const GENERATED_AT = new Date(TEST_NOW - 5 * 60_000).toISOString()
const REQUESTED_AT = new Date(TEST_NOW - 4 * 60_000).toISOString()
const EXPIRES_AT = new Date(TEST_NOW + 26 * 60_000).toISOString()

function payload(): RetrainDryRunPayload {
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
    source_model_id: 'model-source-v1',
    active_model_id: 'model-active-v1',
    feature_contract_hash: computeFeatureContractHash({
      target: 'win',
      model_type: 'lightgbm',
      selected_features: selectedFeatures,
      removed_features: [],
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

function pendingRecord(overrides: Record<string, unknown> = {}) {
  const dryRun = payload()
  return {
    approval_id: APPROVAL,
    dry_run_id: DRY_RUN,
    approved_by: null,
    approved_at: null,
    approval_status: 'pending',
    approval_comment: '',
    approved_payload_hash: canonicalRetrainPayloadHash(dryRun),
    requested_by: ACTOR,
    requested_at: REQUESTED_AT,
    expires_at: EXPIRES_AT,
    invalidation_reason: null,
    execution_policy: 'staging-train',
    allowed_actions: ['submit_approved_retrain', 'view_approval_status', 'view_job_status'],
    dry_run_payload: dryRun,
    record_version: 1,
    authoritative_record: true,
    execution_enabled: false,
    job_created: false,
    ...overrides,
  }
}

describe('model retrain approval ledger boundary', () => {
  it('accepts only an actor-bound preview-ready request with policy-matched actions', () => {
    const input = {
      dry_run_payload: payload(),
      execution_policy: 'staging-train',
      allowed_actions: ['view_job_status', 'submit_approved_retrain', 'view_approval_status'],
    }
    const parsed = validateCreateModelRetrainApprovalBody(input, ACTOR)
    expect(parsed.ok).toBe(true)
    if (parsed.ok) {
      expect(parsed.value.allowed_actions).toEqual([
        'submit_approved_retrain',
        'view_approval_status',
        'view_job_status',
      ])
    }
  })

  it('rejects requester substitution, failed previews, unknown keys and policy/action mismatch', () => {
    const base = {
      dry_run_payload: payload(),
      execution_policy: 'staging-train',
      allowed_actions: ['submit_approved_retrain', 'view_approval_status', 'view_job_status'],
    }
    expect(validateCreateModelRetrainApprovalBody(base, APPROVER).ok).toBe(false)
    expect(validateCreateModelRetrainApprovalBody({ ...base, unexpected: true }, ACTOR).ok).toBe(false)
    expect(validateCreateModelRetrainApprovalBody({
      ...base,
      execution_policy: 'read-only-preview',
    }, ACTOR).ok).toBe(false)
    expect(validateCreateModelRetrainApprovalBody({
      ...base,
      dry_run_payload: { ...payload(), state: 'preview-fail' },
    }, ACTOR).ok).toBe(false)
    expect(validateCreateModelRetrainApprovalBody({
      ...base,
      dry_run_payload: { ...payload(), generated_at: new Date(TEST_NOW - 25 * 60 * 60_000).toISOString() },
    }, ACTOR).ok).toBe(false)
  })

  it('strictly validates CAS decisions and bounded reasons', () => {
    expect(validateModelRetrainApprovalDecisionBody({
      action: 'approve',
      expected_version: 1,
      reason: 'Independent reviewer approved isolated staging training.',
    }).ok).toBe(true)
    expect(validateModelRetrainApprovalDecisionBody({
      action: 'approve', expected_version: 0, reason: 'This reason is sufficiently long.' },
    ).ok).toBe(false)
    expect(validateModelRetrainApprovalDecisionBody({
      action: 'execute', expected_version: 1, reason: 'This reason is sufficiently long.' },
    ).ok).toBe(false)
    expect(validateModelRetrainApprovalDecisionBody({
      action: 'approve', expected_version: 1, reason: 'too short' },
    ).ok).toBe(false)
  })

  it('creates an idempotent RPC request using the server-computed canonical hash', async () => {
    const rpc = vi.fn().mockResolvedValue({ data: [pendingRecord()], error: null })
    const result = await createModelRetrainApprovalViaRpc({ rpc } as never, ACTOR, {
      dry_run_payload: payload(),
      execution_policy: 'staging-train',
      allowed_actions: ['submit_approved_retrain', 'view_approval_status', 'view_job_status'],
    })
    expect(result.ok).toBe(true)
    expect(rpc).toHaveBeenCalledWith('create_model_retrain_approval', expect.objectContaining({
      p_actor_user_id: ACTOR,
      p_dry_run_id: DRY_RUN,
      p_approved_payload_hash: canonicalRetrainPayloadHash(payload()),
    }))
    if (result.ok) {
      expect(result.value.execution_enabled).toBe(false)
      expect(result.value.job_created).toBe(false)
      expect(result.value.approval_record.requested_at).toBe(REQUESTED_AT)
    }
  })

  it('rejects backend records that claim execution or break payload binding', async () => {
    for (const record of [
      pendingRecord({ execution_enabled: true }),
      pendingRecord({ approved_payload_hash: 'd'.repeat(64) }),
    ]) {
      const rpc = vi.fn().mockResolvedValue({ data: [record], error: null })
      const result = await getModelRetrainApprovalViaRpc({ rpc } as never, ACTOR, APPROVAL)
      expect(result).toEqual(expect.objectContaining({ ok: false, status: 502 }))
    }
  })

  it('accepts an authoritative read after a durable job marker is recorded', async () => {
    const rpc = vi.fn().mockResolvedValue({ data: [pendingRecord({
      approved_by: APPROVER,
      approved_at: new Date(TEST_NOW - 3 * 60_000).toISOString(),
      approval_status: 'approved',
      approval_comment: 'Independent reviewer approved isolated staging training.',
      record_version: 3,
      job_created: true,
    })], error: null })
    const result = await getModelRetrainApprovalViaRpc({ rpc } as never, ACTOR, APPROVAL)
    expect(result.ok).toBe(true)
    if (result.ok) expect(result.value.job_created).toBe(true)
  })

  it('requires a correlated version and terminal status after a decision', async () => {
    const approved = pendingRecord({
      approved_by: APPROVER,
      approved_at: new Date(TEST_NOW - 3 * 60_000).toISOString(),
      approval_status: 'approved',
      approval_comment: 'Independent reviewer approved isolated staging training.',
      record_version: 2,
    })
    const rpc = vi.fn().mockResolvedValue({ data: [approved], error: null })
    const result = await transitionModelRetrainApprovalViaRpc(
      { rpc } as never,
      APPROVER,
      APPROVAL,
      {
        action: 'approve',
        expected_version: 1,
        reason: 'Independent reviewer approved isolated staging training.',
      },
    )
    expect(result.ok).toBe(true)

    rpc.mockResolvedValueOnce({ data: [{ ...approved, record_version: 3 }], error: null })
    expect((await transitionModelRetrainApprovalViaRpc(
      { rpc } as never,
      APPROVER,
      APPROVAL,
      {
        action: 'approve',
        expected_version: 1,
        reason: 'Independent reviewer approved isolated staging training.',
      },
    )).ok).toBe(false)
  })

  it('maps database conflicts without leaking backend detail', async () => {
    const rpc = vi.fn().mockResolvedValue({
      data: null,
      error: { code: '42501', message: 'requester cannot decide own approval secret-value' },
    })
    const result = await transitionModelRetrainApprovalViaRpc(
      { rpc } as never,
      ACTOR,
      APPROVAL,
      {
        action: 'approve',
        expected_version: 1,
        reason: 'Independent reviewer approved isolated staging training.',
      },
    )
    expect(result).toEqual({
      ok: false,
      status: 409,
      detail: 'model retrain approval conflicts with current state',
    })
  })
})
