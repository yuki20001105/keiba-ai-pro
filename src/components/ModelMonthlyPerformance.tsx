'use client'

import { useEffect, useState } from 'react'
import { CartesianGrid, Line, LineChart, ReferenceLine, ResponsiveContainer, Tooltip, XAxis, YAxis } from 'recharts'
import { authFetch } from '@/lib/auth-fetch'
import { performanceRoiLabel } from '@/lib/performance-records'
import { readMonthlyPerformance, type ModelMonthlyPerformance as MonthlyData, type ModelRoiSeries, type PaperOrigin } from '@/lib/model-roi'

const yen = (value: number) => `¥${value.toLocaleString()}`
const profit = (value: number) => `${value > 0 ? '+' : value < 0 ? '−' : ''}${yen(Math.abs(value))}`

export function ModelMonthlyPerformance({ models, years, dateFrom, dateTo, origin, revision }: {
  models: ModelRoiSeries[]; years: string[]; dateFrom: string; dateTo: string; origin: PaperOrigin; revision: unknown
}) {
  const [year, setYear] = useState(Number(years.at(-1) || new Date().getFullYear()))
  const [view, setView] = useState<'graph' | 'table'>('graph')
  const [half, setHalf] = useState(0)
  const [result, setResult] = useState<{ key: string; data?: MonthlyData; error?: string } | null>(null)
  const [loading, setLoading] = useState(false)
  const key = `${year}|${dateFrom}|${dateTo}|${origin}`
  const yearOptions = [...new Set([...years.map(Number), year])].sort((a, b) => a - b)
  useEffect(() => {
    let active = true
    const abort = new AbortController()
    setLoading(true)
    const params = new URLSearchParams({ mode: 'paper', source: 'simulation', origin, year: String(year) })
    if (dateFrom) params.set('date_from', dateFrom.replace(/-/g, ''))
    if (dateTo) params.set('date_to', dateTo.replace(/-/g, ''))
    authFetch(`/api/performance?${params}`, { signal: AbortSignal.any([abort.signal, AbortSignal.timeout(20_000)]) })
      .then(async response => {
        const body = await response.json()
        if (!response.ok) throw new Error(typeof body.detail === 'string' ? body.detail : '月別成績を取得できません。')
        const data = readMonthlyPerformance(body, year, dateFrom, dateTo, origin)
        if (!data) throw new Error('月別集計の条件を確認できません。APIを更新してください。')
        if (active) setResult({ key, data })
      })
      .catch(error => { if (active) setResult({ key, error: error instanceof Error ? error.message : '月別成績を取得できません。' }) })
      .finally(() => { if (active) setLoading(false) })
    return () => { active = false; abort.abort() }
  }, [year, dateFrom, dateTo, origin, key, revision])
  const current = result?.key === key && !loading ? result : null
  const monthly = models.map(model => current?.data?.models.find(item => item.model_id === model.model_id))
  const complete = current?.data && monthly.every((model, index) => model && model.color === models[index].color)
  const rows = Array.from({ length: 12 }, (_, index) => ({
    month: index + 1,
    values: new Map(monthly.flatMap(model => model ? [[model.model_id, model.months[index]] as const] : [])),
  }))
  const moveYear = (value: number) => { setYear(value); setHalf(0) }
  return <section aria-label="月別回収率" className="min-w-0 space-y-2 rounded-lg border border-[#252525] bg-[#111] p-3">
    <div className="flex flex-wrap items-center justify-between gap-2">
      <h2 className="text-sm font-medium">月別回収率</h2>
      <div className="flex items-center gap-1 text-xs">
        <button aria-label="前年" disabled={year <= 1900} onClick={() => moveYear(year - 1)} className="rounded border border-[#333] px-2 py-1 disabled:opacity-30">‹</button>
        <select aria-label="表示年" value={year} onChange={event => moveYear(Number(event.target.value))} className="rounded border border-[#333] bg-[#111] px-2 py-1">
          {yearOptions.map(value => <option key={value} value={value}>{value}年</option>)}
        </select>
        <button aria-label="翌年" disabled={year >= 2200} onClick={() => moveYear(year + 1)} className="rounded border border-[#333] px-2 py-1 disabled:opacity-30">›</button>
        {(['graph', 'table'] as const).map(item => <button key={item} aria-pressed={view === item} onClick={() => setView(item)}
          className={`rounded px-2 py-1 ${view === item ? 'bg-[#333] text-white' : 'text-[#888]'}`}>{item === 'graph' ? 'グラフ' : 'データ一覧'}</button>)}
      </div>
    </div>
    {models.length === 0 ? <p className="flex h-[clamp(180px,28vh,280px)] items-center justify-center text-xs text-[#888]">表示モデルを選択してください。</p>
      : !current ? <p role="status" className="flex h-[clamp(180px,28vh,280px)] items-center justify-center text-xs text-[#888]">月別成績を読み込み中...</p>
        : current.error || !complete ? <p role="alert" className="flex h-[clamp(180px,28vh,280px)] items-center justify-center text-xs text-[#fca5a5]">{current.error || '選択モデルの月別集計を確認できません。'}</p>
          : view === 'graph' ? <div role="img" aria-label="モデル別月別回収率グラフ" className="h-[clamp(180px,28vh,280px)] min-w-0">
            <ResponsiveContainer width="100%" height="100%">
              <LineChart data={rows} margin={{ top: 20, right: 12, bottom: 0, left: 0 }} accessibilityLayer>
                <CartesianGrid stroke="#292929" strokeDasharray="3 3" />
                <XAxis dataKey="month" ticks={[1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11, 12]} tickFormatter={value => `${value}月`} tick={{ fontSize: 10 }} stroke="#888" minTickGap={0} />
                <YAxis domain={[0, (max: number) => Math.max(120, Math.ceil(max / 50) * 50)]} tickFormatter={value => `${value}%`} width={58} tick={{ fontSize: 10 }} stroke="#888" />
                <ReferenceLine y={100} stroke="#aaa" strokeDasharray="6 4" label={{ value: '100% 損益分岐', position: 'insideTopRight', fill: '#aaa', fontSize: 10 }} />
                <Tooltip content={({ active, payload }) => {
                  const row = payload?.[0]?.payload as (typeof rows)[number] | undefined
                  return active && row ? <div className="max-w-[min(320px,80vw)] break-all rounded border border-[#444] bg-[#161616] p-3 text-xs">
                    <p className="mb-2">{year}年{row.month}月</p>
                    {models.map(model => {
                      const point = row.values.get(model.model_id)
                      return point && point.roi !== null ? <div key={model.model_id} className="mb-2">
                        <p>{model.display_name || model.model_id} · {performanceRoiLabel(point.roi)}</p>
                        <p>投資 {yen(point.total_cost)} · 払戻 {yen(point.total_return)}</p>
                        <p>収支 {profit(point.profit)} · {point.race_count}レース</p>
                      </div> : null
                    })}
                  </div> : null
                }} />
                {models.map(model => <Line key={model.model_id} name={model.display_name || model.model_id} type="linear"
                  dataKey={(row: (typeof rows)[number]) => row.values.get(model.model_id)?.roi}
                  stroke={model.color} strokeWidth={2} dot={{ r: 3 }} connectNulls={false} isAnimationActive={false} />)}
              </LineChart>
            </ResponsiveContainer>
          </div>
            : <div className="h-[clamp(180px,28vh,280px)] overflow-auto">
              <div className="mb-1 flex gap-2 text-[10px]">
                {[0, 1].map(value => <button key={value} aria-pressed={half === value} onClick={() => setHalf(value)} className={`rounded px-2 py-1 ${half === value ? 'bg-[#333]' : 'text-[#888]'}`}>{value === 0 ? '前半（1〜6月）' : '後半（7〜12月）'}</button>)}
              </div>
              <table className="w-full whitespace-nowrap text-right text-[11px]">
                <thead className="sticky top-0 bg-[#111] text-[#aaa]"><tr>{['月', 'モデル', '投資', '払戻', '収支', '回収率'].map(label => <th key={label} className="px-1 py-1 font-normal">{label}</th>)}</tr></thead>
                <tbody>{rows.slice(half * 6, half * 6 + 6).flatMap(row => models.map(model => {
                  const point = row.values.get(model.model_id)
                  const settled = point && point.total_cost > 0
                  return <tr key={`${row.month}-${model.model_id}`} className="border-t border-[#252525]">
                    <td className="px-1 py-1">{row.month}月</td>
                    <td className="max-w-28 truncate px-1 py-1 text-left" title={model.display_name || model.model_id}><span style={{ color: model.color }}>{model.display_name || model.model_id}</span></td>
                    <td className="px-1 py-1">{settled ? yen(point.total_cost) : '—'}</td>
                    <td className="px-1 py-1">{settled ? yen(point.total_return) : '—'}</td>
                    <td className="px-1 py-1">{settled ? profit(point.profit) : '—'}</td>
                    <td className="px-1 py-1">{settled ? performanceRoiLabel(point.roi) : '—'}</td>
                  </tr>
                }))}</tbody>
              </table>
            </div>}
    <p className="text-[10px] text-[#777]">当月の払戻÷当月の投資×100。データのない月は欠測（—）です。</p>
  </section>
}
