'use client'

import { useEffect, useState } from 'react'
import {
  CartesianGrid, Legend, Line, LineChart, ReferenceLine, ResponsiveContainer, Tooltip, XAxis, YAxis,
} from 'recharts'
import { authFetch } from '@/lib/auth-fetch'
import { performanceRoiLabel } from '@/lib/performance-records'
import { readResearchPerformance, type ResearchPerformance, type ResearchScope } from '@/lib/research-performance'

const yen = (amount: number) => `¥${amount.toLocaleString()}`

export function ResearchBettingPerformance({ dateFrom, dateTo }: { dateFrom: string; dateTo: string }) {
  const [policy, setPolicy] = useState('fixed_two__all')
  const [scope, setScope] = useState<ResearchScope>('retrospective')
  const [data, setData] = useState<ResearchPerformance | null>(null)
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState('')
  useEffect(() => {
    let active = true
    setLoading(true)
    setError('')
    const query = new URLSearchParams({ policy, scope })
    if (dateFrom) query.set('date_from', dateFrom.replace(/-/g, ''))
    if (dateTo) query.set('date_to', dateTo.replace(/-/g, ''))
    authFetch(`/api/performance/research?${query}`, { signal: AbortSignal.timeout(20_000) })
      .then(async response => {
        const parsed = response.ok ? readResearchPerformance(await response.json(), policy, scope, dateFrom, dateTo) : null
        if (!parsed) throw new Error('検証済みの研究成績を確認できませんでした。')
        if (active) setData(parsed)
      })
      .catch(() => { if (active) { setData(null); setError('検証済みの研究成績を確認できませんでした。') } })
      .finally(() => { if (active) setLoading(false) })
    return () => { active = false }
  }, [policy, scope, dateFrom, dateTo])

  const current = !loading && !error && data?.policy === policy && data.scope === scope
    && data.date_from === (dateFrom || null) && data.date_to === (dateTo || null) ? data : null
  const selected = current?.policies.find(row => row.id === policy)
  const stats = selected?.[scope]
  return (
    <section aria-label="101特徴量モデル・買い目比較（研究）" className="space-y-3 rounded-lg border border-[#333] bg-[#111] p-4">
      <h2 className="text-sm font-semibold">101特徴量モデル・買い目比較（研究）</h2>
      <p className="text-xs leading-relaxed text-[#aaa]">学習外の保存予測による14方式の仮想比較です。上の運用予測・購入済み成績とは別集計。全期間学習モデルの学習内成績は使用していません。本番モデルや買い目は切り替えません。</p>
      <div className="flex flex-wrap gap-2 text-xs">
        <label>研究の評価区分 <select aria-label="研究の評価区分" value={scope} onChange={event => setScope(event.target.value as ResearchScope)} className="rounded border border-[#333] bg-[#111] p-2">
          <option value="retrospective">後期の再検証（2023〜2026年）</option>
          <option value="development">開発・選定（2020〜2022年5月）</option>
        </select></label>
        <label>研究の買い目 <select aria-label="研究の買い目" value={policy} onChange={event => setPolicy(event.target.value)} className="rounded border border-[#333] bg-[#111] p-2">
          {data ? data.policies.map(row => <option key={row.id} value={row.id}>{row.label}</option>)
            : <option value={policy}>固定②の比較（採用ではありません）</option>}
        </select></label>
      </div>
      {loading && <p role="status" className="text-xs text-[#888]">研究成績を読み込み中...</p>}
      {error && <p role="status" className="text-xs text-[#fca5a5]">{error}通常の成績とは独立しています。</p>}
      {current && stats && selected && <>
        <p className="text-xs text-[#fbbf24]">{current.recommended_method === null
          ? '採用条件を満たす買い目なし：運用への切替は見送り'
          : `開発期間の候補：${current.policies.find(row => row.id === current.recommended_method)?.label ?? current.recommended_method}（研究のみ・購入許可なし）`}</p>
        <p className="text-xs text-[#888]">選定は開発期間のみで固定。後期は探索済みデータの再検証で、未知の将来成績ではありません。初期表示は固定②であり、後期の最高回収率を自動選択しません。</p>
        <section aria-label="研究の仮想成績" className="grid grid-cols-2 gap-2 md:grid-cols-4">
          {[['仮想投資', yen(stats.investment)], ['仮想払戻', yen(stats.payout)],
            ['仮想収支', `${stats.profit > 0 ? '+' : ''}${yen(stats.profit)}`],
            ['研究回収率', performanceRoiLabel(stats.roi)]].map(([label, value]) =>
            <div key={label} className="rounded border border-[#252525] p-3"><div className="text-xs text-[#888]">{label}</div><div className="mt-1 text-lg tabular-nums">{value}</div></div>)}
        </section>
        <p className="text-xs text-[#aaa]">対象 {stats.opportunities.toLocaleString()}レース · 仮購入 {stats.bet_races.toLocaleString()} · 的中 {stats.hit_races.toLocaleString()} · 見送り {(stats.opportunities - stats.bet_races).toLocaleString()} · 46特徴量の同方式 {performanceRoiLabel(selected.baseline.roi)}</p>
        {current.points.length > 0 ? <figure aria-label="研究の累計回収率" className="space-y-2">
          <figcaption className="text-xs text-[#888]">選択期間内の累計回収率（101特徴量と前回46特徴量研究、同じ精算可能レース）</figcaption>
          <div className="h-64"><ResponsiveContainer width="100%" height="100%">
            <LineChart data={current.points}>
              <CartesianGrid stroke="#252525" /><XAxis dataKey="date" tick={{ fontSize: 10 }} />
              <YAxis tickFormatter={value => `${value}%`} tick={{ fontSize: 10 }} />
              <Tooltip formatter={value => typeof value === 'number' ? `${value.toFixed(1)}%` : '—'} contentStyle={{ background: '#111', borderColor: '#333' }} />
              <Legend /><ReferenceLine y={100} stroke="#888" strokeDasharray="4 4" />
              <Line type="monotone" dataKey="roi" name="101特徴量（研究）" stroke="#60a5fa" dot={false} isAnimationActive={false} />
              <Line type="monotone" dataKey="baseline_roi" name="46特徴量（前回研究）" stroke="#4ade80" dot={false} isAnimationActive={false} />
            </LineChart>
          </ResponsiveContainer></div>
        </figure> : <p className="text-xs text-[#888]">選択した期間には研究対象レースがありません。</p>}
        <details open className="text-xs">
          <summary className="cursor-pointer text-[#aaa]">研究買い目14方式の比較（画面上部の期間条件を適用）</summary>
          <div className="mt-2 overflow-x-auto"><table aria-label="研究買い目14方式の比較" className="w-full text-left tabular-nums">
            <thead className="text-[#888]"><tr><th className="p-2">方式</th><th>開発ROI</th><th>後期ROI</th><th>仮購入（選択区分）</th></tr></thead>
            <tbody>{current.policies.map(row => <tr key={row.id} className={`border-t border-[#252525] ${row.id === policy ? 'text-[#93c5fd]' : ''}`}>
              <td className="p-2"><button aria-pressed={row.id === policy} onClick={() => setPolicy(row.id)} className="text-left hover:underline">{row.label}</button></td>
              <td>{performanceRoiLabel(row.development.roi)}</td><td>{performanceRoiLabel(row.retrospective.roi)}</td><td>{row[scope].bet_races.toLocaleString()}</td>
            </tr>)}</tbody>
          </table></div>
        </details>
        <details open className="text-xs">
          <summary className="cursor-pointer text-[#aaa]">研究の月別成績</summary>
          <div className="mt-2 max-h-80 overflow-auto"><table aria-label="研究の月別成績" className="w-full text-left tabular-nums">
            <thead className="text-[#888]"><tr><th className="p-2">月</th><th>仮投資</th><th>仮払戻</th><th>収支</th><th>101 ROI</th><th>46 ROI</th><th>仮購入 / 対象</th></tr></thead>
            <tbody>{current.monthly.map(row => <tr key={row.month} className="border-t border-[#252525]"><td className="p-2">{row.month}</td>
              <td>{yen(row.investment)}</td><td>{yen(row.payout)}</td><td>{yen(row.profit)}</td>
              <td>{performanceRoiLabel(row.roi)}</td><td>{performanceRoiLabel(row.baseline_roi)}</td><td>{row.bet_races} / {row.opportunities}</td>
            </tr>)}</tbody>
          </table></div>
        </details>
        <p className="text-xs leading-relaxed text-[#777]">1仮購入レース合計1,500円（1点1,500円または3点各500円）。比率は未校正の参考値で、H順位は単勝比率×確定単勝オッズ。発走前の取得時刻は未保証、資金枯渇・複利は未検証。{scope === 'development' ? '開発' : '後期'}全期間の除外 {current.excluded_races}レースは外れ0円に含めません。対象期間 {current.periods[scope].join(' 〜 ')}。{current.study_id}</p>
      </>}
    </section>
  )
}
