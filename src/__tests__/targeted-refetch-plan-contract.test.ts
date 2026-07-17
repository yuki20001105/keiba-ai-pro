import { describe, expect, test } from 'vitest'
import {
  validateTargetedRefetchPlanReport,
  validateTargetedRefetchRequestBody,
} from '@/lib/targeted-refetch-plan-contract'

function validPlan(target: 'all' | 'race' | 'horse' | 'result' | 'pedigree' | 'odds' = 'all') {
  return {
    target,
    verdict: 'pass',
    verdict_reason: 'targeted-refetch-dry-run',
    p0_total_count: 10,
    refetch_candidate_count: 2,
    unique_url_count: 2,
    race_result_url_count: 1,
    race_detail_url_count: 1,
    horse_detail_url_count: 0,
    pedigree_url_count: 0,
    excluded_schema_review_count: 0,
    excluded_domain_allowed_count: 0,
    excluded_metadata_repair_count: 0,
    excluded_cache_available_count: 1,
    reparse_candidate_count: 1,
    estimated_http_request_count: 2,
    estimated_runtime_seconds: 2.4,
    sample_urls: {
      result_page: [
        {
          url: 'https://db.netkeiba.com/race/202601010101/',
          url_type: 'result_page',
          race_id: '202601010101',
          horse_id: null,
          reason: 'true-missing',
          column: 'finish_position',
          priority: 'P0',
          source: 'db',
          recommended_next_action: 'targeted refetch dry-run',
        },
      ],
      race_detail: [
        {
          url: 'https://db.netkeiba.com/race/202601010102/',
          url_type: 'race_detail',
          race_id: '202601010102',
          horse_id: null,
          reason: 'consistency:race_without_horse_data',
          column: '(check)',
          priority: 'P0',
          source: 'db',
          recommended_next_action: 'targeted refetch dry-run',
        },
      ],
      horse_detail: [],
      pedigree: [],
    },
    recommended_next_actions: ['ok'],
    safety_flags: {
      read_only: true,
      no_db_write: true,
      no_http_access: true,
      no_scrape_execute: true,
      no_upsert: true,
      no_force_refresh_execute: true,
    },
  }
}

describe('targeted refetch plan request contract', () => {
  test('accepts empty object as default request', () => {
    const result = validateTargetedRefetchRequestBody({})
    expect(result.ok).toBe(true)
    if (result.ok) {
      expect(result.value).toEqual({ target: 'all', max_targets: 10 })
    }
  })

  test('rejects null/array/primitive bodies', () => {
    expect(validateTargetedRefetchRequestBody(null).ok).toBe(false)
    expect(validateTargetedRefetchRequestBody([]).ok).toBe(false)
    expect(validateTargetedRefetchRequestBody(1).ok).toBe(false)
  })

  test('rejects unknown key and path-like value', () => {
    expect(validateTargetedRefetchRequestBody({ foo: 1 }).ok).toBe(false)
    expect(validateTargetedRefetchRequestBody({ target: 'all', max_targets: 10, path: '/tmp/a' }).ok).toBe(false)
    expect(validateTargetedRefetchRequestBody({ target: '../all', max_targets: 10 }).ok).toBe(false)
  })

  test('max_targets boundaries', () => {
    expect(validateTargetedRefetchRequestBody({ target: 'all', max_targets: 1 }).ok).toBe(true)
    expect(validateTargetedRefetchRequestBody({ target: 'all', max_targets: 50 }).ok).toBe(true)
    expect(validateTargetedRefetchRequestBody({ target: 'all', max_targets: 0 }).ok).toBe(false)
    expect(validateTargetedRefetchRequestBody({ target: 'all', max_targets: 51 }).ok).toBe(false)
  })
})

describe('targeted refetch plan response contract', () => {
  test('accepts valid report', () => {
    const parsed = validateTargetedRefetchPlanReport(validPlan(), { target: 'all', max_targets: 10 })
    expect(parsed.ok).toBe(true)
  })

  test('accepts zero candidate success', () => {
    const plan = validPlan('race')
    plan.refetch_candidate_count = 0
    plan.unique_url_count = 0
    plan.estimated_http_request_count = 0
    plan.estimated_runtime_seconds = 0
    plan.verdict = 'pass'
    plan.sample_urls.result_page = []
    plan.sample_urls.race_detail = []
    const parsed = validateTargetedRefetchPlanReport(plan, { target: 'race', max_targets: 10 })
    expect(parsed.ok).toBe(true)
  })

  test('rejects malformed numeric values', () => {
    const plan = validPlan()
    ;(plan as any).p0_total_count = -1
    const parsed = validateTargetedRefetchPlanReport(plan, { target: 'all', max_targets: 10 })
    expect(parsed.ok).toBe(false)
  })

  test('rejects target mismatch', () => {
    const parsed = validateTargetedRefetchPlanReport(validPlan('race'), { target: 'all', max_targets: 10 })
    expect(parsed.ok).toBe(false)
  })

  test('rejects missing/false safety flag', () => {
    const plan = validPlan()
    ;(plan.safety_flags as any).no_http_access = false
    const parsed = validateTargetedRefetchPlanReport(plan, { target: 'all', max_targets: 10 })
    expect(parsed.ok).toBe(false)
  })

  test('rejects sample URL over max_targets', () => {
    const plan = validPlan()
    plan.sample_urls.result_page.push({ ...plan.sample_urls.result_page[0], url: 'https://db.netkeiba.com/race/202601010103/' })
    const parsed = validateTargetedRefetchPlanReport(plan, { target: 'all', max_targets: 1 })
    expect(parsed.ok).toBe(false)
  })

  test('rejects invalid sample URL format', () => {
    const plan = validPlan()
    ;(plan.sample_urls.result_page[0] as any).url = 'https://example.com/anything'
    const parsed = validateTargetedRefetchPlanReport(plan, { target: 'all', max_targets: 10 })
    expect(parsed.ok).toBe(false)
  })

  test('rejects server path-like values in report fields', () => {
    const plan = validPlan()
    ;(plan.sample_urls.result_page[0] as any).reason = 'C:\\temp\\a.json'
    const parsed = validateTargetedRefetchPlanReport(plan, { target: 'all', max_targets: 10 })
    expect(parsed.ok).toBe(false)
  })

  test('accepts real planner shape with internal provenance paths', () => {
    const plan = {
      ...validPlan('all'),
      timestamp: '2026-07-18T02:00:00Z',
      p0_plan_total_count: 12,
      audit_p0_true_missing_count: 4,
      rate_limit_policy: 'conservative',
      cache_diagnosis_note: 'cache-only review',
      input_audit: 'C:\\Users\\test\\AppData\\Local\\Temp\\planner\\audit.json',
      input_p0_plan: 'C:\\Users\\test\\AppData\\Local\\Temp\\planner\\p0_plan.json',
      input_cache_diagnosis: 'C:\\Users\\test\\AppData\\Local\\Temp\\planner\\cache.json',
    }

    const parsed = validateTargetedRefetchPlanReport(plan, { target: 'all', max_targets: 10 })
    expect(parsed.ok).toBe(true)
  })

  test('rejects surfaced path attacks and allows safe slash wording', () => {
    const withWindowsPath = validPlan()
    ;(withWindowsPath.sample_urls.result_page[0] as any).reason = 'failed at C:\\secret\\data.json'
    expect(validateTargetedRefetchPlanReport(withWindowsPath, { target: 'all', max_targets: 10 }).ok).toBe(false)

    const withUnixPath = validPlan()
    ;(withUnixPath.sample_urls.result_page[0] as any).recommended_next_action = '/etc/passwd を確認'
    expect(validateTargetedRefetchPlanReport(withUnixPath, { target: 'all', max_targets: 10 }).ok).toBe(false)

    const withFileUri = validPlan()
    ;(withFileUri.sample_urls.result_page[0] as any).source = 'file:///tmp/source'
    expect(validateTargetedRefetchPlanReport(withFileUri, { target: 'all', max_targets: 10 }).ok).toBe(false)

    const withUnc = validPlan()
    ;(withUnc.sample_urls.result_page[0] as any).source = 'UNC \\\\server\\share\\x.json'
    expect(validateTargetedRefetchPlanReport(withUnc, { target: 'all', max_targets: 10 }).ok).toBe(false)

    const safeSlashWording = validPlan()
    ;(safeSlashWording.sample_urls.result_page[0] as any).recommended_next_action = 'date/race_id 分割で段階実行を検討'
    expect(validateTargetedRefetchPlanReport(safeSlashWording, { target: 'all', max_targets: 10 }).ok).toBe(true)
  })

  test('rejects invalid IDs and oversized actions', () => {
    const plan = validPlan()
    ;(plan.sample_urls.result_page[0] as any).race_id = '2026/01010101'
    let parsed = validateTargetedRefetchPlanReport(plan, { target: 'all', max_targets: 10 })
    expect(parsed.ok).toBe(false)

    const plan2 = validPlan()
    plan2.recommended_next_actions = new Array(21).fill('next-action')
    parsed = validateTargetedRefetchPlanReport(plan2, { target: 'all', max_targets: 10 })
    expect(parsed.ok).toBe(false)
  })
})
