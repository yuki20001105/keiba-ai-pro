import { describe, expect, test } from 'vitest'
import {
  LiveValidationRequest,
  validateLiveValidationApiResponse,
  validateLiveValidationRequestBody,
} from '@/lib/targeted-refetch-live-validation-contract'

const request: LiveValidationRequest = {
  target: 'all',
  url_type: 'all',
  max_urls: 2,
  confirm_live_fetch: true,
}

export function validLiveValidationResponse(overrides: Record<string, unknown> = {}) {
  return {
    live_validation: true,
    bounded: true,
    external_http: true,
    read_only: true,
    execution_enabled: false,
    internal_path: 'C:\\not-surfaced\\report.json',
    result: {
      target: 'all',
      url_type: 'all',
      max_urls_applied: 2,
      attempted_url_count: 2,
      http_success_count: 1,
      http_error_count: 1,
      parse_success_count: 1,
      parse_error_count: 1,
      would_fix_count: 1,
      would_not_fix_count: 1,
      no_downgrade_count: 0,
      repairable_count: 1,
      elapsed_seconds: 2.1,
      estimated_full_refetch_runtime_seconds: 12.5,
      excluded_schema_review_count: 1,
      excluded_domain_allowed_count: 0,
      excluded_metadata_repair_count: 0,
      excluded_cache_available_count: 1,
      sample_results: [
        {
          url: 'https://db.netkeiba.com/race/202601010101/',
          url_type: 'result_page',
          race_id: '202601010101',
          horse_id: '2021100001',
          http_status: 200,
          parse_status: 'parse_success',
          missing_fields_before: ['finish_position'],
          fields_found_after: ['finish_position'],
          would_fix_columns: ['finish_position'],
          action: 'would-fix',
          reason: 'true-missing',
          recommended_next_action: 'review bounded evidence',
        },
        {
          url: 'https://db.netkeiba.com/horse/ped/2021100002/',
          url_type: 'pedigree',
          race_id: null,
          horse_id: '2021100002',
          http_status: 503,
          parse_status: 'http_error',
          missing_fields_before: ['sire'],
          fields_found_after: [],
          would_fix_columns: [],
          action: 'http_error',
          reason: 'true-missing',
          recommended_next_action: 'review bounded evidence',
        },
      ],
      recommended_next_actions: ['review bounded evidence'],
      rate_limit_policy: {
        max_urls: 2,
        max_supported_urls: 10,
        min_interval_sec: 1,
        max_retries: 1,
        retry_base_sec: 0,
        retry_jitter_sec: 0,
        retry_after_enabled: false,
        max_retry_after_sec: 0,
        per_request_timeout_sec: 12,
        total_timeout_sec: 75,
        max_body_bytes: 1048576,
        circuit_breaker: { threshold: 3, cooldown_sec: 30 },
        parallelism: 1,
        fetch_pipeline_used: true,
      },
      safety_flags: {
        small_live_validation_only: true,
        max_urls_limited: true,
        no_db_write: true,
        no_upsert: true,
        no_repair_execute: true,
        no_production_table_write: true,
        no_force_refresh_execute: true,
        no_bulk_refetch: true,
        redirects_disabled: true,
        bounded_response_body: true,
        bounded_total_runtime: true,
      },
      verdict: 'pass',
      verdict_reason: 'small-live-validation',
      raw_stderr: 'must not surface',
      ...overrides,
    },
  }
}

describe('Phase 3D live validation request contract', () => {
  test('accepts only the explicit confirmed bounded request', () => {
    expect(validateLiveValidationRequestBody(request)).toEqual({ ok: true, value: request })
    expect(validateLiveValidationRequestBody({ ...request, confirm_live_fetch: false }).ok).toBe(false)
    expect(validateLiveValidationRequestBody({ ...request, max_urls: 0 }).ok).toBe(false)
    expect(validateLiveValidationRequestBody({ ...request, max_urls: 4 }).ok).toBe(false)
    expect(validateLiveValidationRequestBody({ ...request, max_urls: 1.5 }).ok).toBe(false)
  })

  test('rejects missing, unknown, URL, path and fixture inputs', () => {
    expect(validateLiveValidationRequestBody({ target: 'all' }).ok).toBe(false)
    expect(validateLiveValidationRequestBody({ ...request, url: 'https://example.com' }).ok).toBe(false)
    expect(validateLiveValidationRequestBody({ ...request, plan: {} }).ok).toBe(false)
    expect(validateLiveValidationRequestBody({ ...request, output: 'C:\\tmp\\x.json' }).ok).toBe(false)
    expect(validateLiveValidationRequestBody({ ...request, fixture_json: 'fixture.json' }).ok).toBe(false)
    expect(validateLiveValidationRequestBody(null).ok).toBe(false)
  })
})

describe('Phase 3D live validation response contract', () => {
  test('accepts a partial result and projects only approved fields', () => {
    const parsed = validateLiveValidationApiResponse(validLiveValidationResponse(), request)
    expect(parsed.ok).toBe(true)
    if (!parsed.ok) return
    expect(parsed.value.result.attempted_url_count).toBe(2)
    expect(parsed.value).not.toHaveProperty('internal_path')
    expect(parsed.value.result).not.toHaveProperty('raw_stderr')
  })

  test('accepts zero-target warn as a bounded successful response', () => {
    const parsed = validateLiveValidationApiResponse(validLiveValidationResponse({
      attempted_url_count: 0,
      http_success_count: 0,
      http_error_count: 0,
      parse_success_count: 0,
      parse_error_count: 0,
      would_fix_count: 0,
      would_not_fix_count: 0,
      no_downgrade_count: 0,
      repairable_count: 0,
      sample_results: [],
      verdict: 'warn',
    }), request)
    expect(parsed.ok).toBe(true)
  })

  test('rejects false-green verdicts and aggregates that contradict samples', () => {
    const zeroPass = validLiveValidationResponse({
      attempted_url_count: 0,
      http_success_count: 0,
      http_error_count: 0,
      parse_success_count: 0,
      parse_error_count: 0,
      would_fix_count: 0,
      would_not_fix_count: 0,
      no_downgrade_count: 0,
      repairable_count: 0,
      sample_results: [],
      verdict: 'pass',
    })
    expect(validateLiveValidationApiResponse(zeroPass, request).ok).toBe(false)

    const aggregateLie = validLiveValidationResponse()
    aggregateLie.result.sample_results[0].http_status = 503
    aggregateLie.result.sample_results[0].parse_status = 'http_error'
    aggregateLie.result.sample_results[0].would_fix_columns = []
    aggregateLie.result.sample_results[0].action = 'http_error'
    expect(validateLiveValidationApiResponse(aggregateLie, request).ok).toBe(false)

    const actionLie = validLiveValidationResponse()
    actionLie.result.sample_results[0].action = 'no-downgrade-skip'
    expect(validateLiveValidationApiResponse(actionLie, request).ok).toBe(false)
  })

  test('requires exact missing/found/would-fix evidence in both directions', () => {
    const falsePositive = validLiveValidationResponse()
    falsePositive.result.sample_results[0].fields_found_after = ['horse_name']
    expect(validateLiveValidationApiResponse(falsePositive, request).ok).toBe(false)

    const falseNegative = validLiveValidationResponse()
    falseNegative.result.sample_results[0].would_fix_columns = []
    falseNegative.result.sample_results[0].action = 'no-downgrade-skip'
    expect(validateLiveValidationApiResponse(falseNegative, request).ok).toBe(false)

    const duplicateEvidence = validLiveValidationResponse()
    duplicateEvidence.result.sample_results[0].missing_fields_before = ['finish_position', 'finish_position']
    expect(validateLiveValidationApiResponse(duplicateEvidence, request).ok).toBe(false)

    const special = JSON.parse(JSON.stringify(validLiveValidationResponse()))
    special.result.sample_results[0] = {
      ...special.result.sample_results[0],
      reason: 'consistency:race_without_horse_data',
      missing_fields_before: ['horse_id', 'horse_name', 'frame_number', 'horse_number', '(check)'],
      fields_found_after: ['(check)'],
      would_fix_columns: ['(check)'],
      action: 'would-fix',
    }
    expect(validateLiveValidationApiResponse(special, request).ok).toBe(true)

    special.result.sample_results[0].missing_fields_before = ['horse_id', 'horse_name']
    expect(validateLiveValidationApiResponse(special, request).ok).toBe(false)
  })

  test('rejects mismatches and inconsistent aggregate counts', () => {
    expect(validateLiveValidationApiResponse(validLiveValidationResponse({ target: 'race' }), request).ok).toBe(false)
    expect(validateLiveValidationApiResponse(validLiveValidationResponse({ max_urls_applied: 3 }), request).ok).toBe(false)
    expect(validateLiveValidationApiResponse(validLiveValidationResponse({ http_success_count: 2 }), request).ok).toBe(false)
    expect(validateLiveValidationApiResponse(validLiveValidationResponse({ parse_error_count: 0 }), request).ok).toBe(false)
    expect(validateLiveValidationApiResponse(validLiveValidationResponse({ repairable_count: 0 }), request).ok).toBe(false)
    expect(validateLiveValidationApiResponse(validLiveValidationResponse({ elapsed_seconds: 91 }), request).ok).toBe(false)
  })

  test('rejects unsafe URLs, redirects encoded as alternate hosts and surfaced paths', () => {
    const unsafeHosts = [
      'http://db.netkeiba.com/race/202601010101/',
      'https://db.netkeiba.com.evil.test/race/202601010101/',
      'https://user@db.netkeiba.com/race/202601010101/',
      'https://db.netkeiba.com:443/race/202601010101/',
      'https://db.netkeiba.com/race/202601010101/?next=https://evil.test',
    ]
    for (const url of unsafeHosts) {
      const payload = validLiveValidationResponse()
      payload.result.sample_results[0].url = url
      expect(validateLiveValidationApiResponse(payload, request).ok).toBe(false)
    }
    const pathPayload = validLiveValidationResponse()
    pathPayload.result.sample_results[0].reason = 'read C:\\secret\\file.json'
    expect(validateLiveValidationApiResponse(pathPayload, request).ok).toBe(false)
  })

  test('rejects weakened safety or runtime bounds and malformed sample values', () => {
    const safety = validLiveValidationResponse()
    safety.result.safety_flags.redirects_disabled = false
    expect(validateLiveValidationApiResponse(safety, request).ok).toBe(false)

    const bodyCap = validLiveValidationResponse()
    bodyCap.result.rate_limit_policy.max_body_bytes = 3 * 1024 * 1024
    expect(validateLiveValidationApiResponse(bodyCap, request).ok).toBe(false)

    const retryPolicy = JSON.parse(JSON.stringify(validLiveValidationResponse()))
    retryPolicy.result.rate_limit_policy.max_retries = 2
    expect(validateLiveValidationApiResponse(retryPolicy, request).ok).toBe(false)

    const retryAfter = JSON.parse(JSON.stringify(validLiveValidationResponse()))
    retryAfter.result.rate_limit_policy.retry_after_enabled = true
    expect(validateLiveValidationApiResponse(retryAfter, request).ok).toBe(false)

    const badId = validLiveValidationResponse()
    badId.result.sample_results[0].race_id = '../secret'
    expect(validateLiveValidationApiResponse(badId, request).ok).toBe(false)

    const badSamples = validLiveValidationResponse()
    badSamples.result.sample_results = badSamples.result.sample_results.slice(0, 1)
    expect(validateLiveValidationApiResponse(badSamples, request).ok).toBe(false)

    const mismatchedPathId = validLiveValidationResponse()
    mismatchedPathId.result.sample_results[0].race_id = '202601010102'
    expect(validateLiveValidationApiResponse(mismatchedPathId, request).ok).toBe(false)

    const duplicateUrl = validLiveValidationResponse()
    duplicateUrl.result.sample_results[1].url = duplicateUrl.result.sample_results[0].url
    duplicateUrl.result.sample_results[1].url_type = 'result_page'
    duplicateUrl.result.sample_results[1].race_id = '202601010101'
    duplicateUrl.result.sample_results[1].horse_id = '2021100001'
    expect(validateLiveValidationApiResponse(duplicateUrl, request).ok).toBe(false)
  })
})
