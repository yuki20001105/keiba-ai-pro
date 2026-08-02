import { describe, expect, it } from 'vitest'
import { readFileSync } from 'node:fs'
import path from 'node:path'
import {
  assessApprovedRetrainEligibility,
  canonicalRetrainPayloadHash,
  computeFeatureContractHash,
  MODEL_FUTURE_FIELDS,
  parseRetrainApprovalRecord,
  parseRetrainDryRunPayload,
  type RetrainEligibilityContext,
} from '@/lib/model-retrain-approval-contract'
import type {
  RetrainApprovalRecord,
  RetrainDryRunPayload,
} from '@/lib/model-retrain-approval-types'

const ACTOR = '11111111-1111-4111-8111-111111111111'
const APPROVER = '22222222-2222-4222-8222-222222222222'
const DRY_RUN = '33333333-3333-4333-8333-333333333333'
const APPROVAL = '44444444-4444-4444-8444-444444444444'
const COMMIT = 'a'.repeat(40)
const SNAPSHOT_HASH = 'c'.repeat(64)

const FEATURE_HASH = computeFeatureContractHash({
  target: 'win',
  model_type: 'lightgbm',
  selected_features: ['horse_age', 'jockey_win_rate', 'trainer_win_rate'],
  removed_features: ['trainer_win_rate'],
})!

function payload(): RetrainDryRunPayload {
  return {
    dry_run_id: DRY_RUN,
    generated_at: '2026-08-02T10:00:00.000Z',
    target: 'win',
    model_type: 'lightgbm',
    train_period: { start: '20240101', end: '20251231' },
    validation_period: { start: '20260101', end: '20260731' },
    feature_count: 2,
    selected_features: ['horse_age', 'jockey_win_rate', 'trainer_win_rate'],
    removed_features: ['trainer_win_rate'],
    expected_outputs: [
      'model-artifact',
      'model-metadata',
      'acceptance-observations',
      'evaluation-report',
      'comparison-report',
    ],
    estimated_runtime: {
      unit: 'minute',
      min: 8,
      max: 25,
      note: 'bounded staging estimate',
    },
    safety_checks: [
      { key: 'future_field_exclusion', status: 'pass', note: 'canonical blocklist checked' },
      { key: 'out_of_time_split', status: 'pass', note: 'validation follows training' },
      { key: 'active_model_immutable', status: 'pass', note: 'pointer unchanged' },
      { key: 'production_write_blocked', status: 'pass', note: 'base writes disabled' },
      { key: 'path_input_rejected', status: 'pass', note: 'caller paths unavailable' },
    ],
    source_model_id: 'model-source-v1',
    active_model_id: 'model-active-v1',
    feature_contract_hash: FEATURE_HASH,
    data_snapshot_id: SNAPSHOT_HASH,
    code_version: 'keiba-ai-pro-v1',
    git_commit: COMMIT,
    created_by: ACTOR,
    state: 'preview-ready',
    warnings: [],
    notes: ['assessment only'],
  }
}

function approval(input = payload()): RetrainApprovalRecord {
  const hash = canonicalRetrainPayloadHash(input)
  if (!hash) throw new Error('fixture payload must be valid')
  return {
    approval_id: APPROVAL,
    dry_run_id: DRY_RUN,
    approved_by: APPROVER,
    approved_at: '2026-08-02T10:10:00.000Z',
    approval_status: 'approved',
    approval_comment: 'Approved for isolated staging evaluation only.',
    approved_payload_hash: hash,
    requested_by: ACTOR,
    requested_at: '2026-08-02T10:05:00.000Z',
    expires_at: '2026-08-02T12:00:00.000Z',
    invalidation_reason: null,
    execution_policy: 'staging-train',
    allowed_actions: ['submit_approved_retrain', 'view_approval_status', 'view_job_status'],
  }
}

function context(): RetrainEligibilityContext {
  return {
    now: '2026-08-02T10:30:00.000Z',
    actor_id: ACTOR,
    actor_is_admin: true,
    current_active_model_id: 'model-active-v1',
    current_feature_contract_hash: FEATURE_HASH,
    current_code_version: 'keiba-ai-pro-v1',
    current_git_commit: COMMIT,
    production_write_blocked: true,
    artifact_write_policy: 'staging-train',
  }
}

describe('model retrain approval contract', () => {
  it('strictly parses and canonically hashes the complete dry-run payload', () => {
    const input = payload()
    expect(parseRetrainDryRunPayload(input)).toEqual(input)
    const reordered = Object.fromEntries(Object.entries(input).reverse())
    expect(canonicalRetrainPayloadHash(reordered)).toBe(canonicalRetrainPayloadHash(input))
    expect(canonicalRetrainPayloadHash(input)).toMatch(/^[0-9a-f]{64}$/)
    expect(computeFeatureContractHash({
      target: input.target,
      model_type: input.model_type,
      selected_features: [...input.selected_features].reverse(),
      removed_features: input.removed_features,
    })).toBe(input.feature_contract_hash)
  })

  it('rejects unknown fields, inconsistent counts, duplicates, invalid periods and removed outsiders', () => {
    expect(parseRetrainDryRunPayload({ ...payload(), path: '../model.joblib' })).toBeNull()
    expect(parseRetrainDryRunPayload({ ...payload(), feature_count: 99 })).toBeNull()
    expect(parseRetrainDryRunPayload({ ...payload(), feature_contract_hash: 'd'.repeat(64) })).toBeNull()
    expect(parseRetrainDryRunPayload({ ...payload(), selected_features: ['horse_age', 'horse_age'] })).toBeNull()
    expect(parseRetrainDryRunPayload({
      ...payload(),
      train_period: { start: '20260101', end: '20250101' },
    })).toBeNull()
    expect(parseRetrainDryRunPayload({
      ...payload(),
      validation_period: { start: '20251231', end: '20260731' },
    })).toBeNull()
    expect(parseRetrainDryRunPayload({
      ...payload(),
      validation_period: { start: '20260230', end: '20260731' },
    })).toBeNull()
    expect(parseRetrainDryRunPayload({ ...payload(), removed_features: ['not_selected'] })).toBeNull()
    expect(parseRetrainDryRunPayload({
      ...payload(),
      selected_features: ['horse_age', 'finish_position'],
      removed_features: [],
      feature_count: 2,
      feature_contract_hash: computeFeatureContractHash({
        target: 'win',
        model_type: 'lightgbm',
        selected_features: ['horse_age', 'finish_position'],
        removed_features: [],
      }),
    })).toBeNull()
    expect(parseRetrainDryRunPayload({
      ...payload(),
      data_snapshot_id: '0'.repeat(64),
    })).toBeNull()
    expect(parseRetrainDryRunPayload({
      ...payload(),
      expected_outputs: ['model-artifact'],
    })).toBeNull()
    expect(parseRetrainDryRunPayload({
      ...payload(),
      safety_checks: payload().safety_checks.filter(check => check.key !== 'future_field_exclusion'),
    })).toBeNull()
  })

  it('keeps the TypeScript future-field blocklist equal to the canonical YAML catalog', () => {
    const yaml = readFileSync(path.join(process.cwd(), 'keiba', 'feature_catalog.yaml'), 'utf8')
    const section = yaml.match(/^future_fields:\s*\r?\n([\s\S]*?)^scraped_fields:/m)?.[1] || ''
    const yamlFields = section
      .split(/\r?\n/)
      .map(line => line.match(/^\s*-\s+([A-Za-z0-9_]+)\s*$/)?.[1] || '')
      .filter(Boolean)
    expect([...MODEL_FUTURE_FIELDS].sort()).toEqual(yamlFields.sort())
  })

  it('derives preview state from safety checks and rejects self-asserted readiness', () => {
    const warned = payload()
    warned.safety_checks[0].status = 'warn'
    expect(parseRetrainDryRunPayload(warned)).toBeNull()
    warned.state = 'preview-warn'
    expect(parseRetrainDryRunPayload(warned)?.state).toBe('preview-warn')

    const failed = payload()
    failed.safety_checks[0].status = 'fail'
    failed.state = 'preview-fail'
    expect(parseRetrainDryRunPayload(failed)?.state).toBe('preview-fail')
  })

  it('strictly parses an approved record and rejects malformed status projections', () => {
    const record = approval()
    expect(parseRetrainApprovalRecord(record)).toEqual(record)
    expect(parseRetrainApprovalRecord({ ...record, extra: true })).toBeNull()
    expect(parseRetrainApprovalRecord({ ...record, approved_by: null })).toBeNull()
    expect(parseRetrainApprovalRecord({ ...record, invalidation_reason: 'manual-invalidation' })).toBeNull()
    expect(parseRetrainApprovalRecord({ ...record, approved_by: ACTOR })).toBeNull()
    expect(parseRetrainApprovalRecord({ ...record, approved_at: '2026-08-02T10:04:00.000Z' })).toBeNull()
    expect(parseRetrainApprovalRecord({
      ...record,
      approval_status: 'pending',
      approved_by: APPROVER,
    })).toBeNull()
  })

  it('returns eligible only when every immutable and policy boundary matches', () => {
    const input = payload()
    const assessment = assessApprovedRetrainEligibility({
      payload: input,
      approval: approval(input),
      context: context(),
    })
    expect(assessment.execution_eligible).toBe(true)
    expect(assessment.execution_performed).toBe(false)
    expect(assessment.failure_codes).toEqual([])
    expect(Object.values(assessment.preconditions).every(Boolean)).toBe(true)
  })

  const mutations: Array<[string, (input: ReturnType<typeof payload>, record: ReturnType<typeof approval>, ctx: RetrainEligibilityContext) => void, string]> = [
    ['payload mutation', input => { input.selected_features[0] = 'changed_feature' }, 'approved-payload-hash-mismatch'],
    ['expired approval', (_input, _record, ctx) => { ctx.now = '2026-08-02T12:00:00.001Z' }, 'approval-expired'],
    ['active model changed', (_input, _record, ctx) => { ctx.current_active_model_id = 'model-other' }, 'active-model-changed'],
    ['feature contract changed', (_input, _record, ctx) => { ctx.current_feature_contract_hash = 'd'.repeat(64) }, 'feature-contract-changed'],
    ['code version changed', (_input, _record, ctx) => { ctx.current_code_version = 'keiba-ai-pro-v2' }, 'code-version-changed'],
    ['commit changed', (_input, _record, ctx) => { ctx.current_git_commit = 'e'.repeat(40) }, 'code-version-changed'],
    ['non-admin caller', (_input, _record, ctx) => { ctx.actor_is_admin = false }, 'admin-role-required'],
    ['production writes enabled', (_input, _record, ctx) => { ctx.production_write_blocked = false }, 'production-write-not-blocked'],
    ['artifact scope mismatch', (_input, _record, ctx) => { ctx.artifact_write_policy = 'sandbox-train' }, 'artifact-write-policy-invalid'],
    ['missing action', (_input, record) => { record.allowed_actions = ['view_approval_status'] }, 'submit-action-not-approved'],
    ['requester changed', (_input, record) => { record.requested_by = APPROVER }, 'approval-requester-mismatch'],
    ['rejected approval', (_input, record) => {
      record.approval_status = 'rejected'
      record.invalidation_reason = 'other'
    }, 'approval-status-not-approved'],
  ]

  for (const [name, mutate, failure] of mutations) {
    it(`fails closed when ${name}`, () => {
      const input = payload()
      const record = approval(input)
      const ctx = context()
      mutate(input, record, ctx)
      const assessment = assessApprovedRetrainEligibility({ payload: input, approval: record, context: ctx })
      expect(assessment.execution_eligible).toBe(false)
      expect(assessment.execution_performed).toBe(false)
      expect(assessment.failure_codes).toContain(failure)
    })
  }

  it('never treats a warning preview or read-only approval as executable', () => {
    const warned = payload()
    warned.safety_checks[0].status = 'warn'
    warned.state = 'preview-warn'
    const warnedAssessment = assessApprovedRetrainEligibility({
      payload: warned,
      approval: approval(warned),
      context: context(),
    })
    expect(warnedAssessment.failure_codes).toContain('dry-run-preview-not-ready')

    const input = payload()
    const readOnly = approval(input)
    readOnly.execution_policy = 'read-only-preview'
    const readOnlyAssessment = assessApprovedRetrainEligibility({
      payload: input,
      approval: readOnly,
      context: { ...context(), artifact_write_policy: 'disabled' },
    })
    expect(readOnlyAssessment.failure_codes).toContain('artifact-write-policy-invalid')
  })
})
