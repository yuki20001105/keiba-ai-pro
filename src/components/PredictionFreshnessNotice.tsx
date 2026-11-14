import type { RacePredictResult } from '@/lib/race-analysis-types'
import type { PredictionCacheStatus } from '@/lib/prediction-freshness'

export function PredictionFreshnessNotice({ result, status }: {
  result: RacePredictResult; status?: PredictionCacheStatus | 'computed'; cachedAt?: number | null
}) {
  const needsRefresh = status === 'stale' || status === 'unverified' || result.freshness?.data_changed_during_prediction === true
  if (!needsRefresh) return null
  const message = status === 'unverified'
    ? '最新データを確認できません · 再予測してください'
    : 'データが更新されています · 再予測してください'
  return (
    <p data-testid="prediction-freshness-notice" role="status" className="text-xs text-[#facc15]">{message}</p>
  )
}
