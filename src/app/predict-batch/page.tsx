'use client'

import { useState, useEffect, useCallback } from 'react'
import Link from 'next/link'
import { Logo } from '@/components/Logo'
import { supabase } from '@/lib/supabase'

// JRA 10場
const JRA_VENUES = [
  { code: '01', name: '札幌' },
  { code: '02', name: '函館' },
  { code: '03', name: '福島' },
  { code: '04', name: '新潟' },
  { code: '05', name: '東京' },
  { code: '06', name: '中山' },
  { code: '07', name: '中京' },
  { code: '08', name: '京都' },
  { code: '09', name: '阪神' },
  { code: '10', name: '小倉' },
]

type RaceItem = {
  race_id: string
  race_name: string
  venue: string
  venue_code: string
  race_no: number
  distance: number
  track_type: string
  num_horses: number
}

type RaceResult = {
  success: boolean
  data?: any
  error?: string
}

function todayStr(): string {
  const d = new Date()
  const y = d.getFullYear()
  const m = String(d.getMonth() + 1).padStart(2, '0')
  const day = String(d.getDate()).padStart(2, '0')
  return `${y}${m}${day}`
}

function toInputDate(yyyymmdd: string): string {
  return `${yyyymmdd.slice(0, 4)}-${yyyymmdd.slice(4, 6)}-${yyyymmdd.slice(6, 8)}`
}

function fromInputDate(s: string): string {
  return s.replace(/-/g, '')
}

export default function PredictBatchPage() {
  const [date, setDate] = useState(todayStr())
  const [venueFilter, setVenueFilter] = useState<Set<string>>(new Set())
  const [races, setRaces] = useState<RaceItem[]>([])
  const [racesLoading, setRacesLoading] = useState(false)
  const [racesError, setRacesError] = useState('')
  const [selectedIds, setSelectedIds] = useState<Set<string>>(new Set())
  const [models, setModels] = useState<any[]>([])
  const [modelId, setModelId] = useState<string>('')
  const [predicting, setPredicting] = useState(false)
  const [results, setResults] = useState<Record<string, RaceResult>>({})
  const [expandedRace, setExpandedRace] = useState<string | null>(null)
  const [scrapeStatus, setScrapeStatus] = useState<'idle' | 'scraping' | 'done' | 'error'>('idle')
  const [scrapeMessage, setScrapeMessage] = useState('')

  useEffect(() => { loadModels() }, [])

  const loadModels = async () => {
    try {
      const res = await fetch('/api/models?ultimate=true')
      if (res.ok) {
        const data = await res.json()
        setModels(data.models || [])
      }
    } catch {}
  }

  const loadRaces = useCallback(async () => {
    setRacesLoading(true)
    setRacesError('')
    setRaces([])
    setSelectedIds(new Set())
    setResults({})
    setScrapeStatus('idle')
    try {
      const res = await fetch(`/api/races/by-date?date=${date}`)
      const data = await res.json()
      if (!res.ok) throw new Error(data.detail || `HTTP ${res.status}`)
      setRaces(data.races || [])
      if ((data.races || []).length === 0) setRacesError('該当日のデータがDBに見つかりません。')
    } catch (e: any) {
      setRacesError(e.message)
    } finally {
      setRacesLoading(false)
    }
  }, [date])

  const triggerScrape = async (force = false) => {
    setScrapeStatus('scraping')
    setScrapeMessage(force ? 'オッズ更新中（強制再スクレイプ）...' : 'スクレイプ開始中...')

    const { data: { session } } = await supabase.auth.getSession()
    const authHeaders: Record<string, string> = session?.access_token
      ? { Authorization: `Bearer ${session.access_token}` }
      : {}

    try {
      // ジョブ開始
      const startRes = await fetch('/api/scrape', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json', ...authHeaders },
        body: JSON.stringify({ start_date: date, end_date: date, force_rescrape: force }),
      })
      if (!startRes.ok) {
        const e = await startRes.json()
        throw new Error(e.detail || `HTTP ${startRes.status}`)
      }
      const { job_id } = await startRes.json()
      setScrapeMessage(`ジョブ開始 (${job_id}) — データ収集中...`)

      // ポーリング（3秒間隔）
      let failCount = 0
      while (true) {
        await new Promise(r => setTimeout(r, 3000))
        const statusRes = await fetch(`/api/scrape/status/${job_id}`, { headers: authHeaders })
        if (!statusRes.ok) {
          if (++failCount >= 10) throw new Error('サーバーが応答しません')
          continue
        }
        failCount = 0
        const s = await statusRes.json()
        const prog = s.progress || {}
        if (prog.message) setScrapeMessage(prog.message)
        if (s.status === 'completed') {
          setScrapeStatus('done')
          setScrapeMessage(`スクレイプ完了 — ${prog.message || ''}`)
          // 自動でレース一覧を再取得
          await loadRaces()
          return
        }
        if (s.status === 'error') throw new Error(s.error || 'スクレイプ失敗')
        if (s.status === 'not_found' && ++failCount >= 10) throw new Error('ジョブが見つかりません')
      }
    } catch (e: any) {
      setScrapeStatus('error')
      setScrapeMessage(e.message)
    }
  }

  // 場所フィルター適用後のレース一覧
  const filteredRaces = venueFilter.size === 0
    ? races
    : races.filter(r => venueFilter.has(r.venue_code))

  const toggleVenue = (code: string) => {
    setVenueFilter(prev => {
      const n = new Set(prev)
      n.has(code) ? n.delete(code) : n.add(code)
      return n
    })
  }

  const toggleRace = (id: string) => {
    setSelectedIds(prev => {
      const n = new Set(prev)
      n.has(id) ? n.delete(id) : n.add(id)
      return n
    })
  }

  const selectAll = () => setSelectedIds(new Set(filteredRaces.map(r => r.race_id)))
  const deselectAll = () => setSelectedIds(new Set())

  const handleBatchPredict = async () => {
    if (selectedIds.size === 0) { alert('予測するレースを選択してください'); return }
    setPredicting(true)
    setResults({})

    const { data: { session } } = await supabase.auth.getSession()
    const authHeaders: Record<string, string> = session?.access_token
      ? { Authorization: `Bearer ${session.access_token}` }
      : {}

    try {
      const res = await fetch('/api/analyze-races-batch', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json', ...authHeaders },
        body: JSON.stringify({
          race_ids: Array.from(selectedIds),
          model_id: modelId || null,
          bankroll: 10000,
          risk_mode: 'balanced',
        }),
      })
      const data = await res.json()
      if (!res.ok) throw new Error(data.detail || `HTTP ${res.status}`)
      setResults(data.results || {})
      // 最初の成功レースを展開
      const firstOk = Object.keys(data.results || {}).find(k => data.results[k].success)
      if (firstOk) setExpandedRace(firstOk)
    } catch (e: any) {
      alert(`一括予測エラー: ${e.message}`)
    } finally {
      setPredicting(false)
    }
  }

  const presentVenueCodes = new Set(races.map(r => r.venue_code))

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
          <span className="text-sm text-[#888]">一括予測</span>
        </div>
      </header>

      <main className="max-w-4xl mx-auto px-6 py-10 space-y-6">

        {/* ── Layer 1: 日付・条件 ── */}
        <div className="bg-[#111] border border-[#1e1e1e] rounded-lg p-6 space-y-5">
          <h2 className="text-sm font-semibold text-white">① 日付・条件設定</h2>

          <div className="flex flex-col sm:flex-row gap-4 items-start sm:items-end">
            <div className="flex-1">
              <label className="text-xs text-[#666] block mb-2">日付</label>
              <input
                type="date"
                value={toInputDate(date)}
                onChange={e => setDate(fromInputDate(e.target.value))}
                className="w-full px-4 py-3 bg-[#0a0a0a] border border-[#1e1e1e] rounded-lg text-white focus:outline-none focus:border-[#333] transition-colors"
              />
            </div>
            <div className="flex-1">
              <label className="text-xs text-[#666] block mb-2">使用モデル</label>
              <select
                value={modelId}
                onChange={e => setModelId(e.target.value)}
                className="w-full px-4 py-3 bg-[#0a0a0a] border border-[#1e1e1e] rounded-lg text-white focus:outline-none focus:border-[#333] transition-colors"
              >
                <option value="">最新モデルを自動選択</option>
                {models.map((m, i) => (
                  <option key={i} value={m.model_id}>{m.model_id} (AUC: {m.auc?.toFixed(4)})</option>
                ))}
              </select>
            </div>
          </div>

          {/* 場所フィルター（DBに存在する場所のみ表示） */}
          {presentVenueCodes.size > 0 && (
            <div>
              <label className="text-xs text-[#666] block mb-2">場所フィルター（未選択 = 全場）</label>
              <div className="flex flex-wrap gap-2">
                {JRA_VENUES.filter(v => presentVenueCodes.has(v.code)).map(v => (
                  <button
                    key={v.code}
                    onClick={() => toggleVenue(v.code)}
                    className={`px-3 py-1.5 text-xs rounded border transition-colors ${
                      venueFilter.has(v.code)
                        ? 'bg-white text-black border-white'
                        : 'bg-transparent text-[#888] border-[#333] hover:border-[#555]'
                    }`}
                  >
                    {v.name}
                  </button>
                ))}
              </div>
            </div>
          )}

          <button
            onClick={loadRaces}
            disabled={racesLoading}
            className="px-6 py-2.5 bg-[#1e1e1e] text-white text-sm rounded-lg hover:bg-[#2a2a2a] disabled:opacity-40 disabled:cursor-not-allowed transition-colors"
          >
            {racesLoading ? 'レース一覧取得中...' : 'レース一覧を取得'}
          </button>
        </div>

        {/* ── Layer 2: レース一覧 ── */}
        {/* スクレイプ進捗 */}
        {scrapeStatus === 'scraping' && (
          <div className="bg-[#0a1a2a] border border-[#1a3a5a] rounded-lg p-4 flex items-center gap-3">
            <div className="w-4 h-4 border-2 border-[#60a5fa] border-t-transparent rounded-full animate-spin shrink-0" />
            <span className="text-sm text-[#60a5fa]">{scrapeMessage || 'スクレイプ中...'}</span>
          </div>
        )}
        {scrapeStatus === 'error' && (
          <div className="bg-[#1a0a0a] border border-[#3a1a1a] rounded-lg p-4 text-sm text-[#f87171]">
            スクレイプエラー: {scrapeMessage}
          </div>
        )}
        {scrapeStatus === 'done' && (
          <div className="bg-[#052e10] border border-[#0a5a20] rounded-lg p-4 text-sm text-[#4ade80]">
            ✓ {scrapeMessage}
          </div>
        )}

        {/* データなし + スクレイプ誘導 */}
        {racesError && scrapeStatus === 'idle' && (
          <div className="bg-[#111] border border-[#1e1e1e] rounded-lg p-5 space-y-3">
            <p className="text-sm text-[#f87171]">{racesError}</p>
            <p className="text-xs text-[#666]">
              この日付のデータをローカルサーバーからスクレイプして取得できます。
              FastAPI（localhost:8000）が起動している必要があります。
            </p>
            <button
              onClick={triggerScrape}
              className="px-5 py-2.5 bg-[#1a3a5a] text-[#60a5fa] text-sm rounded-lg hover:bg-[#1e4a6a] transition-colors border border-[#2a5a8a]"
            >
              この日付をスクレイプして取得
            </button>
          </div>
        )}

        {filteredRaces.length > 0 && (
          <div className="bg-[#111] border border-[#1e1e1e] rounded-lg overflow-hidden">
            <div className="px-5 py-3 border-b border-[#1e1e1e] flex items-center justify-between">
              <span className="text-sm font-semibold text-white">② レース選択</span>
              <div className="flex items-center gap-3">
                <span className="text-xs text-[#555]">{selectedIds.size} / {filteredRaces.length} 選択</span>
                <button onClick={selectAll} className="text-xs text-[#888] hover:text-white transition-colors">全選択</button>
                <button onClick={deselectAll} className="text-xs text-[#888] hover:text-white transition-colors">全解除</button>
                <button
                  onClick={() => triggerScrape(true)}
                  disabled={scrapeStatus === 'scraping'}
                  title="DBのデータを強制上書きして最新オッズを取得"
                  className="flex items-center gap-1 text-xs text-[#60a5fa] hover:text-[#93c5fd] disabled:opacity-40 disabled:cursor-not-allowed transition-colors"
                >
                  <svg className="w-3 h-3" fill="none" viewBox="0 0 24 24" stroke="currentColor">
                    <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M4 4v5h.582m15.356 2A8.001 8.001 0 004.582 9m0 0H9m11 11v-5h-.581m0 0a8.003 8.003 0 01-15.357-2m15.357 2H15" />
                  </svg>
                  オッズ更新
                </button>
              </div>
            </div>

            <div className="divide-y divide-[#1a1a1a]">
              {filteredRaces.map(r => (
                <label
                  key={r.race_id}
                  className="flex items-center gap-4 px-5 py-3 hover:bg-[#161616] transition-colors cursor-pointer"
                >
                  <input
                    type="checkbox"
                    checked={selectedIds.has(r.race_id)}
                    onChange={() => toggleRace(r.race_id)}
                    className="w-4 h-4 accent-white"
                  />
                  <div className="flex-1 min-w-0">
                    <div className="flex items-center gap-2 flex-wrap">
                      <span className="text-xs text-[#555] font-mono">{r.venue}</span>
                      <span className="font-medium text-sm">{r.race_no}R</span>
                      {r.race_name && <span className="text-xs text-[#888] truncate">{r.race_name}</span>}
                    </div>
                    <div className="text-xs text-[#555] mt-0.5">
                      {r.track_type}{r.distance ? ` ${r.distance}m` : ''}{r.num_horses ? ` · ${r.num_horses}頭` : ''}
                    </div>
                  </div>
                  {results[r.race_id] && (
                    <span className={`text-xs px-2 py-0.5 rounded ${results[r.race_id].success ? 'bg-[#052e10] text-[#4ade80]' : 'bg-[#1a0505] text-[#f87171]'}`}>
                      {results[r.race_id].success ? '予測済' : 'エラー'}
                    </span>
                  )}
                </label>
              ))}
            </div>

            <div className="px-5 py-4 border-t border-[#1e1e1e]">
              <button
                onClick={handleBatchPredict}
                disabled={predicting || selectedIds.size === 0}
                className="w-full py-3 bg-white text-black font-medium rounded-lg hover:bg-[#eee] disabled:opacity-40 disabled:cursor-not-allowed transition-colors"
              >
                {predicting
                  ? `予測中... (${Object.keys(results).length}/${selectedIds.size})`
                  : `選択 ${selectedIds.size} レースを一括予測`}
              </button>
            </div>
          </div>
        )}

        {/* ── Layer 3: 一括予測結果 ── */}
        {Object.keys(results).length > 0 && (
          <div className="space-y-3">
            <h2 className="text-sm font-semibold text-white">③ 予測結果</h2>
            {filteredRaces
              .filter(r => results[r.race_id])
              .map(r => {
                const res = results[r.race_id]
                const isOpen = expandedRace === r.race_id
                const preds = res.data?.predictions || []
                const rec = res.data?.recommendation
                const raceLevel = res.data?.race_level ?? 'normal'

                return (
                  <div key={r.race_id} className="bg-[#111] border border-[#1e1e1e] rounded-lg overflow-hidden">
                    {/* ヘッダー（クリックで折りたたみ） */}
                    <button
                      onClick={() => setExpandedRace(isOpen ? null : r.race_id)}
                      className="w-full text-left px-5 py-3 flex items-center justify-between hover:bg-[#161616] transition-colors"
                    >
                      <div className="flex items-center gap-3">
                        <span className="text-xs text-[#555]">{r.venue}</span>
                        <span className="font-medium">{r.race_no}R</span>
                        {r.race_name && <span className="text-xs text-[#888]">{r.race_name}</span>}
                        {raceLevel === 'decisive' && <span className="text-xs text-yellow-400">🔥 勝負</span>}
                        {raceLevel === 'skip' && <span className="text-xs text-[#555]">見送り</span>}
                      </div>
                      <div className="flex items-center gap-3">
                        {!res.success && <span className="text-xs text-[#f87171]">エラー</span>}
                        {res.success && preds.length > 0 && (
                          <span className="text-xs text-[#4ade80]">
                            ◎{preds[0]?.horse_number}番 EV:{(preds[0]?.expected_value ?? 0).toFixed(2)}
                          </span>
                        )}
                        <svg className={`w-4 h-4 text-[#555] transition-transform ${isOpen ? 'rotate-180' : ''}`} fill="none" viewBox="0 0 24 24" stroke="currentColor">
                          <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M19 9l-7 7-7-7" />
                        </svg>
                      </div>
                    </button>

                    {isOpen && (
                      <div className="border-t border-[#1e1e1e]">
                        {!res.success ? (
                          <div className="px-5 py-4 text-sm text-[#f87171]">{res.error}</div>
                        ) : (
                          <>
                            {/* 予測テーブル */}
                            <div className="overflow-x-auto">
                              <table className="w-full text-sm">
                                <thead>
                                  <tr className="border-b border-[#1e1e1e]">
                                    {['順位', '馬番', '馬名', '騎手', '確率', '期待値', 'オッズ'].map(h => (
                                      <th key={h} className="px-4 py-2.5 text-left text-xs text-[#555] font-normal first:pl-5">{h}</th>
                                    ))}
                                  </tr>
                                </thead>
                                <tbody>
                                  {preds.map((p: any, i: number) => {
                                    const pNorm = p.p_norm ?? p.win_probability ?? 0
                                    const ev = p.expected_value ?? (pNorm * (p.odds ?? 0))
                                    const evColor = ev >= 1.2 ? 'text-[#4ade80]' : ev >= 1.0 ? 'text-[#facc15]' : 'text-[#888]'
                                    return (
                                      <tr key={i} className="border-b border-[#1a1a1a] hover:bg-[#161616] transition-colors">
                                        <td className="px-4 py-2.5 pl-5 text-[#888]">{p.predicted_rank ?? i + 1}位</td>
                                        <td className="px-4 py-2.5 font-bold">{p.horse_number ?? p.horse_no}</td>
                                        <td className="px-4 py-2.5">{p.horse_name}</td>
                                        <td className="px-4 py-2.5 text-[#888]">{p.jockey_name}</td>
                                        <td className="px-4 py-2.5 text-[#4ade80]">{(pNorm * 100).toFixed(1)}%</td>
                                        <td className={`px-4 py-2.5 font-medium ${evColor}`}>{ev.toFixed(2)}</td>
                                        <td className="px-4 py-2.5 text-[#888]">{p.odds}</td>
                                      </tr>
                                    )
                                  })}
                                </tbody>
                              </table>
                            </div>

                            {/* 購入推奨 */}
                            {rec && (
                              <div className="px-5 py-4 border-t border-[#1a1a1a] grid grid-cols-2 sm:grid-cols-4 gap-3">
                                <div className="bg-[#0a0a0a] border border-[#1e1e1e] rounded p-3">
                                  <div className="text-xs text-[#555] mb-1">推奨券種</div>
                                  <div className="text-sm font-bold">{res.data?.best_bet_type ?? '—'}</div>
                                </div>
                                <div className="bg-[#0a0a0a] border border-[#1e1e1e] rounded p-3">
                                  <div className="text-xs text-[#555] mb-1">単価 × 点数</div>
                                  <div className="text-sm font-bold">¥{rec.unit_price} × {rec.purchase_count}点</div>
                                </div>
                                <div className="bg-[#0a0a0a] border border-[#1e1e1e] rounded p-3">
                                  <div className="text-xs text-[#555] mb-1">合計投資</div>
                                  <div className="text-sm font-bold text-[#4ade80]">¥{rec.total_cost?.toLocaleString()}</div>
                                </div>
                                <div className="bg-[#0a0a0a] border border-[#1e1e1e] rounded p-3">
                                  <div className="text-xs text-[#555] mb-1">ケリー推奨額</div>
                                  <div className="text-sm font-bold">{rec.kelly_recommended_amount != null ? `¥${rec.kelly_recommended_amount?.toLocaleString()}` : '—'}</div>
                                </div>
                                {rec.strategy_explanation && (
                                  <div className="col-span-2 sm:col-span-4 text-xs text-[#666]">{rec.strategy_explanation}</div>
                                )}
                              </div>
                            )}
                          </>
                        )}
                      </div>
                    )}
                  </div>
                )
              })}
          </div>
        )}

        {/* ── ナビ ── */}
        <div className="p-5 bg-[#111] border border-[#1e1e1e] rounded-lg flex items-center justify-between gap-4">
          <div>
            <div className="text-xs text-[#666] mb-0.5">次のステップ — 04</div>
            <div className="text-sm font-medium">履歴・統計</div>
            <div className="text-xs text-[#555] mt-0.5">購入履歴と成績を確認します</div>
          </div>
          <Link
            href="/dashboard"
            className="shrink-0 flex items-center gap-1.5 bg-white text-black text-sm font-medium px-5 py-2.5 rounded hover:bg-[#eee] transition-colors"
          >
            履歴・統計へ
            <svg className="w-3.5 h-3.5" fill="none" viewBox="0 0 24 24" stroke="currentColor">
              <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M9 5l7 7-7 7" />
            </svg>
          </Link>
        </div>
      </main>
    </div>
  )
}

