'use client'

import { useState } from 'react'
import { CartesianGrid, Line, LineChart, ReferenceLine, ResponsiveContainer, Tooltip, XAxis, YAxis } from 'recharts'
import { performanceDateLabel, performanceRoiLabel } from '@/lib/performance-records'
import { modelRoiChartRows, type ModelPerformance, type ModelRoiChartRow, type ModelRoiSeries } from '@/lib/model-roi'
import { ModelMonthlyPerformance } from '@/components/ModelMonthlyPerformance'

const yen = (value: number) => `¥${value.toLocaleString()}`
const profitLabel = (value: number) => `${value > 0 ? '+' : value < 0 ? '−' : ''}${yen(Math.abs(value))}`
const modelLabel = (model: ModelRoiSeries) => model.display_name || model.model_id || 'モデル未確認'

export function ModelRoiTooltip({ row, models }: { row: ModelRoiChartRow; models: ModelRoiSeries[] }) {
  return (
    <div className="max-w-sm space-y-3 rounded-lg border border-[#444] bg-[#161616] p-3 text-xs shadow-lg">
      <p className="text-[#aaa]">{performanceDateLabel(row.date)}{row.post_time ? ` ${row.post_time}` : ''} 時点</p>
      {models.map(model => {
        const point = row.values.get(model.model_id)
        return point && <div key={model.model_id} className="space-y-1">
          <p className="break-all font-medium">{modelLabel(model)} · {performanceRoiLabel(point.roi)}</p>
          <p className="text-[#ccc]">累計投資 {yen(point.total_cost)} · 累計払戻 {yen(point.total_return)}</p>
          <p className="text-[#ccc]">収支 {profitLabel(point.profit)} · {point.race_count}レース</p>
        </div>
      })}
    </div>
  )
}

export function ModelRoiPerformance({ data, loading }: { data: ModelPerformance | null; loading: boolean }) {
  const [selectedIds, setSelectedIds] = useState<Set<string> | null>(null)
  const [sort, setSort] = useState('roi')
  const [kpiId, setKpiId] = useState('')
  const models = [...(data?.models ?? [])].sort((a, b) => sort === 'name'
    ? modelLabel(a).localeCompare(modelLabel(b), 'ja')
    : ((sort === 'profit' ? b.profit - a.profit : (b.roi ?? -1) - (a.roi ?? -1)) || a.model_id.localeCompare(b.model_id)))
  const available = [...(data?.models ?? [])].sort((a, b) => Number(b.total_cost > 0) - Number(a.total_cost > 0))
  const defaultIds = available.slice(0, 3).map(model => model.model_id)
  const selected = models.filter(model => selectedIds === null ? defaultIds.includes(model.model_id) : selectedIds.has(model.model_id))
  const rows = modelRoiChartRows(selected)
  const fallbackKpi = available.find(model => selected.some(item => item.model_id === model.model_id))
  const kpi = selected.find(model => model.model_id === (kpiId || defaultIds[0])) || fallbackKpi
  const cost = kpi?.total_cost ?? 0
  const returned = kpi?.total_return ?? 0
  const races = kpi?.race_count ?? 0
  const roi = cost > 0 ? returned / cost * 100 : null
  const cards = [
    { label: '累計投資', value: cost > 0 ? yen(cost) : '—' },
    { label: '累計払戻', value: cost > 0 ? yen(returned) : '—' },
    { label: '収支', value: cost > 0 ? profitLabel(returned - cost) : '—' },
    { label: '累積回収率', value: performanceRoiLabel(roi) },
  ]
  const cohortLabels = [
    { key: 'evaluation', label: '学習時TEST評価' },
    { key: 'historical', label: '保存データによる事後検証' },
    { key: 'pre_race', label: '発走前に保存した運用予測' },
  ] as const
  return (
    <>
      <section aria-label="確定分の成績" className="space-y-2">
        <div className="flex flex-wrap items-center justify-between gap-2 text-xs text-[#aaa]">
          <label className="flex min-w-0 items-center gap-2">KPIモデル
            <select aria-label="KPIモデル" value={kpi?.model_id || ''} onChange={event => setKpiId(event.target.value)} className="max-w-[65vw] rounded border border-[#333] bg-[#111] px-2 py-1 sm:max-w-md" disabled={selected.length === 0}>
              {selected.length === 0 && <option value="">未選択</option>}
              {selected.map(model => <option key={model.model_id} value={model.model_id}>{modelLabel(model)}</option>)}
            </select>
          </label>
          <span>対象 {races}レース · 確定分のみ · モデルは合算しません</span>
        </div>
        <div className="grid grid-cols-2 gap-2 sm:grid-cols-4">
          {cards.map((card, index) => <div key={card.label} className={`rounded-lg border px-3 py-2 ${index === 3 ? 'border-[#32677e] bg-[#0c202a]' : 'border-[#252525] bg-[#111]'}`}>
            <div className="mb-1 text-[11px] text-[#aaa]">{card.label}</div>
            <div className={`font-semibold tabular-nums ${index === 3 ? 'text-3xl text-[#7dd3fc]' : 'text-xl'}`}>{card.value}</div>
          </div>)}
        </div>
      </section>
      <section aria-label="成績の内訳" className="space-y-2 rounded-lg border border-[#252525] bg-[#111] p-3 text-xs">
        <h2 className="font-medium">成績の内訳 · {kpi ? modelLabel(kpi) : 'モデル未選択'}</h2>
        {kpi?.cohorts ? <div className="overflow-x-auto">
          <table className="w-full whitespace-nowrap text-right text-[#aaa]">
            <thead><tr>{['記録区分', '確定レース', '投資', '払戻', '回収率', '未精算'].map(label => <th key={label} className="px-2 py-1 font-normal">{label}</th>)}</tr></thead>
            <tbody>{cohortLabels.map(({ key, label }) => {
              const cohort = kpi.cohorts![key]
              return <tr key={key} className="border-t border-[#252525]">
                <th scope="row" className="px-2 py-1 text-left font-normal text-[#ddd]">{label}</th>
                <td className="px-2 py-1">{cohort.race_count}</td>
                <td className="px-2 py-1">{cohort.total_cost > 0 ? yen(cohort.total_cost) : '—'}</td>
                <td className="px-2 py-1">{cohort.total_cost > 0 ? yen(cohort.total_return) : '—'}</td>
                <td className="px-2 py-1">{performanceRoiLabel(cohort.roi)}</td>
                <td className="px-2 py-1">{cohort.pending_races}</td>
              </tr>
            })}</tbody>
          </table>
        </div> : <p className="text-[#888]">記録区分の内訳は未取得です。</p>}
        {kpi?.status_counts && <p className="text-[#aaa]">結果待ち {kpi.status_counts.pending} · 払戻待ち {kpi.status_counts.payout_pending} · 要確認 {kpi.status_counts.review + kpi.status_counts.other} · 見送り {kpi.status_counts.skipped}</p>}
        <p className="text-[#888]">未精算は外れ0円にせず、回収率・グラフから除外します。運用予測は保存後、結果・公式払戻が揃うと更新時に自動反映されます。</p>
      </section>
      <section aria-label="表示モデル" className="flex flex-wrap items-center justify-between gap-2 text-xs">
        <fieldset className="flex min-w-0 flex-wrap gap-x-4 gap-y-2">
          <legend className="sr-only">表示モデル</legend>
          {models.map(model => <label key={model.model_id} className="flex min-w-0 cursor-pointer items-center gap-2">
            <input type="checkbox" checked={selected.includes(model)} onChange={() => {
              const next = new Set(selected.map(item => item.model_id))
              if (next.has(model.model_id)) next.delete(model.model_id)
              else next.add(model.model_id)
              setSelectedIds(next)
              if (!next.has(kpi?.model_id || '')) setKpiId(available.find(item => next.has(item.model_id))?.model_id || '')
            }} className="accent-[#60a5fa]" />
            <span aria-hidden="true" className="h-2 w-2 shrink-0 rounded-full" style={{ backgroundColor: model.color }} />
            <span className="max-w-72 truncate text-[#ddd]" title={modelLabel(model)}>{modelLabel(model)}</span>
          </label>)}
        </fieldset>
        <div className="flex flex-wrap items-center gap-2 text-[#aaa]">
          <span>表示中：{selected.length}モデル</span>
          <button onClick={() => setSelectedIds(new Set(models.map(model => model.model_id)))} className="hover:text-white">全モデル表示</button>
          <button onClick={() => setSelectedIds(new Set())} className="hover:text-white">全解除</button>
          <select aria-label="モデルの並び順" value={sort} onChange={event => setSort(event.target.value)} className="rounded border border-[#333] bg-[#111] px-2 py-1">
            <option value="roi">回収率が高い順</option><option value="profit">収支が高い順</option><option value="name">モデル名順</option>
          </select>
        </div>
      </section>
      <div className="grid min-w-0 gap-3 lg:grid-cols-2">
      <section aria-label="モデル別回収率推移" className="min-w-0 space-y-2 rounded-lg border border-[#252525] bg-[#111] p-3">
        <div className="flex flex-wrap items-center justify-between gap-2">
          <h1 className="text-sm font-medium">累積回収率</h1>
          <span className="text-[10px] text-[#888]">{data?.date_from || data?.date_to ? '指定期間の推移' : '全期間の推移'}</span>
        </div>
        {!data ? <p role="status" className="py-12 text-center text-sm text-[#888]">{loading ? '読み込み中...' : 'モデル別集計を取得できません。APIを更新して再読み込みしてください。'}</p>
          : models.length === 0 ? <p className="py-12 text-center text-sm text-[#888]">投資・払戻が確定したAI参考の記録がありません。</p>
            : selected.length === 0 ? <p className="py-12 text-center text-sm text-[#888]">表示モデルを選択してください。</p>
              : rows.length === 0 ? <p className="flex h-[clamp(180px,28vh,280px)] items-center justify-center text-xs text-[#888]">結果・公式払戻の確定を待っています。</p>
              : <div role="img" aria-label="モデル別累積回収率グラフ" className="h-[clamp(180px,28vh,280px)] min-w-0">
                <ResponsiveContainer width="100%" height="100%">
                  <LineChart data={rows} margin={{ top: 24, right: 12, bottom: 8, left: 8 }} accessibilityLayer>
                    <CartesianGrid stroke="#292929" strokeDasharray="3 3" />
                    <XAxis dataKey="event" tickFormatter={value => String(value).slice(0, 10).replace(/-/g, '/')} stroke="#888" tick={{ fontSize: 11 }} minTickGap={35} height={45} label={{ value: '日付', position: 'insideBottomRight', offset: -3, fill: '#888', fontSize: 11 }} />
                    <YAxis domain={[0, (max: number) => Math.max(120, Math.ceil(max / 50) * 50)]} tickFormatter={value => `${value}%`} stroke="#888" tick={{ fontSize: 11 }} width={65} />
                    <ReferenceLine y={100} stroke="#aaa" strokeDasharray="6 4" label={{ value: '100% 損益分岐', position: 'insideTopRight', fill: '#aaa', fontSize: 11 }} />
                    <Tooltip content={({ active, payload }) => {
                      const row = payload?.[0]?.payload as ModelRoiChartRow | undefined
                      return active && row ? <ModelRoiTooltip row={row} models={selected} /> : null
                    }} />
                    {selected.map(model => <Line key={model.model_id} name={modelLabel(model)} type="linear"
                      dataKey={(row: ModelRoiChartRow) => row.values.get(model.model_id)?.roi}
                      stroke={model.color} strokeWidth={2}
                      dot={rows.length <= 60 ? { r: 3 } : false} activeDot={{ r: 5 }} isAnimationActive={false} connectNulls={false} />)}
                  </LineChart>
                </ResponsiveContainer>
              </div>}
        <p className="text-[10px] text-[#777]">累計払戻÷累計投資×100。日次累積・モデル別の独立集計です。</p>
      </section>
      {data && <ModelMonthlyPerformance models={selected} years={data.years || []} dateFrom={data.date_from || ''} dateTo={data.date_to || ''} origin={data.origin || 'all'} revision={data} />}
      </div>
      <p className="text-[10px] leading-relaxed text-[#888]">固定②3点×各500円の仮想購入（勝負候補の選別なし）。{data?.origin === 'combined' || data?.origin === 'historical_validation'
        ? '学習レースは除外。保存済みTEST評価・事後検証を含み、全成績では発走前の運用予測も集計します。月別seed42・年次前進モデルはTRAIN・CAL除外。従来モデルの事後検証には校正・評価に使用した期間も含みます。seed42は未来側も学習したランダム分割です。完全な未知データの成績ではなく、対象期間が異なるモデルの回収率は単純比較できません。'
        : '学習・校正・評価期間外のみ。'}事後再予測を含み、発走前取得・将来利益は保証しません。実購入とは別集計です。見送り・未精算分は除外します。</p>
    </>
  )
}
