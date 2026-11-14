'use client'
import type { RacePredictResult } from '@/lib/race-analysis-types'
import { BettingRecommendationPanel } from '@/components/BettingRecommendationPanel'
import { HorseCandidatePanel, HorseCandidateProvider } from '@/components/HorseCandidatePanel'
import { useBettingClock } from '@/hooks/useBettingClock'
import { HorsePredictionTable } from '@/components/HorsePredictionTable'
import { ModelComparisonPanel } from '@/components/ModelComparisonPanel'

type Props = { result: RacePredictResult }

export function RacePredictionPanel({ result }: Props) {
  const preds = result.predictions
  const rec = result.recommendation
  const bettingNow = useBettingClock(preds.length > 0)

  return (
    <div className="flex-1 overflow-auto p-4 space-y-3">
      {/* 推奨カード */}
      <BettingRecommendationPanel recommendation={rec} diagnostics={result.input_diagnostics} snapshotId={result.performance_snapshot_id} />
      <HorseCandidateProvider raceId={result.race_info.race_id} modelId={result.model_id} snapshotId={result.performance_snapshot_id} predictions={preds} runnerCount={result.race_info.num_horses}>
        <div className="grid min-w-0 grid-cols-1 items-start gap-3 xl:grid-cols-[minmax(0,1fr)_300px]">
          <div className="min-w-0"><HorsePredictionTable predictions={preds} recommendation={rec} input_diagnostics={result.input_diagnostics} result={result} nowMs={bettingNow} /></div>
          <div className="min-w-0 xl:sticky xl:top-3"><HorseCandidatePanel raceId={result.race_info.race_id} modelId={result.model_id} snapshotId={result.performance_snapshot_id} predictions={preds} runnerCount={result.race_info.num_horses} /></div>
        </div>
      </HorseCandidateProvider>
      <ModelComparisonPanel result={result} />
    </div>
  )
}
