'use client'
import type { RacePredictResult } from '@/lib/race-analysis-types'
import { BettingRecommendationPanel } from '@/components/BettingRecommendationPanel'
import { PredictionInputNotice } from '@/components/PredictionInputNotice'
import { predictionInputVerified } from '@/lib/prediction-input-contract'
import { predictionExpectationDisplay, REFERENCE_EXPECTATION_CAVEAT, REFERENCE_EXPECTATION_NOTE } from '@/lib/prediction-expectation-display'
import { useBettingClock } from '@/hooks/useBettingClock'

function probColor(p: number): string {
  if (p > 0.3) return 'bg-[#86efac]'
  if (p > 0.2) return 'bg-[#a3e635]'
  if (p > 0.1) return 'bg-[#fbbf24]'
  return 'bg-[#555]'
}

type Props = { result: RacePredictResult }

export function RacePredictionPanel({ result }: Props) {
  const preds = result.predictions
  const rec = result.recommendation
  const calibrated = rec?.probability_basis === 'calibrated' && rec.input_verified === true && predictionInputVerified(result.input_diagnostics)
  const bettingNow = useBettingClock(preds.length > 0)
  const expectation = predictionExpectationDisplay(result, rec, bettingNow)
  const maxReferenceRatio = Math.max(...preds.map(p => p.p_norm), 0.001)

  return (
    <div className="flex-1 overflow-auto p-4 space-y-4">
      <PredictionInputNotice diagnostics={result.input_diagnostics} />
      {/* 推奨カード */}
      <div className="rounded border border-[#1e1e1e]"><BettingRecommendationPanel recommendation={rec} diagnostics={result.input_diagnostics} snapshotId={result.performance_snapshot_id} referenceBets={result.reference_bets} /></div>
      {expectation.mode === 'reference' && <p className="text-xs text-[#888]" title={REFERENCE_EXPECTATION_CAVEAT}>{REFERENCE_EXPECTATION_NOTE}</p>}

      {/* 馬一覧 */}
      <div className="space-y-2">
        {preds.map((p) => {
          const barW = Math.max(0, Math.min(100, Math.round((p.p_norm / maxReferenceRatio) * 100)))
          const isTop = p.predicted_rank === 1
          const isTop3 = p.predicted_rank <= 3
          const expectedValue = expectation.values.get(p.horse_number)
          return (
            <div
              key={p.horse_number}
              className={`p-3 rounded border ${isTop ? 'bg-[#0d1a0d] border-[#2a4a2a]' : isTop3 ? 'bg-[#0f130f] border-[#1e2a1e]' : 'bg-[#0c0c0c] border-[#1a1a1a]'}`}
            >
              <div className="flex items-center gap-3">
                <div className={`w-7 h-7 rounded-full flex items-center justify-center text-xs font-bold shrink-0 ${isTop ? 'bg-[#fbbf24] text-black' : isTop3 ? 'bg-[#333] text-white' : 'bg-[#1a1a1a] text-[#555]'}`}>
                  {p.predicted_rank}
                </div>
                <div className="w-6 text-center text-xs text-[#666]">{p.horse_number}</div>
                <div className="flex-1 min-w-0">
                  <div className="font-medium text-sm truncate">{p.horse_name}</div>
                  <div className="text-[10px] text-[#555] truncate">{p.jockey_name}{p.age ? ` · ${p.sex}${p.age}` : ''}</div>
                </div>
                <div className="flex items-center gap-4 shrink-0 text-right">
                  <div>
                    <div className="text-[10px] text-[#555]">スコア</div>
                    <div className="text-sm font-mono font-bold text-[#7dd3fc]">{(p.p_raw * 100).toFixed(1)}</div>
                  </div>
                  <div>
                    <div className="text-[10px] text-[#555]">{calibrated ? '勝率' : '参考比率'}</div>
                    <div className="text-sm font-mono">{(p.p_norm * 100).toFixed(1)}%</div>
                  </div>
                  <div>
                    <div className="text-[10px] text-[#555]">オッズ</div>
                    <div className="text-sm font-mono">{p.odds != null ? `${p.odds}倍` : '—'}</div>
                  </div>
                  <div>
                    <div className="text-[10px] text-[#555]">{expectation.label}</div>
                    <div className={`text-sm font-mono ${expectedValue === undefined ? 'text-[#555]' : expectation.mode === 'reference' ? 'text-[#888]' : expectedValue >= 1 ? 'text-[#86efac]' : 'text-[#f87171]'}`}>
                      {expectedValue !== undefined ? expectedValue.toFixed(2) : '—'}
                    </div>
                  </div>
                  <div>
                    <div className="text-[10px] text-[#555]">人気</div>
                    <div className="text-sm font-mono">{p.popularity ?? '—'}</div>
                  </div>
                </div>
              </div>
              {/* 確率バー */}
              <div className="mt-2 flex items-center gap-2">
                <div className="flex-1 bg-[#1a1a1a] rounded-full h-1.5 overflow-hidden">
                  <div
                    className={`h-full rounded-full transition-all ${probColor(p.p_norm)}`}
                    style={{ width: `${barW}%` }}
                  />
                </div>
                <span className="text-[10px] text-[#555] w-12 text-right">{(p.p_norm * 100).toFixed(1)}%</span>
              </div>
            </div>
          )
        })}
      </div>
    </div>
  )
}
