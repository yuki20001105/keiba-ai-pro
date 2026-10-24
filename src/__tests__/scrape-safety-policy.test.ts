import { describe, expect, it } from 'vitest'
import { supportsSafeAcquisition } from '@/lib/scrape-safety-policy'

const policy = {
  safety_policy_version: 'normal-acquisition-safe-v1', minimum_provider_interval_seconds: 3,
  halt_http400: true, preserve_saved_values: true, max_concurrent_http_requests: 1,
}

describe('normal acquisition safety capability', () => {
  it('requires all the deployed guarantees and allows a slower provider interval', () => {
    expect(supportsSafeAcquisition({ scrape_capabilities: policy })).toBe(true)
    expect(supportsSafeAcquisition({ scrape_capabilities: { ...policy, minimum_provider_interval_seconds: 5 } })).toBe(true)
  })

  it.each([null, [], {}, { scrape_capabilities: [] }, { scrape_capabilities: {
    race_scopes: ['all', 'jra'], jra_month_reuse: true,
  } }])('does not infer a safe policy from old or invalid health: %j', value => {
    expect(supportsSafeAcquisition(value)).toBe(false)
  })

  it.each([
    { safety_policy_version: 'unknown' }, { minimum_provider_interval_seconds: 1 },
    { minimum_provider_interval_seconds: '3' }, { minimum_provider_interval_seconds: NaN },
    { minimum_provider_interval_seconds: Infinity }, { halt_http400: false },
    { halt_http400: 'true' }, { preserve_saved_values: false }, { preserve_saved_values: 'true' },
    { max_concurrent_http_requests: 3 }, { max_concurrent_http_requests: '1' },
  ])('fails closed for a missing guarantee: %j', fields => {
    expect(supportsSafeAcquisition({ scrape_capabilities: { ...policy, ...fields } })).toBe(false)
  })
})
