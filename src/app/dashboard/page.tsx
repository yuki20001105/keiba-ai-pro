'use client'

import { useState, useEffect, useCallback, Fragment } from 'react'
import Link from 'next/link'
import { Logo } from '@/components/Logo'
import { Toast } from '@/components/Toast'
import {
  LineChart, Line, BarChart, Bar, XAxis, YAxis, Tooltip,
  ResponsiveContainer, ReferenceLine, CartesianGrid, Cell,
} from 'recharts'
import { authFetch } from '@/lib/auth-fetch'

type Bet = {
  id: number | string; race_id: string; purchase_date: string | null; created_at: string
  bet_type: string; strategy_type: string; total_cost: number
  actual_return: number | null; is_hit: boolean; season: string
}
type BetTypeStat = {
  bet_type: string; count: number; total_cost: number; total_return: number
  recovery_rate: number; hit_count: number; hit_rate: number
}

// ── カスタム Tooltip ─────────────────────────────
function PlTooltip({ active, payload, label }: any) {
  if (!active || !payload?.length) return null
  return (
    <div className="bg-[#1a1a1a] border border-[#333] rounded px-3 py-2 text-xs">
      <div className="text-[#888] mb-1">{label}</div>
      {payload.map((p: any) => (
        <div key={p.name} style={{ color: p.color }}>
          {p.name}: {typeof p.value === 'number' ? p.value.toLocaleString() : p.value}
          {p.name === '回収率' ? '%' : p.name === '損益' || p.name === '累積損益' ? '円' : ''}
        </div>
      ))}
    </div>
  )
}

// ── 結果入力フォーム（インライン）────────────────────
function ResultInputRow({
  bet,
  onSave,
  onClose,
}: {
  bet: Bet
  onSave: (updated: Bet) => void
  onClose: () => void
}) {
  const [returnAmount, setReturnAmount] = useState('')
  const [isHit, setIsHit] = useState(false)
  const [saving, setSaving] = useState(false)
  const [error, setError] = useState('')

  const handleSave = async () => {
    const amount = parseInt(returnAmount, 10)
    if (isNaN(amount) || amount < 0) { setError('0以上の整数を入力してください'); return }
    setSaving(true)
    setError('')
    try {
      const res = await fetch(`/api/purchase/${bet.id}`, {
        method: 'PATCH',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ actual_return: amount, is_hit: isHit }),
      })
      if (!res.ok) { const d = await res.json(); throw new Error(d.detail || 'エラー') }
      onSave({ ...bet, actual_return: amount, is_hit: isHit })
    } catch (e: unknown) {
      setError(e instanceof Error ? e.message : 'エラーが発生しました')
    } finally {
      setSaving(false)
    }
  }

  return (
    <tr className="bg-[#0d0d0d] border-b border-[#1a1a1a]">
      <td colSpan={8} className="px-5 py-4">
        <div className="flex flex-wrap items-end gap-4">
          <div>
            <label className="text-xs text-[#555] block mb-1.5">払戻金額 (円)</label>
            <input
              type="number"
              min="0"
              value={returnAmount}
              onChange={e => { setReturnAmount(e.target.value); if (parseInt(e.target.value, 10) > 0) setIsHit(true) }}
              placeholder="0"
              className="w-36 px-3 py-2 text-sm bg-[#111] border border-[#333] rounded text-white focus:outline-none focus:border-[#555] placeholder-[#444]"
            />
          </div>
          <label className="flex items-center gap-2 cursor-pointer pb-2">
            <input
              type="checkbox"
              checked={isHit}
              onChange={e => setIsHit(e.target.checked)}
              className="w-4 h-4 accent-white"
            />
            <span className="text-xs text-[#888]">的中</span>
          </label>
          <div className="flex items-center gap-2 pb-2">
            <button
              onClick={handleSave}
              disabled={saving}
              className="text-xs px-4 py-2 bg-white text-black rounded font-medium hover:bg-[#eee] disabled:opacity-50 transition-colors"
            >
              {saving ? '保存中...' : '保存'}
            </button>
            <button
              onClick={onClose}
              className="text-xs px-3 py-2 border border-[#333] rounded text-[#666] hover:border-[#555] transition-colors"
            >
              キャンセル
            </button>
          </div>
          {error && <span className="text-xs text-[#f87171] pb-2">{error}</span>}
        </div>
      </td>
    </tr>
  )
}

export default function DashboardPage() {
  const [loading, setLoading] = useState(true)
  const [dataStats, setDataStats] = useState({ totalRaces: 0, totalHorses: 0, totalModels: 0 })
  const [bets, setBets] = useState<Bet[]>([])
  const [betTypeStats, setBetTypeStats] = useState<BetTypeStat[]>([])
  const [editingBetId, setEditingBetId] = useState<string | null>(null)
  const [toast, setToast] = useState({ visible: false, message: '', type: 'success' as 'success' | 'error' })

  const showToast = useCallback((message: string, type: 'success' | 'error' = 'success') => {
    setToast({ visible: true, message, type })
  }, [])

  useEffect(() => {
    Promise.all([loadStats(), loadBets(), loadStatistics()]).finally(() => setLoading(false))
  }, [])

  const loadStats = async () => {
    try {
      const res = await authFetch('/api/data-stats?ultimate=true')
      if (res.ok) {
        const d = await res.json()
        setDataStats({ totalRaces: d.total_races || 0, totalHorses: d.total_horses || 0, totalModels: d.total_models || 0 })
      }
    } catch {}
  }

  const loadBets = async () => {
    try {
      const res = await authFetch('/api/purchase-history?limit=200')
      if (res.ok) {
        const d = await res.json()
        setBets(d.history || [])
      }
    } catch {}
  }

  const loadStatistics = async () => {
    try {
      const res = await authFetch('/api/statistics')
      if (res.ok) {
        const d = await res.json()
        setBetTypeStats(d.statistics?.by_bet_type || [])
      }
    } catch {}
  }

  // ── 集計 ──────────────────────────────────────────────────────────────────
  const totalBets = bets.length
  const wins = bets.filter(b => b.is_hit).length
  const totalCost = bets.reduce((s, b) => s + (b.total_cost ?? 0), 0)
  const totalReturn = bets.reduce((s, b) => s + (b.actual_return ?? 0), 0)
  const totalPL = totalReturn - totalCost
  const winRate = totalBets > 0 ? (wins / totalBets * 100).toFixed(1) : '-'
  const recoveryRate = totalCost > 0 ? (totalReturn / totalCost * 100).toFixed(1) : '-'

  // ── 累積損益チャートデータ（日付昇順）──────────────────────────────────────
  const cumulChartData = (() => {
    const sorted = [...bets]
      .sort((a, b) => new Date(a.created_at).getTime() - new Date(b.created_at).getTime())
    let cumPL = 0
    let cumCost = 0
    let cumReturn = 0
    return sorted.map((b, i) => {
      const pl = (b.actual_return ?? 0) - (b.total_cost ?? 0)
      cumPL += pl
      cumCost += b.total_cost ?? 0
      cumReturn += b.actual_return ?? 0
      const rr = cumCost > 0 ? Math.round(cumReturn / cumCost * 100) : 0
      const label = b.purchase_date ?? b.created_at?.slice(0, 10) ?? `#${i + 1}`
      return { label, 累積損益: cumPL, 回収率: rr }
    })
  })()

  // ── 券種別回収率チャートデータ ───────────────────────────────────────────
  const betTypeChartData = betTypeStats.map(s => ({
    name: s.bet_type,
    回収率: s.recovery_rate,
    的中率: s.hit_rate,
    件数: s.count,
  }))

  const rrNum = Number(recoveryRate)
  const SUMMARY_CARDS = [
    { label: '購入回数',  value: String(totalBets), sub: `的中 ${wins}回` },
    { label: '的中率',   value: `${winRate}%`,      sub: `${wins}/${totalBets}` },
    { label: '累積損益', value: `${totalPL >= 0 ? '+' : ''}¥${totalPL.toLocaleString()}`, sub: `投資 ¥${totalCost.toLocaleString()}`, color: totalPL >= 0 ? '#4ade80' : '#f87171' },
    { label: '回収率',   value: `${recoveryRate}%`, sub: rrNum >= 100 ? '黒字' : '赤字', color: rrNum >= 100 ? '#4ade80' : '#f87171', progress: rrNum },
  ]

  if (loading) {
    return (
      <div className="min-h-screen bg-[#0a0a0a] flex items-center justify-center">
        <div className="w-5 h-5 rounded-full border-2 border-white border-t-transparent animate-spin" />
      </div>
    )
  }

  return (
    <div className="min-h-screen bg-[#0a0a0a] text-white">
      <header className="border-b border-[#1e1e1e] px-6 py-4 flex items-center justify-between">
        <Logo href="/home" />
        <div className="flex items-center gap-4">
          <Link href="/home" className="flex items-center gap-1 text-xs text-[#555] hover:text-white transition-colors">
            <svg className="w-3 h-3" fill="none" viewBox="0 0 24 24" stroke="currentColor">
              <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M15 19l-7-7 7-7" />
            </svg>
            ホーム
          </Link>
          <span className="text-sm text-[#888]">ダッシュボード</span>
        </div>
      </header>

      <main className="max-w-5xl mx-auto px-6 py-10 space-y-8">

        {/* ── DB 統計 ──────────────────────────────────────────────────────── */}
        <div className="flex items-center gap-6 px-5 py-3 bg-[#111] border border-[#1e1e1e] rounded-lg">
          {[
            { label: 'DBレース', value: dataStats.totalRaces.toLocaleString() },
            { label: '馬',       value: dataStats.totalHorses.toLocaleString() },
            { label: 'モデル',   value: String(dataStats.totalModels) },
          ].map((s, i) => (
            <div key={s.label} className={`flex items-baseline gap-2 ${i > 0 ? 'border-l border-[#1e1e1e] pl-6' : ''}`}>
              <span className="text-lg font-bold">{s.value}</span>
              <span className="text-xs text-[#555]">{s.label}</span>
            </div>
          ))}
        </div>

        {/* ── 購入サマリー ──────────────────────────────────────────────────── */}
        {totalBets === 0 ? (
          <div className="bg-[#111] border border-[#1e1e1e] rounded-lg p-8 text-center space-y-3">
            <p className="text-[#555] text-sm">まだ購入記録がありません</p>
            <Link href="/predict-batch" className="inline-flex items-center gap-1.5 text-xs text-[#7dd3fc] hover:underline">
              予測を実行して購入を記録する
              <svg className="w-3 h-3" fill="none" viewBox="0 0 24 24" stroke="currentColor">
                <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M9 5l7 7-7 7" />
              </svg>
            </Link>
          </div>
        ) : (
          <div className="grid grid-cols-2 md:grid-cols-4 gap-3">
            {(SUMMARY_CARDS as Array<{label:string;value:string;sub:string;color?:string;progress?:number}>).map(s => (
              <div key={s.label} className="bg-[#111] border border-[#1e1e1e] rounded-lg p-5 space-y-2">
                <div className="text-xs text-[#666]">{s.label}</div>
                <div className="text-2xl font-bold" style={s.color ? { color: s.color } : undefined}>{s.value}</div>
                {s.progress !== undefined && (
                  <div className="h-1 bg-[#1e1e1e] rounded-full overflow-hidden">
                    <div
                      className="h-full rounded-full transition-all"
                      style={{
                        width: `${Math.min(s.progress, 200) / 2}%`,
                        background: s.progress >= 100 ? '#4ade80' : '#f87171',
                      }}
                    />
                  </div>
                )}
                {s.sub && <div className="text-[10px] text-[#555]">{s.sub}</div>}
              </div>
            ))}
          </div>
        )}

        {/* ── 累積損益グラフ ────────────────────────────────────────────────── */}
        {cumulChartData.length >= 2 && (
          <div className="bg-[#111] border border-[#1e1e1e] rounded-lg p-5">
            <div className="flex items-center justify-between mb-4">
              <h2 className="text-sm font-medium">累積損益 &amp; 回収率の推移</h2>
              <div className="flex items-center gap-4 text-[10px] text-[#555]">
                <span className="flex items-center gap-1"><span className="w-3 h-0.5 bg-[#7dd3fc] inline-block"/>累積損益（円）</span>
                <span className="flex items-center gap-1"><span className="w-3 h-0.5 bg-[#fbbf24] inline-block"/>回収率（%）</span>
              </div>
            </div>
            <ResponsiveContainer width="100%" height={240}>
              <LineChart data={cumulChartData} margin={{ top: 4, right: 16, left: 0, bottom: 0 }}>
                <CartesianGrid strokeDasharray="3 3" stroke="#1e1e1e" />
                <XAxis
                  dataKey="label"
                  tick={{ fontSize: 10, fill: '#555' }}
                  tickLine={false}
                  axisLine={{ stroke: '#333' }}
                  interval="preserveStartEnd"
                />
                <YAxis
                  yAxisId="pl"
                  tick={{ fontSize: 10, fill: '#555' }}
                  tickLine={false}
                  axisLine={false}
                  tickFormatter={v => v.toLocaleString()}
                />
                <YAxis
                  yAxisId="rr"
                  orientation="right"
                  tick={{ fontSize: 10, fill: '#555' }}
                  tickLine={false}
                  axisLine={false}
                  tickFormatter={v => `${v}%`}
                  domain={[0, 'auto']}
                />
                <Tooltip content={<PlTooltip />} />
                <ReferenceLine yAxisId="pl" y={0} stroke="#333" strokeDasharray="4 2" />
                <ReferenceLine yAxisId="rr" y={100} stroke="#555" strokeDasharray="4 2" />
                <Line
                  yAxisId="pl"
                  type="monotone"
                  dataKey="累積損益"
                  stroke="#7dd3fc"
                  strokeWidth={2}
                  dot={false}
                  activeDot={{ r: 4, fill: '#7dd3fc' }}
                />
                <Line
                  yAxisId="rr"
                  type="monotone"
                  dataKey="回収率"
                  stroke="#fbbf24"
                  strokeWidth={1.5}
                  dot={false}
                  activeDot={{ r: 4, fill: '#fbbf24' }}
                  strokeDasharray="5 3"
                />
              </LineChart>
            </ResponsiveContainer>
          </div>
        )}

        {/* ── 券種別回収率グラフ ─────────────────────────────────────────────── */}
        {betTypeChartData.length > 0 && (
          <div className="bg-[#111] border border-[#1e1e1e] rounded-lg p-5">
            <h2 className="text-sm font-medium mb-4">券種別 回収率 &amp; 的中率</h2>
            <ResponsiveContainer width="100%" height={200}>
              <BarChart data={betTypeChartData} layout="vertical" margin={{ top: 0, right: 48, left: 0, bottom: 0 }}>
                <CartesianGrid strokeDasharray="3 3" stroke="#1e1e1e" horizontal={false} />
                <XAxis
                  type="number"
                  tick={{ fontSize: 10, fill: '#555' }}
                  tickLine={false}
                  axisLine={{ stroke: '#333' }}
                  tickFormatter={v => `${v}%`}
                  domain={[0, (max: number) => Math.max(max, 120)]}
                />
                <YAxis
                  dataKey="name"
                  type="category"
                  tick={{ fontSize: 11, fill: '#aaa' }}
                  tickLine={false}
                  axisLine={false}
                  width={40}
                />
                <Tooltip content={<PlTooltip />} />
                <ReferenceLine x={100} stroke="#555" strokeDasharray="4 2" />
                <Bar dataKey="回収率" radius={[0, 4, 4, 0]}>
                  {betTypeChartData.map((entry, i) => (
                    <Cell key={i} fill={entry['回収率'] >= 100 ? '#4ade80' : '#f87171'} fillOpacity={0.8} />
                  ))}
                </Bar>
                <Bar dataKey="的中率" fill="#7dd3fc" fillOpacity={0.5} radius={[0, 4, 4, 0]} />
              </BarChart>
            </ResponsiveContainer>
            <div className="flex items-center gap-4 mt-2 text-[10px] text-[#555]">
              <span className="flex items-center gap-1"><span className="w-3 h-2 rounded bg-[#4ade80] inline-block opacity-80"/>回収率≥100%</span>
              <span className="flex items-center gap-1"><span className="w-3 h-2 rounded bg-[#f87171] inline-block opacity-80"/>回収率＜100%</span>
              <span className="flex items-center gap-1"><span className="w-3 h-2 rounded bg-[#7dd3fc] inline-block opacity-50"/>的中率</span>
              <span className="ml-auto">点線: 100%ライン</span>
            </div>
          </div>
        )}

        {/* ── 購入履歴テーブル ──────────────────────────────────────────────── */}
        <div>
          <h2 className="text-sm font-medium text-[#888] mb-3">購入履歴（直近 {Math.min(bets.length, 50)} 件）</h2>
          <div className="bg-[#111] border border-[#1e1e1e] rounded-lg overflow-hidden">
            {bets.length === 0 ? (
              <div className="p-8 text-center space-y-3">
                <p className="text-[#555] text-sm">購入履歴がまだありません</p>
                <Link href="/predict-batch" className="inline-flex items-center gap-1 text-xs text-[#7dd3fc] hover:underline">
                  予測実行ページで購入を記録する →
                </Link>
              </div>
            ) : (
              <div className="overflow-x-auto">
                <table className="w-full text-sm">
                  <thead>
                    <tr className="border-b border-[#1e1e1e]">
                      {['日付', 'レースID', '券種', '戦略', '投資額', '回収', 'P&L', '結果'].map(h => (
                        <th key={h} className="px-4 py-3 text-left text-xs text-[#555] font-normal first:pl-5">
                          {h}
                        </th>
                      ))}
                    </tr>
                  </thead>
                  <tbody>
                    {bets.slice(0, 50).map((bet, i) => {
                      const ret = bet.actual_return ?? 0
                      const pl = ret - (bet.total_cost ?? 0)
                      const needsResult = !bet.is_hit && (bet.actual_return == null || bet.actual_return === 0)
                      const isEditing = editingBetId === String(bet.id)
                      return (
                        <Fragment key={i}>
                          <tr className="border-b border-[#1a1a1a] hover:bg-[#161616] transition-colors">
                            <td className="px-4 py-3 pl-5 text-[#888] text-xs whitespace-nowrap">
                              {bet.purchase_date ?? bet.created_at?.slice(0, 10)}
                            </td>
                            <td className="px-4 py-3 text-[#888] text-xs font-mono">{bet.race_id}</td>
                            <td className="px-4 py-3 font-medium">{bet.bet_type}</td>
                            <td className="px-4 py-3 text-[#888] text-xs">{bet.strategy_type}</td>
                            <td className="px-4 py-3 text-[#888]">¥{bet.total_cost?.toLocaleString()}</td>
                            <td className={`px-4 py-3 ${bet.is_hit ? 'text-[#4ade80]' : 'text-[#555]'}`}>
                              {bet.is_hit ? `¥${ret.toLocaleString()}` : '未確定'}
                            </td>
                            <td className={`px-4 py-3 font-medium ${bet.is_hit ? (pl >= 0 ? 'text-[#4ade80]' : 'text-[#f87171]') : 'text-[#555]'}`}>
                              {bet.is_hit ? `${pl >= 0 ? '+' : ''}¥${pl.toLocaleString()}` : '—'}
                            </td>
                            <td className="px-4 py-3">
                              {needsResult ? (
                                <button
                                  onClick={() => setEditingBetId(isEditing ? null : String(bet.id))}
                                  className="text-xs text-[#555] hover:text-[#7dd3fc] border border-[#222] rounded px-2 py-0.5 hover:border-[#444] transition-colors whitespace-nowrap"
                                >
                                  {isEditing ? '閉じる' : '結果入力'}
                                </button>
                              ) : (
                                <span className="text-xs text-[#333]">入力済</span>
                              )}
                            </td>
                          </tr>
                          {isEditing && (
                            <ResultInputRow
                              bet={bet}
                              onClose={() => setEditingBetId(null)}
                              onSave={updated => {
                                setBets(prev => prev.map(b => String(b.id) === String(updated.id) ? updated : b))
                                setEditingBetId(null)
                                showToast('結果を記録しました')
                              }}
                            />
                          )}
                        </Fragment>
                      )
                    })}
                  </tbody>
                </table>
              </div>
            )}
          </div>
        </div>

        <Toast
          message={toast.message}
          type={toast.type}
          isVisible={toast.visible}
          onClose={() => setToast(t => ({ ...t, visible: false }))}
        />

        {/* ── フッター CTA ──────────────────────────────────────────────────── */}
        <div className="p-5 bg-[#111] border border-[#1e1e1e] rounded-lg flex items-center justify-between gap-4">
          <div>
            <div className="text-xs text-[#666] mb-0.5">新しいデータを追加する</div>
            <div className="text-sm font-medium">データ取得</div>
            <div className="text-xs text-[#555] mt-0.5">最新のレース情報を収集してモデルを更新します</div>
          </div>
          <Link
            href="/data-collection"
            className="shrink-0 flex items-center gap-1.5 bg-white text-black text-sm font-medium px-5 py-2.5 rounded hover:bg-[#eee] transition-colors"
          >
            データ取得へ
            <svg className="w-3.5 h-3.5" fill="none" viewBox="0 0 24 24" stroke="currentColor">
              <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M9 5l7 7-7 7" />
            </svg>
          </Link>
        </div>
      </main>
    </div>
  )
}
