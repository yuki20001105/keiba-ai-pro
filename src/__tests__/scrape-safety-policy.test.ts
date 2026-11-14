import { describe, expect, it } from 'vitest'
import { supportsRequiredPublicInputs, supportsSafeAcquisition } from '@/lib/scrape-safety-policy'

const policy = {
  safety_policy_version: 'normal-acquisition-safe-v1', minimum_provider_interval_seconds: 3,
  halt_http400: true, preserve_saved_values: true, max_concurrent_http_requests: 1,
}

describe('normal acquisition safety capability', () => {
  it('requires all the deployed guarantees and allows a slower provider interval', () => {
    expect(supportsSafeAcquisition({ scrape_capabilities: policy })).toBe(true)
    expect(supportsSafeAcquisition({ scrape_capabilities: { ...policy, minimum_provider_interval_seconds: 5 } })).toBe(true)
  })

  describe('required public inputs capability', () => {
    const required = {
      ...policy, required_public_inputs_version: 'historical-jra-required-public-v1',
      required_public_inputs_scope: 'historical_jra_older_than_30_days',
      profile_totals_are_model_inputs: false, prior_results_auto_imported: false,
    }

    it('does not start the new collection policy against an older API', () => {
      expect(supportsRequiredPublicInputs({ scrape_capabilities: policy })).toBe(false)
      expect(supportsRequiredPublicInputs({ scrape_capabilities: required })).toBe(true)
    })

    it.each([
      { required_public_inputs_version: 'unknown' },
      { required_public_inputs_scope: 'all' },
      { profile_totals_are_model_inputs: true },
      { prior_results_auto_imported: true },
      { halt_http400: false },
    ])('requires accurate scope and the existing safety policy: %j', fields => {
      expect(supportsRequiredPublicInputs({ scrape_capabilities: { ...required, ...fields } })).toBe(false)
    })
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
