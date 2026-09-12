'use client'

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
}

export type ModelEvaluation = {
  primary?: EvaluationPrimary
  details?: EvaluationDetails
  time_slices?: TimeSlice[]
}

type Props = {
  evaluation?: ModelEvaluation | null
  target?: string
  legacyAuc?: MetricValue
  legacyLogloss?: MetricValue
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
  return `${details.evaluation_date_from || '?'}〜${details.evaluation_date_to || '?'}`
}

export function ModelEvaluationSummary({ evaluation, target, legacyAuc, legacyLogloss }: Props) {
  const primary: EvaluationPrimary = evaluation?.primary ?? (
    target === 'speed_deviation'
      ? { rank_correlation: legacyAuc, rmse: legacyLogloss }
      : {}
  )
  const details = evaluation?.details ?? {}
  const timeSlices = Array.isArray(evaluation?.time_slices) ? evaluation.time_slices : []
  const delta = finite(primary.top_pick_win_rate_delta)

  const mainMetrics = [
    { label: '順位相関', value: decimal(primary.rank_correlation) },
    { label: '予測誤差', hint: 'RMSE', value: decimal(primary.rmse) },
    { label: '1位推奨の勝率', value: percent(primary.top_pick_win_rate) },
    {
      label: '人気1位との差',
      value: points(primary.top_pick_win_rate_delta),
      tone: delta === null ? '' : delta >= 0 ? 'text-[#4ade80]' : 'text-[#fb923c]',
    },
    { label: '回収率', value: returnRate(primary.win_roi) },
  ]

  return (
    <div className="space-y-3" data-testid="model-evaluation-summary">
      <div className="grid grid-cols-2 gap-2 md:grid-cols-5">
        {mainMetrics.map(metric => (
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

      <details className="rounded-lg border border-[#202020] bg-[#0c0c0c] px-4 py-3">
        <summary className="cursor-pointer text-xs text-[#888] hover:text-white">詳細</summary>
        <div className="mt-4 grid grid-cols-2 gap-3 text-xs md:grid-cols-4">
          <div><div className="text-[#555]">平均誤差 (MAE)</div><div className="mt-1 text-white">{decimal(details.mae)}</div></div>
          <div><div className="text-[#555]">説明率 (R²)</div><div className="mt-1 text-white">{decimal(details.r2)}</div></div>
          <div><div className="text-[#555]">検証期間</div><div className="mt-1 text-white">{period(details)}</div></div>
          <div><div className="text-[#555]">検証レース</div><div className="mt-1 text-white">{details.evaluation_race_count?.toLocaleString() ?? '未計測'}</div></div>
        </div>

        {timeSlices.length > 0 && (
          <div className="mt-4 overflow-x-auto">
            <div className="mb-2 text-xs text-[#666]">時系列別成績</div>
            <table className="w-full min-w-[480px] text-left text-xs">
              <thead className="text-[#555]">
                <tr>
                  <th className="pb-2 font-normal">期間</th>
                  <th className="pb-2 text-right font-normal">レース</th>
                  <th className="pb-2 text-right font-normal">勝率</th>
                  <th className="pb-2 text-right font-normal">人気差</th>
                  <th className="pb-2 text-right font-normal">回収率</th>
                </tr>
              </thead>
              <tbody className="divide-y divide-[#1c1c1c] text-[#bbb]">
                {timeSlices.map(slice => (
                  <tr key={slice.period}>
                    <td className="py-2">{slice.period}</td>
                    <td className="py-2 text-right tabular-nums">{slice.race_count?.toLocaleString() ?? '—'}</td>
                    <td className="py-2 text-right tabular-nums">{percent(slice.top_pick_win_rate)}</td>
                    <td className="py-2 text-right tabular-nums">{points(slice.top_pick_win_rate_delta)}</td>
                    <td className="py-2 text-right tabular-nums">{returnRate(slice.win_roi)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}

        <p className="mt-4 text-[10px] leading-5 text-[#555]">
          回収率は1位推奨へ毎回100円を投じた単勝払い戻し基準です。
        </p>
      </details>
    </div>
  )
}
