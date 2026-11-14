export const ACQUISITION_SAFETY_POLICY_VERSION = 'normal-acquisition-safe-v1'
export const ACQUISITION_SAFETY_POLICY_LABEL = '最低3秒・HTTP400で停止・保存済み値を保護'
export const REQUIRED_PUBLIC_INPUTS_VERSION = 'historical-jra-required-public-v1'
export const REQUIRED_PUBLIC_INPUTS_LABEL = '過去30日超の中央馬は、出典付き血統と対象日前の成績だけを補助取得'

function scrapeCapabilities(value: unknown): Record<string, unknown> | null {
  if (typeof value !== 'object' || value === null || Array.isArray(value)) return null
  const capabilities = (value as Record<string, unknown>).scrape_capabilities
  if (typeof capabilities !== 'object' || capabilities === null || Array.isArray(capabilities)) return null
  return capabilities as Record<string, unknown>
}

/** The running API must explicitly advertise the complete, versioned policy. */
export function supportsSafeAcquisition(value: unknown): boolean {
  const fields = scrapeCapabilities(value)
  if (!fields) return false
  return fields.safety_policy_version === ACQUISITION_SAFETY_POLICY_VERSION
    && typeof fields.minimum_provider_interval_seconds === 'number'
    && Number.isFinite(fields.minimum_provider_interval_seconds)
    && fields.minimum_provider_interval_seconds >= 3
    && fields.halt_http400 === true
    && fields.preserve_saved_values === true
    && fields.max_concurrent_http_requests === 1
}

export function supportsRequiredPublicInputs(value: unknown): boolean {
  const fields = scrapeCapabilities(value)
  return supportsSafeAcquisition(value) && fields !== null
    && fields.required_public_inputs_version === REQUIRED_PUBLIC_INPUTS_VERSION
    && fields.required_public_inputs_scope === 'historical_jra_older_than_30_days'
    && fields.profile_totals_are_model_inputs === false
    && fields.prior_results_auto_imported === false
}

export function supportsCachedCardAcquisition(value: unknown): boolean {
  return supportsSafeAcquisition(value)
    && scrapeCapabilities(value)?.prediction_card_policy_version === 'prediction-card-cached-v1'
}
