'use client'

import { ModelVerificationStatus } from '@/components/ModelVerificationStatus'

type MetricValue = number | null | undefined

type EvaluationPrimary = {
  rank_correlation?: MetricValue
  rmse?: MetricValue
  top_pick_win_rate?: MetricValue
  favorite_win_rate?: MetricValue
  top_pick_win_rate_delta?: MetricValue
  win_roi?: MetricValue
}

type EvaluationDetails = {
  mae?: MetricValue
  r2?: MetricValue
  evaluation_date_from?: string | null
  evaluation_date_to?: string | null
  evaluation_race_count?: number | null
}

type TimeSlice = {
  period?: string
  race_count?: number
  top_pick_win_rate?: MetricValue
  top_pick_win_rate_delta?: MetricValue
  win_roi?: MetricValue
  rank_correlation?: MetricValue
  rmse?: MetricValue
}

export type ProbabilityValidation = {
  status?: string
  calibrated?: boolean
  validated?: boolean
  eligible_for_ev?: boolean
  market_edge_verified?: boolean
  profitability_verified?: boolean
  metrics?: { nll?: MetricValue; brier?: MetricValue; selection_ece?: MetricValue }
  reason_codes?: string[]
  evaluation_date_from?: string | null
  evaluation_date_to?: string | null
  validation_period_start?: string | null
  validation_period_end?: string | null
  race_count?: number | null
}

export type ModelEvaluation = {
  rmse_unit?: string
  evaluation_label?: string
  primary?: EvaluationPrimary
  details?: EvaluationDetails
  time_slices?: TimeSlice[]
  probability_validation?: ProbabilityValidation | null
}

type Props = {
  evaluation?: ModelEvaluation | null
  target?: string
  legacyAuc?: MetricValue
  legacyLogloss?: MetricValue
  featurePipeline?: string | null
  probabilityValidation?: ProbabilityValidation | null
  trainingTimeSeconds?: MetricValue
  trainingParallelWorkers?: MetricValue
  nativeThreads?: MetricValue
}

function finite(value: MetricValue): number | null {
  return typeof value === 'number' && Number.isFinite(value) ? value : null
}

function decimal(value: MetricValue, digits = 3) {
  const number = finite(value)
  return number === null ? '未計測' : number.toFixed(digits)
}

function percent(value: MetricValue) {
  const number = finite(value)
  return number === null ? '未計測' : `${(number * 100).toFixed(1)}%`
}

function points(value: MetricValue) {
  const number = finite(value)
  if (number === null) return '未計測'
  const amount = number * 100
  return `${amount > 0 ? '+' : ''}${amount.toFixed(1)}pt`
}

function returnRate(value: MetricValue) {
  const number = finite(value)
  return number === null ? '未計測' : `${number.toFixed(1)}%`
}

function period(details: EvaluationDetails) {
  if (!details.evaluation_date_from && !details.evaluation_date_to) return '未計測'
  const date = (value?: string | null) => value?.replace(/^(\d{4})(\d{2})(\d{2})$/, '$1/$2/$3') || '?'
  return `${date(details.evaluation_date_from)}〜${date(details.evaluation_date_to)}`
}

function duration(value: number) {
  const seconds = Math.round(value)
  return seconds < 60 ? `${seconds}秒` : `${Math.floor(seconds / 60)}分${seconds % 60}秒`
}

function positiveInteger(value: MetricValue) {
  const number = finite(value)
  return number !== null && Number.isInteger(number) && number > 0 ? number : null
}

export function ModelEvaluationSummary({
  evaluation, target, legacyAuc, legacyLogloss, featurePipeline, probabilityValidation,
  trainingTimeSeconds, trainingParallelWorkers, nativeThreads,
}: Props) {
  const primary: EvaluationPrimary = evaluation?.primary ?? (
    target === 'speed_deviation'
      ? { rank_correlation: legacyAuc, rmse: legacyLogloss }
      : {}
  )
  const details = evaluation?.details ?? {}
  const timeSlices = Array.isArray(evaluation?.time_slices) ? evaluation.time_slices : []
  const delta = finite(primary.top_pick_win_rate_delta)
  const winNllReference = featurePipeline === 'local-win-nll-reference-v1'
  const optimizedPipeline = featurePipeline === 'local-db-shared-optimized-v1'
  const contextPipeline = featurePipeline === 'local-db-context-v1'
  const sharedPipeline = featurePipeline === 'local-db-shared-v1' || optimizedPipeline || contextPipeline
  const accuracyOnly = sharedPipeline || winNllReference
  const rmseUnit = winNllReference ? '勝敗0/1' : evaluation?.rmse_unit ?? (sharedPipeline ? 'm/s' : undefined)
  const validation = probabilityValidation ?? evaluation?.probability_validation
  const calibrated = validation?.calibrated === true
  const probabilityVerified = !winNllReference && !contextPipeline && calibrated && validation?.validated === true && validation?.status === 'validated'
  const calibrationLabel = probabilityVerified ? '検証済み' : calibrated ? '校正済み・未検証' : '未校正'
  const evCalibrationEligible = probabilityVerified && validation?.eligible_for_ev === true
  const elapsed = finite(trainingTimeSeconds)
  const parallelWorkers = positiveInteger(trainingParallelWorkers)
  const threads = positiveInteger(nativeThreads)

  const mainMetrics = [
    { label: '順位相関', value: decimal(primary.rank_correlation) },
    { label: '予測誤差', hint: rmseUnit ? `RMSE ${rmseUnit}` : 'RMSE', value: decimal(primary.rmse) },
    { label: '1位推奨の勝率', value: percent(primary.top_pick_win_rate) },
    {
      label: '人気1位との差',
      value: points(primary.top_pick_win_rate_delta),
      tone: delta === null ? '' : delta >= 0 ? 'text-[#4ade80]' : 'text-[#fb923c]',
    },
    { label: '回収率', value: returnRate(primary.win_roi) },
  ]
  const displayedMetrics = accuracyOnly ? mainMetrics.slice(0, 2) : mainMetrics
  const verificationEvidence = winNllReference || contextPipeline ? {
    ...validation, status: 'unverified', validated: false,
    market_edge_verified: false, profitability_verified: false,
  } : validation

  return (
    <div className="space-y-3" data-testid="model-evaluation-summary">
      {accuracyOnly && (
        <p className="text-xs text-[#888]" data-testid="model-feature-profile">
          {winNllReference ? '46特徴量＋市場4項目（50項目）' : contextPipeline ? '条件・展開80項目（比較候補）' : optimizedPipeline ? '候補46項目' : '基準47項目'}
        </p>
      )}
      {winNllReference && (
        <p className="rounded border border-[#665120] bg-[#211b0d] px-3 py-2 text-xs leading-5 text-[#fde68a]" data-testid="win-nll-reference-state">
          参考運用 · 発走前オッズ・未知期間は未検証 · 購入判断未承認
        </p>
      )}
      {contextPipeline && (
        <p className="rounded border border-[#665120] bg-[#211b0d] px-3 py-2 text-xs leading-5 text-[#fde68a]" data-testid="context-model-reference-state">
          条件・展開の比較候補 · 校正と予測精度を比較します · 購入判断未承認
        </p>
      )}
      <div className={`grid grid-cols-2 gap-2 ${accuracyOnly ? '' : 'md:grid-cols-5'}`} data-testid="model-primary-metrics">
        {displayedMetrics.map(metric => (
          <div key={metric.label} className="rounded-lg border border-[#222] bg-[#0a0a0a] px-3 py-3">
            <div className="text-[11px] text-[#666]">
              {metric.label}{metric.hint && <span className="ml-1 text-[#444]">({metric.hint})</span>}
            </div>
            <div className={`mt-1 text-lg font-semibold tabular-nums ${metric.tone || 'text-white'}`}>
              {metric.value}
            </div>
          </div>
        ))}
      </div>

      {(accuracyOnly || validation) && (
        <p className="text-xs text-[#888]" data-testid="model-calibration-state">
          勝率校正：{calibrationLabel}<span className="mx-2 text-[#444]">·</span>買い目・回収率は別評価
        </p>
      )}

      <ModelVerificationStatus evidence={verificationEvidence} />

      <details className="rounded-lg border border-[#202020] bg-[#0c0c0c] px-4 py-3">
        <summary className="cursor-pointer text-xs text-[#888] hover:text-white">詳細</summary>
        {evaluation?.evaluation_label && <p className="mt-3 text-xs text-[#888]">{evaluation.evaluation_label}</p>}
        {optimizedPipeline && <p className="mt-3 text-xs text-[#888]">直近5走の複勝圏率を除外した候補です。安定した優位性は未確認です。</p>}
        <div className="mt-4 grid grid-cols-2 gap-3 text-xs md:grid-cols-4">
          {!winNllReference && <div><div className="text-[#555]">平均誤差 (MAE{rmseUnit ? ` ${rmseUnit}` : ''})</div><div className="mt-1 text-white">{decimal(details.mae)}</div></div>}
          {!winNllReference && <div><div className="text-[#555]">説明率 (R²)</div><div className="mt-1 text-white">{decimal(details.r2)}</div></div>}
          <div><div className="text-[#555]">検証期間</div><div className="mt-1 text-white">{period(details)}</div></div>
          <div><div className="text-[#555]">検証レース</div><div className="mt-1 text-white">{details.evaluation_race_count?.toLocaleString() ?? '未計測'}</div></div>
        </div>

        {(elapsed !== null && elapsed >= 0 || parallelWorkers !== null || threads !== null) && (
          <div className="mt-4 flex flex-wrap gap-x-5 gap-y-2 text-xs text-[#888]" data-testid="model-training-runtime">
            {elapsed !== null && elapsed >= 0 && <span>学習時間 <span className="text-white">{duration(elapsed)}</span></span>}
            {parallelWorkers !== null && <span>並列 <span className="text-white">{parallelWorkers}候補</span></span>}
            {threads !== null && <span>CPU <span className="text-white">{threads}スレッド/候補</span></span>}
          </div>
        )}

        {(accuracyOnly || validation) && (
          <div className="mt-4 border-t border-[#202020] pt-3" data-testid="model-probability-validation">
            <div className="text-xs text-[#888]">勝率校正・検証（順位評価とは別）</div>
            <div className="mt-3 grid grid-cols-2 gap-3 text-xs md:grid-cols-4">
              <div><div className="text-[#555]">NLL</div><div className="mt-1 text-white">{decimal(validation?.metrics?.nll)}</div></div>
              <div><div className="text-[#555]">Brier</div><div className="mt-1 text-white">{decimal(validation?.metrics?.brier)}</div></div>
              <div><div className="text-[#555]">校正誤差 (ECE)</div><div className="mt-1 text-white">{decimal(validation?.metrics?.selection_ece)}</div></div>
              <div><div className="text-[#555]">EV用の確率条件</div><div className="mt-1 text-white">{evCalibrationEligible ? '充足' : '未達'}</div></div>
            </div>
            <p className="mt-3 text-[10px] leading-5 text-[#555]">買い目の判断には、発走前の入力・オッズも別途確認します。</p>
            <p className="mt-1 text-[10px] leading-5 text-[#555]">EV用の確率条件は購入の許可ではありません。購入対象の校正と戦略は別に検証します。</p>
          </div>
        )}

        {timeSlices.length > 0 && (
          <div className="mt-4 overflow-x-auto">
            <div className="mb-2 text-xs text-[#666]">時系列別成績</div>
            <table className="w-full min-w-[480px] text-left text-xs">
              <thead className="text-[#555]">
                <tr>
                  <th className="pb-2 font-normal">期間</th>
                  <th className="pb-2 text-right font-normal">レース</th>
                  <th className="pb-2 text-right font-normal">順位相関</th>
                  <th className="pb-2 text-right font-normal">RMSE{rmseUnit ? ` (${rmseUnit})` : ''}</th>
                  {!accuracyOnly && <th className="pb-2 text-right font-normal">勝率</th>}
                  {!accuracyOnly && <th className="pb-2 text-right font-normal">人気差</th>}
                  {!accuracyOnly && <th className="pb-2 text-right font-normal">回収率</th>}
                </tr>
              </thead>
              <tbody className="divide-y divide-[#1c1c1c] text-[#bbb]">
                {timeSlices.map(slice => (
                  <tr key={slice.period}>
                    <td className="py-2">{slice.period}</td>
                    <td className="py-2 text-right tabular-nums">{slice.race_count?.toLocaleString() ?? '—'}</td>
                    <td className="py-2 text-right tabular-nums">{decimal(slice.rank_correlation)}</td>
                    <td className="py-2 text-right tabular-nums">{decimal(slice.rmse)}</td>
                    {!accuracyOnly && <td className="py-2 text-right tabular-nums">{percent(slice.top_pick_win_rate)}</td>}
                    {!accuracyOnly && <td className="py-2 text-right tabular-nums">{points(slice.top_pick_win_rate_delta)}</td>}
                    {!accuracyOnly && <td className="py-2 text-right tabular-nums">{returnRate(slice.win_roi)}</td>}
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}

        <p className="mt-4 text-[10px] leading-5 text-[#555]">
          {winNllReference
            ? 'RMSEは勝敗0/1に対する勝率予測の誤差です。速度RMSEとは直接比較できません。過去の最終オッズによる評価であり、未知期間の購入成績ではありません。'
            : sharedPipeline
            ? '順位相関は高いほど、RMSEは小さいほど良好です。回収率は買い目の条件と分けて評価します。'
            : evaluation?.rmse_unit === 'm/s' ? '速度RMSEはm/s。回収率と勝率校正は別評価です。' : '回収率は1位推奨へ毎回100円を投じた単勝払い戻し基準です。'}
        </p>
      </details>
    </div>
  )
}
