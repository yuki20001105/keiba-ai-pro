'use client'

import { useState, useEffect, useCallback } from 'react'
import Link from 'next/link'
import { Logo } from '@/components/Logo'
import { Toast } from '@/components/Toast'
import { supabase } from '@/lib/supabase'
import { authFetch } from '@/lib/auth-fetch'
import { JRA_VENUES, todayStr, toInputDate, fromInputDate } from '@/lib/types'
import type { RaceItem } from '@/lib/types'
import { useScrape } from '@/hooks/useScrape'
import { useJobPoller } from '@/hooks/useJobPoller'
import { CACHE_TTL_MS } from '@/hooks/useRaceCache'

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
  const [predictProgress, setPredictProgress] = useState({ done: 0, total: 0, current: '' })
  const [results, setResults] = useState<Record<string, any>>({})
  const [purchased, setPurchased] = useState<Set<string>>(new Set())
  const [purchasing, setPurchasing] = useState<Set<string>>(new Set())
  const [bankroll, setBankroll] = useState(10000)
  const [riskMode, setRiskMode] = useState<'conservative' | 'balanced' | 'aggressive'>('balanced')
  const [expandedRace, setExpandedRace] = useState<string | null>(null)
  const [toast, setToast] = useState({ visible: false, message: '', type: 'success' as 'success' | 'error' })
  const [showBettingSettings, setShowBettingSettings] = useState(false)
  const showToast = (message: string, type: 'success' | 'error' = 'success') =>
    setToast({ visible: true, message, type })

  // リアルタイムオッズ
  const [realtimeOdds, setRealtimeOdds] = useState<Record<string, Record<string, number>>>({})
  const [oddsRefreshing, setOddsRefreshing] = useState(false)
  const [oddsLastUpdated, setOddsLastUpdated] = useState<Date | null>(null)

  // 当日・未来レースかどうか（過去レースはリアルタイムオッズ不可）
  const isCurrentOrFutureDate = date >= todayStr()

  // 購入推奨エクスポート
  const [exportLoading, setExportLoading] = useState(false)
  const [exportMinEv, setExportMinEv] = useState(1.0)
  const [exportMaxBets, setExportMaxBets] = useState(3)

  // スクレイプ（useScrape + useJobPoller）
  const scrape = useScrape()
  useJobPoller({
    jobId: scrape.jobId,
    getStatusUrl: id => `/api/scrape/status/${id}`,
    onCompleted: async () => {
      scrape.setStatus('done')
      scrape.setMessage('スクレイプ完了')
      await loadRaces()
    },
    onError: msg => {
      scrape.setStatus('error')
      scrape.setMessage(msg)
    },
  })

  useEffect(() => {
    loadModels()
    // 起動時にキャッシュを精査:
    //   当日レース → TTL 30分を超えたものを削除
    //   過去レース → 永続（削除しない）
    const todayStr = (() => {
      const t = new Date()
      return `${t.getFullYear()}${String(t.getMonth() + 1).padStart(2, '0')}${String(t.getDate()).padStart(2, '0')}`
    })()
    Object.keys(localStorage)
      .filter(k => k.startsWith('ra-cache:'))
      .forEach(k => {
        try {
          const parsed = JSON.parse(localStorage.getItem(k) ?? '{}')
          const raceDate: string = parsed?.data?.race_info?.date ?? ''
          const isTodayOrFuture = !raceDate || raceDate >= todayStr
          // 過去レースは削除しない。当日レースのみ TTL チェック。
          if (isTodayOrFuture && (!parsed.cachedAt || Date.now() - parsed.cachedAt > CACHE_TTL_MS)) {
            localStorage.removeItem(k)
          }
        } catch { localStorage.removeItem(k) }
      })
  }, [])

  const loadModels = async () => {
    try {
      const res = await authFetch('/api/models?ultimate=true')
      if (res.ok) {
        const data = await res.json()
        // model_id 降順（YYYYMMDD_HHMMSS 末尾）で最新モデルを先頭に
        const sorted = (data.models || []).sort((a: any, b: any) => b.model_id.localeCompare(a.model_id))
        setModels(sorted)
      }
    } catch {}
  }

  const loadRaces = useCallback(async () => {
    setRacesLoading(true)
    setRacesError('')
    setRaces([])
    setSelectedIds(new Set())
    setResults({})
    scrape.reset()
    try {
      const res = await authFetch(`/api/races/by-date?date=${date}`)
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

  const loadRacesWithAutoScrape = async () => {
    setRacesLoading(true)
    setRacesError('')
    setRaces([])
    setSelectedIds(new Set())
    setResults({})
    scrape.reset()
    try {
      const res = await authFetch(`/api/races/by-date?date=${date}`)
      const data = await res.json()
      if (!res.ok) throw new Error(data.detail || `HTTP ${res.status}`)
      const fetched = data.races || []
      setRaces(fetched)
      if (fetched.length === 0) {
        // DB になければ自動でスクレイプ開始
        setRacesLoading(false)
        scrape.startScrape({ startDate: date, endDate: date, force: false })
        return
      }
    } catch (e: any) {
      setRacesError(e.message)
    } finally {
      setRacesLoading(false)
    }
  }

  const triggerScrape = (force = false) => {
    scrape.startScrape({ startDate: date, endDate: date, force })
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
    if (selectedIds.size === 0) { showToast('予測するレースを選択してください', 'error'); return }
    const ids = Array.from(selectedIds)
    setPredicting(true)
    setResults({})
    setPredictProgress({ done: 0, total: ids.length, current: '' })

    let authHeaders: Record<string, string> = {}
    try {
      const { data: { session } } = await supabase.auth.getSession()
      if (session?.access_token) authHeaders = { Authorization: `Bearer ${session.access_token}` }
    } catch {}

    let done = 0
    let firstOk: string | undefined

    const CONCURRENCY = 1  // FastAPI は単一プロセス: 並列するとGIL競合でタイムアウト。逐次処理で2本目以降はHistキャッシュが効き高速化
    for (let i = 0; i < ids.length; i += CONCURRENCY) {
      const chunk = ids.slice(i, i + CONCURRENCY)
      await Promise.allSettled(
        chunk.map(async (raceId) => {
          const raceLabel = races.find(r => r.race_id === raceId)
          const label = raceLabel ? `${raceLabel.venue} ${raceLabel.race_no}R` : raceId
          setPredictProgress(prev => ({ ...prev, current: label }))
          try {
            const res = await fetch('/api/analyze-race', {
              method: 'POST',
              headers: { 'Content-Type': 'application/json', ...authHeaders },
              signal: AbortSignal.timeout(180000),  // 180s: 再スクレイプ込みで余裕を持たせる
              body: JSON.stringify({ race_id: raceId, model_id: modelId || null, bankroll: bankroll, risk_mode: riskMode }),
            })
            const data = await res.json()
            const result: { success: boolean; data?: any; error?: string } = res.ok
              ? { success: true, data }
              : { success: false, error: data.detail || `HTTP ${res.status}` }
            if (result.success && !firstOk) firstOk = raceId
            if (result.success && result.data) {
              try {
                const cachedAt = Date.now()
                // モデルIDなしキー（決打）
                localStorage.setItem(`ra-cache:${raceId}`, JSON.stringify({ data: result.data, cachedAt }))
                // モデルIDありキー（モデル別予測結果分析ページで即利用）
                if (modelId) {
                  localStorage.setItem(`ra-cache:${raceId}__${modelId}`, JSON.stringify({ data: result.data, cachedAt }))
                }
              } catch {}
            }
            setResults(prev => ({ ...prev, [raceId]: result }))
          } catch (e: any) {
            setResults(prev => ({ ...prev, [raceId]: { success: false, error: e.message } }))
          } finally {
            done++
            setPredictProgress(prev => ({ ...prev, done }))
          }
        })
      )
    }

    setPredictProgress(prev => ({ ...prev, done: ids.length, current: '' }))
    if (firstOk) setExpandedRace(firstOk)
    setPredicting(false)
  }

  const recordPurchase = async (raceId: string, venue: string, res: { success: boolean; data?: any; error?: string }, preds: any[]) => {
    if (!res.data) return
    const rec = res.data.recommendation
    const betType = res.data.best_bet_type || '単勝'
    const count: number = rec?.purchase_count ?? 1
    const combinations = preds.slice(0, count).map((p: any) => String(p.horse_number ?? p.horse_no ?? ''))
    const ev: number = preds[0]?.expected_value ?? 1.0
    setPurchasing(prev => new Set(prev).add(raceId))
    try {
      const body = {
        race_id: raceId,
        venue,
        bet_type: betType,
        combinations,
        strategy_type: rec?.strategy_explanation || 'AI推奨',
        purchase_count: rec?.purchase_count ?? 1,
        unit_price: rec?.unit_price ?? 100,
        total_cost: rec?.total_cost ?? 100,
        expected_value: ev,
        expected_return: (rec?.total_cost ?? 100) * ev,
      }
      const r = await fetch('/api/purchase', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(body),
      })
      if (r.ok) {
        setPurchased(prev => new Set(prev).add(raceId))
        showToast('購入を記録しました — ダッシュボードで結果を入力できます')
      } else {
        showToast('購入記録の保存に失敗しました', 'error')
      }
    } catch {
      showToast('購入記録の保存に失敗しました', 'error')
    } finally {
      setPurchasing(prev => { const s = new Set(prev); s.delete(raceId); return s })
    }
  }

  const handleRefreshOdds = async () => {
    const successIds = filteredRaces
      .filter(r => results[r.race_id]?.success)
      .map(r => r.race_id)
    if (successIds.length === 0) { showToast('先に予測を実行してください', 'error'); return }
    setOddsRefreshing(true)
    try {
      const res = await fetch('/api/realtime-odds/refresh', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ race_ids: successIds, types: 'tansho,umaren' }),
      })
      if (!res.ok) throw new Error(`HTTP ${res.status}`)
      // 各レースの最新オッズを取得してstateに格納
      const updated: Record<string, Record<string, number>> = { ...realtimeOdds }
      await Promise.all(successIds.map(async (raceId) => {
        try {
          const r = await fetch(`/api/realtime-odds/${raceId}?types=tansho`)
          if (r.ok) {
            const d = await r.json()
            updated[raceId] = d.odds?.tansho || {}
          }
        } catch {}
      }))
      setRealtimeOdds(updated)
      setOddsLastUpdated(new Date())
      showToast(`${successIds.length}レースのオッズを更新しました`)
    } catch (e: any) {
      showToast(`オッズ更新失敗: ${e.message}`, 'error')
    } finally {
      setOddsRefreshing(false)
    }
  }

  const handleExportJson = async () => {
    const successResults = Object.entries(results)
      .filter(([, r]) => r.success && r.data)
      .map(([, r]) => r.data)
    if (successResults.length === 0) { showToast('エクスポートできる予測結果がありません', 'error'); return }
    setExportLoading(true)
    try {
      const res = await fetch('/api/export/bet-list', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ results: successResults, bankroll, min_ev: exportMinEv, max_bets_per_race: exportMaxBets }),
      })
      if (!res.ok) throw new Error(`HTTP ${res.status}`)
      const data = await res.json()
      const blob = new Blob([JSON.stringify(data, null, 2)], { type: 'application/json' })
      const url = URL.createObjectURL(blob)
      const a = document.createElement('a')
      a.href = url
      a.download = `bet_list_${date}.json`
      a.click()
      URL.revokeObjectURL(url)
      showToast(`${data.summary.bets}件の買い目をエクスポートしました`)
    } catch (e: any) {
      showToast(`エクスポート失敗: ${e.message}`, 'error')
    } finally {
      setExportLoading(false)
    }
  }

  const handleExportCsv = async () => {
    const successResults = Object.entries(results)
      .filter(([, r]) => r.success && r.data)
      .map(([, r]) => r.data)
    if (successResults.length === 0) { showToast('エクスポートできる予測結果がありません', 'error'); return }
    setExportLoading(true)
    try {
      const res = await fetch('/api/export/bet-list?format=csv', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ results: successResults, bankroll, min_ev: exportMinEv, max_bets_per_race: exportMaxBets }),
      })
      if (!res.ok) throw new Error(`HTTP ${res.status}`)
      const text = await res.text()
      const blob = new Blob([new Uint8Array([0xEF, 0xBB, 0xBF]), text], { type: 'text/csv' })
      const url = URL.createObjectURL(blob)
      const a = document.createElement('a')
      a.href = url
      a.download = `bet_list_${date}.csv`
      a.click()
      URL.revokeObjectURL(url)
      showToast('CSVをダウンロードしました')
    } catch (e: any) {
      showToast(`CSV出力失敗: ${e.message}`, 'error')
    } finally {
      setExportLoading(false)
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
        <div className="bg-[#111] border border-[#1e1e1e] rounded-lg p-6 space-y-4">
          <div className="flex items-center justify-between">
            <h2 className="text-sm font-semibold text-white">① 日付・モデル設定</h2>
          </div>

          <div className="flex flex-col sm:flex-row gap-4">
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
                  <option key={i} value={m.model_id}>{m.target ?? ''} / {m.model_id.slice(0, 16)}... (AUC: {m.auc?.toFixed(3)})</option>
                ))}
              </select>
            </div>
          </div>

          {/* 賭け設定（折りたたみ） */}
          <div className="border border-[#1e1e1e] rounded-lg overflow-hidden">
            <button
              onClick={() => setShowBettingSettings(v => !v)}
              className="w-full flex items-center justify-between px-4 py-3 bg-[#0d0d0d] hover:bg-[#161616] transition-colors"
            >
              <span className="text-xs text-[#555]">
                賭け設定 — バンクロール ¥{bankroll.toLocaleString()} ·
                {riskMode === 'conservative' ? ' Steady (2%)' : riskMode === 'aggressive' ? ' Bold (5%)' : ' Smart (3.5%)'}
              </span>
              <svg className={`w-3 h-3 text-[#444] transition-transform ${showBettingSettings ? 'rotate-180' : ''}`} fill="none" viewBox="0 0 24 24" stroke="currentColor">
                <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M19 9l-7 7-7-7" />
              </svg>
            </button>
            {showBettingSettings && (
              <div className="px-4 pb-4 pt-3 border-t border-[#1e1e1e] space-y-4">
                <div className="grid grid-cols-1 sm:grid-cols-2 gap-4">
                  <div>
                    <label className="text-xs text-[#666] block mb-2">バンクロール（総資金）</label>
                    <div className="relative">
                      <span className="absolute left-4 top-1/2 -translate-y-1/2 text-[#555] text-sm">¥</span>
                      <input
                        type="number"
                        min={1000}
                        step={1000}
                        value={bankroll}
                        onChange={e => setBankroll(Math.max(1000, Number(e.target.value)))}
                        className="w-full pl-8 pr-4 py-3 bg-[#0a0a0a] border border-[#1e1e1e] rounded-lg text-white focus:outline-none focus:border-[#333] transition-colors"
                      />
                    </div>
                  </div>
                  <div>
                    <label className="text-xs text-[#666] block mb-2">リスクモード</label>
                    <div className="grid grid-cols-3 gap-2 h-[50px]">
                      {([
                        ['conservative', 'Steady', '2%'],
                        ['balanced',     'Smart',  '3.5%'],
                        ['aggressive',   'Bold',   '5%'],
                      ] as const).map(([mode, name, pct]) => (
                        <button
                          key={mode}
                          onClick={() => setRiskMode(mode)}
                          className={`flex flex-col items-center justify-center rounded-lg border transition-colors ${
                            riskMode === mode
                              ? mode === 'aggressive' ? 'bg-[#ef4444] text-white border-[#ef4444]'
                                : mode === 'conservative' ? 'bg-[#3b82f6] text-white border-[#3b82f6]'
                                : 'bg-white text-black border-white'
                              : 'bg-transparent text-[#888] border-[#2a2a2a] hover:border-[#444] hover:text-white'
                          }`}
                        >
                          <span className="text-xs font-semibold tracking-wide">{name}</span>
                          <span className={`text-[10px] mt-0.5 ${riskMode === mode ? 'opacity-70' : 'text-[#555]'}`}>{pct}</span>
                        </button>
                      ))}
                    </div>
                  </div>
                </div>
                <p className="text-xs text-[#444]">
                  1レース上限: ¥{(bankroll * ({ conservative: 0.02, balanced: 0.035, aggressive: 0.05 } as const)[riskMode]).toLocaleString()}
                </p>
              </div>
            )}
          </div>

          {/* 場所フィルター */}
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
            onClick={loadRacesWithAutoScrape}
            disabled={racesLoading || scrape.status === 'scraping'}
            className="px-6 py-2.5 bg-[#1e1e1e] text-white text-sm rounded-lg hover:bg-[#2a2a2a] disabled:opacity-40 disabled:cursor-not-allowed transition-colors"
          >
            {racesLoading ? 'レース一覧取得中...' : 'レース一覧を取得'}
          </button>
        </div>

        {/* ── Layer 2: レース一覧 ── */}
        {/* スクレイプ進捗 */}
        {scrape.status === 'scraping' && (
          <div className="bg-[#0a1a2a] border border-[#1a3a5a] rounded-lg p-4 flex items-center gap-3">
            <div className="w-4 h-4 border-2 border-[#60a5fa] border-t-transparent rounded-full animate-spin shrink-0" />
            <span className="text-sm text-[#60a5fa]">{scrape.message || 'スクレイプ中...'}</span>
          </div>
        )}
        {scrape.status === 'error' && (
          <div className="bg-[#1a0a0a] border border-[#3a1a1a] rounded-lg p-4 text-sm text-[#f87171]">
            スクレイプエラー: {scrape.message}
          </div>
        )}
        {scrape.status === 'done' && (
          <div className="bg-[#052e10] border border-[#0a5a20] rounded-lg p-4 text-sm text-[#4ade80]">
            ✓ {scrape.message}
          </div>
        )}

        {/* データなし + スクレイプ誘導 */}
        {racesError && scrape.status === 'idle' && (
          <div className="bg-[#111] border border-[#1e1e1e] rounded-lg p-5 space-y-3">
            <p className="text-sm text-[#f87171]">{racesError}</p>
            <p className="text-xs text-[#666]">
              この日付のデータをローカルサーバーからスクレイプして取得できます。
              FastAPI（localhost:8000）が起動している必要があります。
            </p>
            <button
              onClick={() => triggerScrape(false)}
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
                {isCurrentOrFutureDate && (
                  <button
                    onClick={() => triggerScrape(true)}
                    disabled={scrape.status === 'scraping'}
                    title="DBのデータを強制上書きして最新オッズを取得"
                    className="flex items-center gap-1 text-xs text-[#60a5fa] hover:text-[#93c5fd] disabled:opacity-40 disabled:cursor-not-allowed transition-colors"
                  >
                    <svg className="w-3 h-3" fill="none" viewBox="0 0 24 24" stroke="currentColor">
                      <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M4 4v5h.582m15.356 2A8.001 8.001 0 004.582 9m0 0H9m11 11v-5h-.581m0 0a8.003 8.003 0 01-15.357-2m15.357 2H15" />
                    </svg>
                    オッズ更新
                  </button>
                )}
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
              {/* 進捗バー */}
              {predicting && (
                <div className="mb-3 space-y-1.5">
                  <div className="flex justify-between items-center text-xs text-[#888]">
                    <span>{predictProgress.current ? `予測中: ${predictProgress.current}` : '予測中...'}</span>
                    <span className="tabular-nums">{predictProgress.done}/{predictProgress.total}</span>
                  </div>
                  <div className="w-full h-2 bg-[#1e1e1e] rounded-full overflow-hidden">
                    <div
                      className="h-full bg-white rounded-full transition-all duration-300"
                      style={{ width: predictProgress.total > 0 ? `${(predictProgress.done / predictProgress.total) * 100}%` : '0%' }}
                    />
                  </div>
                </div>
              )}
              <button
                onClick={handleBatchPredict}
                disabled={predicting || selectedIds.size === 0}
                className="w-full py-3 bg-white text-black font-medium rounded-lg hover:bg-[#eee] disabled:opacity-40 disabled:cursor-not-allowed transition-colors"
              >
                {predicting
                  ? `予測中... (${predictProgress.done}/${predictProgress.total})`
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
                                    {['順位', '馬番', '馬名', '騎手', '勝率', '複勝圏', 'アンサンブル', '期待値', 'オッズ'].map(h => (
                                      <th key={h} className="px-4 py-2.5 text-left text-xs text-[#555] font-normal first:pl-5">{h}</th>
                                    ))}
                                  </tr>
                                </thead>
                                <tbody>
                                  {(() => {
                                    const maxProb = Math.max(...preds.map((p: any) => p.p_norm ?? p.win_probability ?? 0), 0.001)
                                    return preds.map((p: any, i: number) => {
                                      const pNorm = p.p_norm ?? p.win_probability ?? 0
                                      const pPlace3: number | null = p.p_place3 ?? null
                                      const pEns: number = p.p_ensemble ?? pNorm
                                      const ev: number | null = p.expected_value ?? (p.odds != null ? pNorm * p.odds : null)
                                      const evColor = ev == null ? 'text-[#555]' : ev >= 1.2 ? 'text-[#4ade80]' : ev >= 1.0 ? 'text-[#facc15]' : 'text-[#888]'
                                      const pct = maxProb > 0 ? Math.round((pNorm / maxProb) * 100) : 0
                                      return (
                                        <tr key={i} className="border-b border-[#1a1a1a] hover:bg-[#161616] transition-colors">
                                          <td className="px-4 py-2.5 pl-5 text-[#888]">{p.predicted_rank ?? i + 1}位</td>
                                          <td className="px-4 py-2.5 font-bold">{p.horse_number ?? p.horse_no}</td>
                                          <td className="px-4 py-2.5">{p.horse_name}</td>
                                          <td className="px-4 py-2.5 text-[#888]">{p.jockey_name}</td>
                                          <td className="px-4 py-2.5">
                                            <div className="flex items-center gap-2">
                                              <div className="w-16 h-1.5 bg-[#1e1e1e] rounded-full overflow-hidden">
                                                <div className="h-full bg-[#4ade80] rounded-full" style={{ width: `${pct}%` }} />
                                              </div>
                                              <span className="text-[#4ade80] tabular-nums">{(pNorm * 100).toFixed(1)}%</span>
                                            </div>
                                          </td>
                                          <td className="px-4 py-2.5 tabular-nums text-[#60a5fa]">
                                            {pPlace3 != null ? `${(pPlace3 * 100).toFixed(1)}%` : '—'}
                                          </td>
                                          <td className="px-4 py-2.5 tabular-nums text-[#f472b6]">
                                            {(pEns * 100).toFixed(1)}%
                                          </td>
                                          <td className={`px-4 py-2.5 font-medium ${evColor}`}>{ev != null ? ev.toFixed(2) : '—'}</td>
                                          <td className="px-4 py-2.5 text-[#888]">{p.odds != null ? p.odds : '—'}</td>
                                        </tr>
                                      )
                                    })
                                  })()}
                                </tbody>
                              </table>
                            </div>

                            {/* 購入推奨 */}
                            {rec && (() => {
                              const bestType = res.data?.best_bet_type
                              const combos: any[] = bestType && res.data?.bet_types?.[bestType]
                                ? res.data.bet_types[bestType].slice(0, rec.purchase_count)
                                : []
                              return (
                                <div className="px-5 py-4 border-t border-[#1a1a1a] space-y-3">
                                  <div className="grid grid-cols-2 sm:grid-cols-4 gap-3">
                                    <div className="bg-[#0a0a0a] border border-[#1e1e1e] rounded p-3">
                                      <div className="text-xs text-[#555] mb-1">推奨券種</div>
                                      <div className="text-sm font-bold">{bestType ?? '—'}</div>
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
                                  </div>
                                  {combos.length > 0 && (
                                    <div className="bg-[#060606] border border-[#1a1a1a] rounded p-3">
                                      <div className="text-xs text-[#555] mb-2">買い目（{bestType}）</div>
                                      <div className="flex flex-wrap gap-2">
                                        {combos.map((c: any, ci: number) => (
                                          <span key={ci} className="text-xs px-2.5 py-1 bg-[#111] border border-[#333] rounded font-mono text-white">
                                            {c.combination}
                                          </span>
                                        ))}
                                      </div>
                                    </div>
                                  )}
                                  {rec.strategy_explanation && (
                                    <div className="text-xs text-[#666]">{rec.strategy_explanation}</div>
                                  )}
                                </div>
                              )
                            })()}

                            {/* 購入記録ボタン */}
                            {res.success && (
                              <div className="px-5 py-3 border-t border-[#1a1a1a] flex justify-end">
                                {purchased.has(r.race_id) ? (
                                  <div className="flex items-center gap-3 text-xs">
                                    <span className="text-[#4ade80] flex items-center gap-1">
                                      <svg className="w-3.5 h-3.5" fill="none" viewBox="0 0 24 24" stroke="currentColor">
                                        <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M5 13l4 4L19 7" />
                                      </svg>
                                      購入記録済み
                                    </span>
                                    <Link href="/dashboard" className="text-[#7dd3fc] hover:underline">
                                      ダッシュボードで結果入力 →
                                    </Link>
                                  </div>
                                ) : (
                                  <button
                                    onClick={() => recordPurchase(r.race_id, r.venue, res, preds)}
                                    disabled={purchasing.has(r.race_id)}
                                    className="text-xs px-4 py-1.5 bg-[#facc15] text-black font-medium rounded hover:bg-[#fde047] disabled:opacity-50 disabled:cursor-not-allowed transition-colors"
                                  >
                                    {purchasing.has(r.race_id) ? '保存中...' : '購入を記録する'}
                                  </button>
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

        {/* ── Layer 4: リアルタイムオッズ & 購入リストエクスポート ── */}
        {Object.values(results).some(r => r.success) && (
          <div className="bg-[#111] border border-[#1e1e1e] rounded-lg p-6 space-y-5">
            <h2 className="text-sm font-semibold text-white">④ リアルタイムオッズ更新 & 購入リスト出力</h2>

            {/* リアルタイムオッズ（当日・未来レースのみ有効） */}
            {isCurrentOrFutureDate ? (
            <div className="p-4 bg-[#0a0a0a] border border-[#1e1e1e] rounded-lg space-y-3">
              <div className="flex items-center justify-between">
                <div>
                  <p className="text-sm font-medium text-white">レース直前オッズ更新</p>
                  <p className="text-xs text-[#555] mt-0.5">
                    race.netkeiba.com から最新の単勝・馬連オッズを取得します（締切前のみ有効）
                  </p>
                </div>
                <button
                  onClick={handleRefreshOdds}
                  disabled={oddsRefreshing}
                  className="flex items-center gap-2 px-4 py-2 bg-[#1a3a5a] text-[#60a5fa] text-sm rounded-lg hover:bg-[#1e4a6a] disabled:opacity-50 disabled:cursor-not-allowed transition-colors border border-[#2a5a8a]"
                >
                  {oddsRefreshing ? (
                    <svg className="animate-spin h-4 w-4" viewBox="0 0 24 24" fill="none">
                      <circle className="opacity-25" cx="12" cy="12" r="10" stroke="currentColor" strokeWidth="4" />
                      <path className="opacity-75" fill="currentColor" d="M4 12a8 8 0 018-8V0C5.373 0 0 5.373 0 12h4z" />
                    </svg>
                  ) : (
                    <svg className="w-4 h-4" fill="none" viewBox="0 0 24 24" stroke="currentColor">
                      <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M4 4v5h.582m15.356 2A8.001 8.001 0 004.582 9m0 0H9m11 11v-5h-.581m0 0a8.003 8.003 0 01-15.357-2m15.357 2H15" />
                    </svg>
                  )}
                  {oddsRefreshing ? '更新中...' : 'オッズを今すぐ更新'}
                </button>
              </div>
              {oddsLastUpdated && (
                <p className="text-xs text-[#4ade80]">
                  最終更新: {oddsLastUpdated.toLocaleTimeString('ja-JP')} —{' '}
                  {Object.keys(realtimeOdds).length}レース取得済み
                </p>
              )}
              {Object.keys(realtimeOdds).length > 0 && (
                <div className="space-y-2 max-h-48 overflow-y-auto">
                  {filteredRaces
                    .filter(r => realtimeOdds[r.race_id] && Object.keys(realtimeOdds[r.race_id]).length > 0)
                    .map(r => {
                      const odds = realtimeOdds[r.race_id]
                      const sortedHorses = Object.entries(odds)
                        .sort(([, a], [, b]) => a - b)
                        .slice(0, 5)
                      return (
                        <div key={r.race_id} className="bg-[#111] border border-[#1a1a1a] rounded p-2">
                          <span className="text-xs text-[#888] mr-2">{r.venue} {r.race_no}R</span>
                          <span className="text-xs text-[#555]">単勝上位5頭:</span>
                          <div className="flex flex-wrap gap-1 mt-1">
                            {sortedHorses.map(([horseNo, od]) => (
                              <span key={horseNo} className="text-xs px-1.5 py-0.5 bg-[#0a0a0a] border border-[#222] rounded font-mono">
                                {horseNo}番 {od}倍
                              </span>
                            ))}
                          </div>
                        </div>
                      )
                    })}
                </div>
              )}
            </div>
            ) : (
              <div className="p-4 bg-[#0a0a0a] border border-[#1e1e1e] rounded-lg">
                <p className="text-xs text-[#555]">過去レースのため、リアルタイムオッズ更新は利用できません。確定オッズは予測時に自動取得されます。</p>
              </div>
            )}

            {/* 購入リスト出力 */}
            <div className="p-4 bg-[#0a0a0a] border border-[#1e1e1e] rounded-lg space-y-4">
              <div>
                <p className="text-sm font-medium text-white">購入推奨リスト出力</p>
                <p className="text-xs text-[#555] mt-0.5">
                  予測結果から「このレース・この馬番・この金額で買え」を一覧化してJSON/CSV出力します
                </p>
              </div>
              <div className="grid grid-cols-2 gap-3">
                <div>
                  <label className="text-xs text-[#666] block mb-1.5">最低期待値フィルタ</label>
                  <input
                    type="number"
                    min={0.5}
                    max={5.0}
                    step={0.1}
                    value={exportMinEv}
                    onChange={e => setExportMinEv(parseFloat(e.target.value) || 1.0)}
                    className="w-full px-3 py-2 bg-[#111] border border-[#1e1e1e] rounded text-white text-sm focus:outline-none focus:border-[#333]"
                  />
                  <p className="text-xs text-[#555] mt-1">期待値がこの値未満の買い目を除外</p>
                </div>
                <div>
                  <label className="text-xs text-[#666] block mb-1.5">レースあたり最大買い目数</label>
                  <input
                    type="number"
                    min={1}
                    max={10}
                    step={1}
                    value={exportMaxBets}
                    onChange={e => setExportMaxBets(parseInt(e.target.value) || 3)}
                    className="w-full px-3 py-2 bg-[#111] border border-[#1e1e1e] rounded text-white text-sm focus:outline-none focus:border-[#333]"
                  />
                </div>
              </div>
              <div className="flex gap-3">
                <button
                  onClick={handleExportJson}
                  disabled={exportLoading}
                  className="flex-1 py-2.5 bg-[#1a1a1a] border border-[#333] text-white text-sm rounded-lg hover:bg-[#222] disabled:opacity-50 disabled:cursor-not-allowed transition-colors flex items-center justify-center gap-2"
                >
                  <svg className="w-4 h-4" fill="none" viewBox="0 0 24 24" stroke="currentColor">
                    <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M4 16v1a3 3 0 003 3h10a3 3 0 003-3v-1m-4-4l-4 4m0 0l-4-4m4 4V4" />
                  </svg>
                  JSON 出力
                </button>
                <button
                  onClick={handleExportCsv}
                  disabled={exportLoading}
                  className="flex-1 py-2.5 bg-white text-black text-sm font-medium rounded-lg hover:bg-[#eee] disabled:opacity-50 disabled:cursor-not-allowed transition-colors flex items-center justify-center gap-2"
                >
                  {exportLoading ? (
                    <svg className="animate-spin h-4 w-4" viewBox="0 0 24 24" fill="none">
                      <circle className="opacity-25" cx="12" cy="12" r="10" stroke="currentColor" strokeWidth="4" />
                      <path className="opacity-75" fill="currentColor" d="M4 12a8 8 0 018-8V0C5.373 0 0 5.373 0 12h4z" />
                    </svg>
                  ) : (
                    <svg className="w-4 h-4" fill="none" viewBox="0 0 24 24" stroke="currentColor">
                      <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M9 17v-2m3 2v-4m3 4v-6m2 10H7a2 2 0 01-2-2V5a2 2 0 012-2h5.586a1 1 0 01.707.293l5.414 5.414a1 1 0 01.293.707V19a2 2 0 01-2 2z" />
                    </svg>
                  )}
                  CSV 出力（IPAT入力用）
                </button>
              </div>
              <p className="text-xs text-[#444]">
                CSVには馬券種コード（tan/umaren等）・馬番組み合わせ・金額が含まれます。IPAT手入力の補助に使用できます。
              </p>
            </div>
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

      <Toast
        message={toast.message}
        type={toast.type}
        isVisible={toast.visible}
        onClose={() => setToast(t => ({ ...t, visible: false }))}
      />
    </div>
  )
}

