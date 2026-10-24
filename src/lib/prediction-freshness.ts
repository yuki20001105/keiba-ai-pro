import type { RacePredictResult } from '@/lib/race-analysis-types'

export const PREDICTION_CACHE_TTL_MS = 30 * 60 * 1000

export type PredictionFreshness = {
  schema_version: 'prediction-freshness-v1'
  generated_at?: string
  data_revision: string
  model_runtime_revision: string
  resolved_model_id: string
  cache_hit?: boolean
  source?: 'saved_db' | 'auto'
  data_changed_during_prediction?: boolean
  revision_kind?: 'sqlite-source-generation' | 'sqlite-file-version'
  source_tracking?: boolean
  source_snapshot_verified?: false
}

export type PredictionCacheStatus = 'current' | 'stale' | 'unverified'

export function parsePredictionFreshness(value: unknown): PredictionFreshness | null {
  if (!value || typeof value !== 'object') return null
  const data = value as Record<string, unknown>
  if (data.schema_version !== 'prediction-freshness-v1'
    || typeof data.data_revision !== 'string' || !data.data_revision
    || typeof data.model_runtime_revision !== 'string' || !data.model_runtime_revision
    || typeof data.resolved_model_id !== 'string' || !data.resolved_model_id) return null
  return data as PredictionFreshness
}

export function supportsSavedDbRecompute(value: unknown): boolean {
  if (!value || typeof value !== 'object') return false
  const data = value as Record<string, unknown>
  const capabilities = data.capabilities as Record<string, unknown> | undefined
  return capabilities?.saved_db_recompute === true && parsePredictionFreshness(data.freshness ?? data) !== null
}

/** A saved result is not current merely because its race happened in the past. */
export function predictionCacheStatus(result: RacePredictResult, current: PredictionFreshness | null,
  cachedAt: number, ownerMatches: boolean, now = Date.now()): PredictionCacheStatus {
  const saved = parsePredictionFreshness(result.freshness)
  if (!ownerMatches || !saved || !current || saved.source_tracking !== true || current.source_tracking !== true) return 'unverified'
  if (!Number.isFinite(cachedAt) || cachedAt > now || now - cachedAt >= PREDICTION_CACHE_TTL_MS
    || saved.data_changed_during_prediction === true
    || saved.data_revision !== current.data_revision
    || saved.model_runtime_revision !== current.model_runtime_revision
    || saved.resolved_model_id !== current.resolved_model_id
    || result.model_id !== current.resolved_model_id) return 'stale'
  return 'current'
}

/** Display old rankings without making a cache a purchase/recording authority. */
export function savedPredictionForDisplay(result: RacePredictResult, status: PredictionCacheStatus): RacePredictResult {
  if (status === 'current') return { ...result, performance_snapshot_id: null }
  return {
    ...result, performance_snapshot_id: null, reference_expectation: null,
    recommendation: result.recommendation ? {
      ...result.recommendation, decision: 'skip', action: '見送り', input_verified: false,
      reason: '保存予測です。最新データで再予測してください。', reason_codes: ['prediction_data_unverified'],
      tickets: [], purchase_count: 0, unit_price: 0, total_cost: 0, expected_return: 0,
    } : null,
  }
}

export function predictionTimestamp(result: RacePredictResult, cachedAt?: number | null): string {
  const timestamp = result.freshness?.generated_at
  const parsed = timestamp ? Date.parse(timestamp) : cachedAt
  if (typeof parsed !== 'number' || !Number.isFinite(parsed)) return '生成時刻不明'
  return new Intl.DateTimeFormat('ja-JP', {
    timeZone: 'Asia/Tokyo', month: '2-digit', day: '2-digit', hour: '2-digit', minute: '2-digit', second: '2-digit', hour12: false,
  }).format(parsed)
}

/** User-scoped display cache. Paper/actual records live elsewhere and are never deleted here. */
export function predictionStorageKey(cacheKey: string, userId?: string | null): string {
  return userId ? `ra-cache:${encodeURIComponent(userId)}::${cacheKey}` : `ra-cache:${cacheKey}`
}

export function readPredictionCache(cacheKey: string, userId?: string | null): {
  data: RacePredictResult; cachedAt: number; ownerMatches: boolean
} | null {
  try {
    const raw = localStorage.getItem(predictionStorageKey(cacheKey, userId))
      // Legacy results are display-only. Never borrow another user's scoped result.
      ?? localStorage.getItem(`ra-cache:${cacheKey}`)
    if (!raw) return null
    const parsed = JSON.parse(raw)
    if (!parsed?.data || typeof parsed.cachedAt !== 'number') return null
    if (parsed.owner_user_id && parsed.owner_user_id !== userId) return null
    return { data: parsed.data, cachedAt: parsed.cachedAt,
      ownerMatches: typeof userId === 'string' && !!userId && parsed.owner_user_id === userId }
  } catch { return null }
}

export function writePredictionCache(cacheKey: string, data: RacePredictResult, cachedAt: number, userId?: string | null) {
  try {
    localStorage.setItem(predictionStorageKey(cacheKey, userId), JSON.stringify({ data, cachedAt, owner_user_id: userId ?? null }))
  } catch { /* The in-memory result remains usable if storage is full. */ }
}
