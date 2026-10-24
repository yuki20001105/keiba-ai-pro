export const ACQUISITION_SAFETY_POLICY_VERSION = 'normal-acquisition-safe-v1'
export const ACQUISITION_SAFETY_POLICY_LABEL = '最低3秒・HTTP400で停止・保存済み値を保護'

/** The running API must explicitly advertise the complete, versioned policy. */
export function supportsSafeAcquisition(value: unknown): boolean {
  if (typeof value !== 'object' || value === null || Array.isArray(value)) return false
  const capabilities = (value as Record<string, unknown>).scrape_capabilities
  if (typeof capabilities !== 'object' || capabilities === null || Array.isArray(capabilities)) return false
  const fields = capabilities as Record<string, unknown>
  return fields.safety_policy_version === ACQUISITION_SAFETY_POLICY_VERSION
    && typeof fields.minimum_provider_interval_seconds === 'number'
    && Number.isFinite(fields.minimum_provider_interval_seconds)
    && fields.minimum_provider_interval_seconds >= 3
    && fields.halt_http400 === true
    && fields.preserve_saved_values === true
    && fields.max_concurrent_http_requests === 1
}
