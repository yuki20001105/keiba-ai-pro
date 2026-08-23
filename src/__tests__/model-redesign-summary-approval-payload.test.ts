import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import {
  canonicalRetrainPayloadHash,
  parseRetrainDryRunPayload,
} from '@/lib/model-retrain-approval-contract'

const ACTOR = '11111111-1111-4111-8111-111111111111'
const COMMIT = 'a'.repeat(40)

vi.mock('@supabase/supabase-js', () => ({
  createClient: () => ({
    auth: {
      getUser: async () => ({ data: { user: { id: ACTOR } }, error: null }),
    },
    from: () => ({
      select: () => ({
        eq: () => ({
          single: async () => ({
            data: { role: 'admin', subscription_tier: 'premium' },
            error: null,
          }),
        }),
      }),
    }),
  }),
}))

import { POST } from '@/app/api/model-redesign/summary/route'

function request(overrides: Record<string, unknown> = {}): Request {
  return new Request('http://localhost/api/model-redesign/summary', {
    method: 'POST',
    headers: {
      Authorization: 'Bearer test-token',
      'Content-Type': 'application/json',
    },
    body: JSON.stringify({
      action: 'retrain_dry_run',
      target: 'win',
      model_type: 'lightgbm',
      train_period: { start: '20240101', end: '20251231' },
      validation_period: { start: '20260101', end: '20260731' },
      selected_features: ['horse_age', 'jockey_win_rate'],
      removed_features: [],
      data_snapshot_id: 'c'.repeat(64),
      ...overrides,
    }),
  })
}

describe('model redesign dry-run approval payload producer', () => {
  beforeEach(() => {
    process.env.NEXT_PUBLIC_SUPABASE_URL = 'https://example.supabase.co'
    process.env.NEXT_PUBLIC_SUPABASE_ANON_KEY = 'test-anon-key'
    process.env.APP_COMMIT_SHA = COMMIT
  })

  afterEach(() => {
    delete process.env.NEXT_PUBLIC_SUPABASE_URL
    delete process.env.NEXT_PUBLIC_SUPABASE_ANON_KEY
    delete process.env.APP_COMMIT_SHA
  })

  it('produces a complete canonical payload bound to actor, active model and commit', async () => {
    const result = await POST(request())
    const body = await result.json()
    const parsed = parseRetrainDryRunPayload(body.dry_run_payload)

    expect(result.status).toBe(200)
    expect(body.code).toBe('dry-run-preview')
    expect(body.state).toBe('pass')
    expect(body.guard.approval_payload_ready).toBe(true)
    expect(parsed).not.toBeNull()
    expect(parsed?.created_by).toBe(ACTOR)
    expect(parsed?.git_commit).toBe(COMMIT)
    expect(parsed?.data_snapshot_id).toBe('c'.repeat(64))
    expect(parsed?.state).toBe('preview-ready')
    expect(body.approved_payload_hash).toBe(canonicalRetrainPayloadHash(parsed))
  })

  it('fails the preview when the immutable data snapshot is missing', async () => {
    const result = await POST(request({ data_snapshot_id: undefined }))
    const body = await result.json()

    expect(body.state).toBe('fail')
    expect(body.guard.approval_payload_ready).toBe(false)
    expect(body.dry_run_payload.state).toBe('preview-fail')
    expect(body.dry_run_payload.warnings).toContain('data_snapshot_bound')
  })

  it('fails the preview when a canonical future field is selected', async () => {
    const result = await POST(request({ selected_features: ['horse_age', 'finish_position'] }))
    const body = await result.json()

    expect(body.state).toBe('fail')
    expect(body.dry_run_payload).toBeNull()
    expect(body.approval_payload_blockers).toContain('future_field_exclusion')
  })

  it('does not accept caller identity, commit, active model, or path overrides', async () => {
    const identityOverride = await POST(request({
      created_by: '99999999-9999-4999-8999-999999999999',
      git_commit: 'f'.repeat(40),
      active_model_id: 'attacker-model',
    }))
    const identityBody = await identityOverride.json()
    expect(identityBody.dry_run_payload.created_by).toBe(ACTOR)
    expect(identityBody.dry_run_payload.git_commit).toBe(COMMIT)
    expect(identityBody.dry_run_payload.active_model_id).not.toBe('attacker-model')

    const pathOverride = await POST(request({ modelPath: '../model.joblib' }))
    expect(pathOverride.status).toBe(400)
    expect((await pathOverride.json()).code).toBe('path-input-forbidden')
  })
})
