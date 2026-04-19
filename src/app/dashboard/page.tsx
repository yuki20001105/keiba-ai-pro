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
import { JRA_VENUES } from '@/lib/types'

type Bet = {
  id: number | string; race_id: string; purchase_date: string | null; created_at: string
  bet_type: string; strategy_type: string; total_cost: number
  actual_return: number | null; is_hit: boolean; season: string; venue?: string
  combinations?: string[]
}

// race_id (YYYYMMDDVVRR) → { date, venueCode, venueLabel, raceNo }
function parseRaceId(raceId: string) {
  const date = raceId.slice(0, 8)   // '20260902'
  const vc   = raceId.slice(8, 10)  // '08'
  const rno  = raceId.slice(10, 12) // '03'
  const venue = JRA_VENUES.find(v => v.code === vc)
  return {
    date,
    venueCode: vc,
    venueLabel: venue?.name ?? vc,
    raceNo: rno ? String(parseInt(rno, 10)) : '',
    dateLabel: date.length === 8
      ? `${date.slice(0, 4)}-${date.slice(4, 6)}-${date.slice(6, 8)}`
      : date,
  }
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
  const [result, setResult] = useState<'hit' | 'miss' | null>(null)
  const [returnAmount, setReturnAmount] = useState('')
  const [saving, setSaving] = useState(false)
  const [error, setError] = useState('')

  const handleSave = async () => {
    if (result === null) { setError('的中 / 外れ を選んでください'); return }
    const isHit = result === 'hit'
    const amount = isHit ? parseInt(returnAmount, 10) : 0
    if (isHit && (isNaN(amount) || amount <= 0)) { setError('払戻金額を入力してください'); return }
    setSaving(true)
    setError('')
    try {
      const res = await authFetch(`/api/purchase/${bet.id}`, {
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
      <td colSpan={9} className="px-5 py-4">
        <div className="flex flex-wrap items-center gap-3">
          {/* 的中 / 外れ ボタン */}
          <div className="flex gap-2">
            <button
              onClick={() => setResult('hit')}
              className="text-sm px-4 py-2 rounded border font-medium transition-colors"
              style={result === 'hit'
                ? { background: '#052e10', color: '#4ade80', borderColor: '#16a34a' }
                : { background: 'transparent', color: '#555', borderColor: '#222' }
              }
            >
              ✓ 的中
            </button>
            <button
              onClick={() => { setResult('miss'); setReturnAmount('') }}
              className="text-sm px-4 py-2 rounded border font-medium transition-colors"
              style={result === 'miss'
                ? { background: '#1a0505', color: '#f87171', borderColor: '#991b1b' }
                : { background: 'transparent', color: '#555', borderColor: '#222' }
              }
            >
              ✕ 外れ
            </button>
          </div>

          {/* 払戻金額入力（的中時のみ） */}
          {result === 'hit' && (
            <div className="flex items-center gap-2">
              <span className="text-xs text-[#555]">払戻</span>
              <span className="text-xs text-[#666]">¥</span>
              <input
                type="number"
                min="1"
                autoFocus
                value={returnAmount}
                onChange={e => setReturnAmount(e.target.value)}
                placeholder="例: 1560"
                className="w-36 px-3 py-2 text-sm bg-[#111] border border-[#1a3a1a] rounded text-white focus:outline-none focus:border-[#16a34a] placeholder-[#333]"
              />
            </div>
          )}

          {/* 保存 / キャンセル */}
          <div className="flex items-center gap-2 ml-auto">
            {error && <span className="text-xs text-[#f87171]">{error}</span>}
            <button
              onClick={handleSave}
              disabled={saving || result === null}
              className="text-xs px-4 py-2 bg-white text-black rounded font-medium hover:bg-[#eee] disabled:opacity-40 disabled:cursor-not-allowed transition-colors"
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
  const [deletingBetId, setDeletingBetId] = useState<string | null>(null)
  const [resultEnteredIds, setResultEnteredIds] = useState<Set<string>>(new Set())
  const [toast, setToast] = useState({ visible: false, message: '', type: 'success' as 'success' | 'error' })
  const [sortKey, setSortKey] = useState<'date' | 'venue' | 'raceNo'>('date')
  const [sortDir, setSortDir] = useState<'desc' | 'asc'>('desc')

  const handleSort = (key: 'date' | 'venue' | 'raceNo') => {
    if (sortKey === key) {
      setSortDir(d => d === 'asc' ? 'desc' : 'asc')
    } else {
      setSortKey(key)
      setSortDir('asc')
    }
  }

  const showToast = useCallback((message: string, type: 'success' | 'error' = 'success') => {
    setToast({ visible: true, message, type })
  }, [])

  const deleteBet = async (betId: string) => {
    try {
      const res = await authFetch(`/api/purchase/${betId}`, { method: 'DELETE' })
      if (!res.ok) { const d = await res.json(); throw new Error(d.detail || 'エラー') }
      setBets(prev => prev.filter(b => String(b.id) !== betId))
      showToast('削除しました')
    } catch (e: unknown) {
      showToast(e instanceof Error ? e.message : '削除に失敗しました', 'error')
    } finally {
      setDeletingBetId(null)
    }
  }

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

  // ソート済み履歴
  const sortedBets = [...bets].sort((a, b) => {
    const rpa = parseRaceId(a.race_id)
    const rpb = parseRaceId(b.race_id)
    let cmp = 0
    if (sortKey === 'date')   cmp = rpa.date.localeCompare(rpb.date)
    if (sortKey === 'venue')  cmp = (a.venue || rpa.venueLabel).localeCompare(b.venue || rpb.venueLabel)
    if (sortKey === 'raceNo') cmp = (parseInt(rpa.raceNo || '0') - parseInt(rpb.raceNo || '0'))
    // 同値の場合は次のキーで追加ソート
    if (cmp === 0 && sortKey !== 'date')   cmp = rpa.date.localeCompare(rpb.date)
    if (cmp === 0 && sortKey !== 'raceNo') cmp = (parseInt(rpa.raceNo || '0') - parseInt(rpb.raceNo || '0'))
    return sortDir === 'asc' ? cmp : -cmp
  })

  const pendingBets = sortedBets.filter(bet =>
    !resultEnteredIds.has(String(bet.id)) && !bet.is_hit && (bet.actual_return == null || bet.actual_return === 0)
  )
  const completedBets = sortedBets.filter(bet =>
    resultEnteredIds.has(String(bet.id)) || bet.is_hit || (bet.actual_return != null && bet.actual_return !== 0)
  )

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

        {/* ── 購入履歴 ─────────────────────────────────────────────────── */}
        {bets.length === 0 ? (
          <div className="bg-[#111] border border-[#1e1e1e] rounded-lg p-8 text-center space-y-3">
            <p className="text-[#555] text-sm">購入履歴がまだありません</p>
            <Link href="/predict-batch" className="inline-flex items-center gap-1 text-xs text-[#7dd3fc] hover:underline">
              予測実行ページで購入を記録する →
            </Link>
          </div>
        ) : (
          <div className="space-y-6">

            {/* ── 結果未入力セクション ── */}
            <div>
              <div className="flex items-center gap-2 mb-3">
                <h2 className="text-sm font-medium">結果未入力</h2>
                {pendingBets.length > 0 && (
                  <span className="text-xs px-2 py-0.5 rounded-full bg-[#1a1200] text-[#fbbf24] border border-[#3a2800] font-medium">
                    {pendingBets.length}件
                  </span>
                )}
              </div>
              {pendingBets.length === 0 ? (
                <div className="flex items-center gap-2 px-5 py-4 bg-[#0d130d] border border-[#1a3a1a] rounded-lg text-xs text-[#4ade80]">
                  <span>✓</span>
                  <span>すべての購入結果が入力されています</span>
                </div>
              ) : (
                <div className="bg-[#111] border border-[#2a1e00] rounded-lg overflow-hidden">
                  <div className="overflow-x-auto">
                    <table className="w-full text-sm">
                      <thead>
                        <tr className="border-b border-[#2a1e00]">
                          {([
                            { label: '日付', key: 'date' as const },
                            { label: '開催', key: 'venue' as const },
                            { label: 'R',    key: 'raceNo' as const },
                          ] as const).map(({ label, key }) => (
                            <th key={key} className="px-4 py-3 first:pl-5 text-left">
                              <button
                                onClick={() => handleSort(key)}
                                className="flex items-center gap-1 text-xs font-normal hover:text-white transition-colors"
                                style={{ color: sortKey === key ? '#fff' : '#555' }}
                              >
                                {label}
                                <span className="text-[10px] opacity-60">
                                  {sortKey === key ? (sortDir === 'asc' ? '▲' : '▼') : '⇅'}
                                </span>
                              </button>
                            </th>
                          ))}
                          {['券種', '馬番', '投資額', '詳細', '結果入力', ''].map(h => (
                            <th key={h} className="px-4 py-3 text-left text-xs text-[#555] font-normal">{h}</th>
                          ))}
                        </tr>
                      </thead>
                      <tbody>
                        {pendingBets.map(bet => {
                          const isEditing = editingBetId === String(bet.id)
                          const rp = parseRaceId(bet.race_id)
                          return (
                            <Fragment key={String(bet.id)}>
                              <tr className="border-b border-[#1e1600] hover:bg-[#161000] transition-colors">
                                <td className="px-4 py-3 pl-5 text-[#888] text-xs whitespace-nowrap">
                                  {bet.purchase_date ?? rp.dateLabel}
                                </td>
                                <td className="px-4 py-3 text-white text-xs font-medium whitespace-nowrap">
                                  {bet.venue || rp.venueLabel}
                                </td>
                                <td className="px-4 py-3 text-[#888] text-xs whitespace-nowrap">
                                  {rp.raceNo ? `${rp.raceNo}R` : '—'}
                                </td>
                                <td className="px-4 py-3 font-medium">{bet.bet_type}</td>
                                <td className="px-4 py-3 text-[#aaa] text-xs whitespace-nowrap">
                                  {bet.combinations && bet.combinations.length > 0
                                    ? bet.combinations.map(n => `${n}番`).join(' · ')
                                    : '—'}
                                </td>
                                <td className="px-4 py-3 text-[#888]">¥{bet.total_cost?.toLocaleString()}</td>
                                <td className="px-4 py-3">
                                  <Link
                                    href={`/race-analysis?date=${rp.date}&race_id=${bet.race_id}`}
                                    className="text-xs text-[#7dd3fc] hover:underline whitespace-nowrap"
                                  >
                                    確認
                                  </Link>
                                </td>
                                <td className="px-4 py-3">
                                  <button
                                    onClick={() => setEditingBetId(isEditing ? null : String(bet.id))}
                                    className="text-xs px-3 py-1 rounded font-medium transition-colors whitespace-nowrap"
                                    style={isEditing
                                      ? { background: '#1a1a1a', color: '#666', border: '1px solid #2a2a2a' }
                                      : { background: '#1a1200', color: '#fbbf24', border: '1px solid #3a2800' }
                                    }
                                  >
                                    {isEditing ? '閉じる' : '結果を入力 →'}
                                  </button>
                                </td>
                                <td className="px-4 py-3">
                                  {deletingBetId === String(bet.id) ? (
                                    <span className="flex items-center gap-1">
                                      <button
                                        onClick={() => deleteBet(String(bet.id))}
                                        className="text-xs text-[#f87171] hover:text-red-400 border border-[#3a1a1a] rounded px-2 py-0.5 hover:border-[#f87171] transition-colors whitespace-nowrap"
                                      >
                                        確認
                                      </button>
                                      <button
                                        onClick={() => setDeletingBetId(null)}
                                        className="text-xs text-[#555] hover:text-[#888] transition-colors"
                                      >
                                        ✕
                                      </button>
                                    </span>
                                  ) : (
                                    <button
                                      onClick={() => setDeletingBetId(String(bet.id))}
                                      className="text-xs text-[#333] hover:text-[#f87171] transition-colors px-1"
                                      title="削除"
                                    >
                                      🗑
                                    </button>
                                  )}
                                </td>
                              </tr>
                              {isEditing && (
                                <ResultInputRow
                                  bet={bet}
                                  onClose={() => setEditingBetId(null)}
                                  onSave={updated => {
                                    setBets(prev => prev.map(b => String(b.id) === String(updated.id) ? updated : b))
                                    setResultEnteredIds(prev => new Set(prev).add(String(updated.id)))
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
                </div>
              )}
            </div>

            {/* ── 入力済みセクション ── */}
            {completedBets.length > 0 && (
              <div>
                <div className="flex items-center gap-2 mb-3">
                  <h2 className="text-sm font-medium text-[#888]">入力済み</h2>
                  <span className="text-xs text-[#444]">{completedBets.length}件</span>
                </div>
                <div className="bg-[#111] border border-[#1e1e1e] rounded-lg overflow-hidden">
                  <div className="overflow-x-auto">
                    <table className="w-full text-sm">
                      <thead>
                        <tr className="border-b border-[#1e1e1e]">
                          {([
                            { label: '日付', key: 'date' as const },
                            { label: '開催', key: 'venue' as const },
                            { label: 'R',    key: 'raceNo' as const },
                          ] as const).map(({ label, key }) => (
                            <th key={key} className="px-4 py-3 first:pl-5 text-left">
                              <button
                                onClick={() => handleSort(key)}
                                className="flex items-center gap-1 text-xs font-normal hover:text-white transition-colors"
                                style={{ color: sortKey === key ? '#fff' : '#555' }}
                              >
                                {label}
                                <span className="text-[10px] opacity-60">
                                  {sortKey === key ? (sortDir === 'asc' ? '▲' : '▼') : '⇅'}
                                </span>
                              </button>
                            </th>
                          ))}
                          {['券種', '馬番', '投資額', '回収', 'P&L', '詳細', '結果', ''].map(h => (
                            <th key={h} className="px-4 py-3 text-left text-xs text-[#555] font-normal">{h}</th>
                          ))}
                        </tr>
                      </thead>
                      <tbody>
                        {completedBets.slice(0, 50).map(bet => {
                          const ret = bet.actual_return ?? 0
                          const pl = ret - (bet.total_cost ?? 0)
                          const rp = parseRaceId(bet.race_id)
                          return (
                            <tr key={String(bet.id)} className="border-b border-[#1a1a1a] hover:bg-[#161616] transition-colors">
                              <td className="px-4 py-3 pl-5 text-[#888] text-xs whitespace-nowrap">
                                {bet.purchase_date ?? rp.dateLabel}
                              </td>
                              <td className="px-4 py-3 text-white text-xs font-medium whitespace-nowrap">
                                {bet.venue || rp.venueLabel}
                              </td>
                              <td className="px-4 py-3 text-[#888] text-xs whitespace-nowrap">
                                {rp.raceNo ? `${rp.raceNo}R` : '—'}
                              </td>
                              <td className="px-4 py-3 font-medium">{bet.bet_type}</td>
                              <td className="px-4 py-3 text-[#aaa] text-xs whitespace-nowrap">
                                {bet.combinations && bet.combinations.length > 0
                                  ? bet.combinations.map(n => `${n}番`).join(' · ')
                                  : '—'}
                              </td>
                              <td className="px-4 py-3 text-[#888]">¥{bet.total_cost?.toLocaleString()}</td>
                              <td className={`px-4 py-3 ${bet.is_hit ? 'text-[#4ade80]' : 'text-[#555]'}`}>
                                {bet.is_hit ? `¥${ret.toLocaleString()}` : '—'}
                              </td>
                              <td className={`px-4 py-3 font-medium ${bet.is_hit ? (pl >= 0 ? 'text-[#4ade80]' : 'text-[#f87171]') : 'text-[#555]'}`}>
                                {bet.is_hit ? `${pl >= 0 ? '+' : ''}¥${pl.toLocaleString()}` : '—'}
                              </td>
                              <td className="px-4 py-3">
                                <Link
                                  href={`/race-analysis?date=${rp.date}&race_id=${bet.race_id}`}
                                  className="text-xs text-[#7dd3fc] hover:underline whitespace-nowrap"
                                >
                                  確認
                                </Link>
                              </td>
                              <td className="px-4 py-3">
                                <span className={`text-xs ${bet.is_hit ? 'text-[#4ade80]' : 'text-[#f87171]'}`}>
                                  {bet.is_hit ? '✓ 的中' : '✕ 外れ'}
                                </span>
                              </td>
                              <td className="px-4 py-3">
                                {deletingBetId === String(bet.id) ? (
                                  <span className="flex items-center gap-1">
                                    <button
                                      onClick={() => deleteBet(String(bet.id))}
                                      className="text-xs text-[#f87171] hover:text-red-400 border border-[#3a1a1a] rounded px-2 py-0.5 hover:border-[#f87171] transition-colors whitespace-nowrap"
                                    >
                                      確認
                                    </button>
                                    <button
                                      onClick={() => setDeletingBetId(null)}
                                      className="text-xs text-[#555] hover:text-[#888] transition-colors"
                                    >
                                      ✕
                                    </button>
                                  </span>
                                ) : (
                                  <button
                                    onClick={() => setDeletingBetId(String(bet.id))}
                                    className="text-xs text-[#333] hover:text-[#f87171] transition-colors px-1"
                                    title="削除"
                                  >
                                    🗑
                                  </button>
                                )}
                              </td>
                            </tr>
                          )
                        })}
                      </tbody>
                    </table>
                  </div>
                </div>
              </div>
            )}

          </div>
        )}

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
