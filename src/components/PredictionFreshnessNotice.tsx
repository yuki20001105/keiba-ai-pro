import type { RacePredictResult } from '@/lib/race-analysis-types'
import { predictionTimestamp, type PredictionCacheStatus } from '@/lib/prediction-freshness'

export function PredictionFreshnessNotice({ result, status, cachedAt }: {
  result: RacePredictResult; status?: PredictionCacheStatus | 'computed'; cachedAt?: number | null
}) {
  const saved = status && status !== 'computed'
  const needsRefresh = status === 'stale' || status === 'unverified' || result.freshness?.data_changed_during_prediction === true
  return (
    <div data-testid="prediction-freshness-notice" role="status" className={`text-xs space-y-1 ${needsRefresh ? 'text-[#facc15]' : 'text-[#888]'}`}>
      <p>{needsRefresh ? '保存予測 · 最新データで再予測してください' : saved ? '保存予測 · データ・モデルの更新なし' : '保存済みDBから予測'} · {predictionTimestamp(result, cachedAt)} JST</p>
      {result.model_id && <details className="text-[#888]"><summary className="cursor-pointer">使用モデルを確認</summary><p className="mt-1 break-all">{result.model_id}</p></details>}
    </div>
  )
}
