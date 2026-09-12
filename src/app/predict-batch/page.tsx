'use client'

import { useState, useEffect, useCallback } from 'react'
import Link from 'next/link'
import { Logo } from '@/components/Logo'
import { Toast } from '@/components/Toast'
import { authFetch } from '@/lib/auth-fetch'
import { JRA_VENUES, todayStr, toInputDate, fromInputDate } from '@/lib/types'
import type { RaceItem } from '@/lib/types'
import { useScrape } from '@/hooks/useScrape'
import { useJobPoller } from '@/hooks/useJobPoller'
import { CACHE_TTL_MS } from '@/hooks/useRaceCache'
import { useAuth } from '@/contexts/AuthContext'
import { formatModelOptionLabel, type ModelSummary } from '@/lib/model-display'
import { RaceExplanationPanel } from '@/components/RaceExplanationPanel'
import type { RacePredictResult } from '@/lib/race-analysis-types'

export default function PredictBatchPage() {
  const { isPremium } = useAuth()
  const [date, setDate] = useState(todayStr())
  const [venueFilter, setVenueFilter] = useState<Set<string>>(new Set())
  const [races, setRaces] = useState<RaceItem[]>([])
  const [racesLoading, setRacesLoading] = useState(false)
  const [racesError, setRacesError] = useState('')
  const [selectedIds, setSelectedIds] = useState<Set<string>>(new Set())
  const [models, setModels] = useState<ModelSummary[]>([])
  const [modelId, setModelId] = useState<string>('')
  const [predicting, setPredicting] = useState(false)
  const [predictProgress, setPredictProgress] = useState({ done: 0, total: 0, current: '' })
  const [results, setResults] = useState<Record<string, any>>({})
  const [bankroll, setBankroll] = useState(10000)
  const [riskMode, setRiskMode] = useState<'conservative' | 'balanced' | 'aggressive'>('balanced')
  const [expandedRace, setExpandedRace] = useState<string | null>(null)
  const [explanationRaceId, setExplanationRaceId] = useState<string | null>(null)
  const [toast, setToast] = useState({ visible: false, message: '', type: 'success' as 'success' | 'error' })
  const [showBettingSettings, setShowBettingSettings] = useState(false)
  const showToast = (message: string, type: 'success' | 'error' = 'success') =>
    setToast({ visible: true, message, type })

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
        // API が bundle.created_at を正本として新しい順に返す。
        setModels(Array.isArray(data.models) ? data.models : [])
      }
    } catch {}
  }

  // キャッシュから予測結果を復元する
  const restoreResultsFromCache = useCallback((raceIds: string[]) => {
    const restored: Record<string, { success: boolean; data?: any; error?: string }> = {}
    raceIds.forEach(raceId => {
      try {
        const raw = localStorage.getItem(`ra-cache:${raceId}`)
        if (!raw) return
        const parsed = JSON.parse(raw)
        if (parsed?.data) {
          restored[raceId] = { success: true, data: parsed.data }
        }
      } catch {}
    })
    if (Object.keys(restored).length > 0) {
      setResults(restored)
    }
  }, [])

  const loadRaces = useCallback(async () => {
    setRacesLoading(true)
    setRacesError('')
    setRaces([])
    setSelectedIds(new Set())
    setResults({})
    setExplanationRaceId(null)
    scrape.reset()
    try {
      const res = await authFetch(`/api/races/by-date?date=${date}`)
      const data = await res.json()
      if (!res.ok) throw new Error(data.detail || `HTTP ${res.status}`)
      const fetched = data.races || []
      setRaces(fetched)
      if (fetched.length === 0) {
        setRacesError('該当日のデータがDBに見つかりません。')
      } else {
        restoreResultsFromCache(fetched.map((r: any) => r.race_id))
      }
    } catch (e: any) {
      setRacesError(e.message)
    } finally {
      setRacesLoading(false)
    }
  }, [date, restoreResultsFromCache])  

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
      } else {
        restoreResultsFromCache(fetched.map((r: any) => r.race_id))
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
    setExplanationRaceId(null)
    setPredictProgress({ done: 0, total: ids.length, current: '' })

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
            const res = await authFetch('/api/analyze-race', {
              method: 'POST',
              headers: { 'Content-Type': 'application/json' },
              signal: AbortSignal.timeout(180000),  // 180s: 再スクレイプ込みで余裕を持たせる
              body: JSON.stringify({
                race_id: raceId,
                model_id: modelId || null,
                bankroll,
                risk_mode: riskMode,
                include_explanation: isPremium,
              }),
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
                // API が実際に使用したモデルでも保存し、詳細画面で再予測しない。
                const resolvedModelId = result.data.model_id || modelId
                if (resolvedModelId) {
                  localStorage.setItem(`ra-cache:${raceId}__${resolvedModelId}`, JSON.stringify({ data: result.data, cachedAt }))
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
              <label htmlFor="prediction-model" className="text-xs text-[#666] block mb-2">使用モデル</label>
              <select
                id="prediction-model"
                value={modelId}
                onChange={e => setModelId(e.target.value)}
                className="w-full px-4 py-3 bg-[#0a0a0a] border border-[#1e1e1e] rounded-lg text-white focus:outline-none focus:border-[#333] transition-colors"
              >
                <option value="">既定モデル（使用中）</option>
                {models.map(m => (
                  <option key={m.model_id} value={m.model_id}>{formatModelOptionLabel(m)}</option>
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
              </div>
            </div>

            <div className="divide-y divide-[#1a1a1a]">
              {filteredRaces.map(r => (
                <div key={r.race_id}>
                <label
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
                  <div className="flex items-center gap-2">
                    {results[r.race_id] && (
                      <span className={`text-xs px-2 py-0.5 rounded ${results[r.race_id].success ? 'bg-[#052e10] text-[#4ade80]' : 'bg-[#1a0505] text-[#f87171]'}`}>
                        {results[r.race_id].success ? '予測済' : 'エラー'}
                      </span>
                    )}
                  </div>
                </label>
                </div>
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
            <h2 className="text-sm font-semibold text-white">③ 予測結果・スコア詳細</h2>
            {filteredRaces
              .filter(r => results[r.race_id])
              .map(r => {
                const res = results[r.race_id]
                const isOpen = expandedRace === r.race_id
                const preds = res.data?.predictions || []
                const rec = res.data?.recommendation
                const raceLevel = res.data?.race_level ?? 'normal'
                const confidenceValue = res.data?.pro_evaluation?.confidence
                const confidence = confidenceValue == null ? null : Number(confidenceValue)

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
                        {confidence !== null && Number.isFinite(confidence) && (
                          <span className="text-[10px] text-[#666]">信頼度 {Math.round(confidence * 100)}%</span>
                        )}
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
                                    {['順位', '馬番', '馬名', '騎手', 'スコア', '勝率', '複勝圏', 'アンサンブル', '期待値', 'オッズ', '人気'].map(h => (
                                      <th key={h} className="px-4 py-2.5 text-left text-xs text-[#555] font-normal first:pl-5">{h}</th>
                                    ))}
                                  </tr>
                                </thead>
                                <tbody>
                                  {(() => {
                                    const maxProb = Math.max(...preds.map((p: any) => p.p_norm ?? p.win_probability ?? 0), 0.001)
                                    return preds.map((p: any, i: number) => {
                                      const pNorm = p.p_norm ?? p.win_probability ?? 0
                                      const pRaw = p.p_raw ?? pNorm
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
                                          <td className="px-4 py-2.5 tabular-nums text-[#7dd3fc]">{(pRaw * 100).toFixed(1)}</td>
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
                                          <td className="px-4 py-2.5 text-[#888]">{p.popularity ?? '—'}</td>
                                        </tr>
                                      )
                                    })
                                  })()}
                                </tbody>
                              </table>
                            </div>

                            {isPremium && (
                              <div className="flex justify-end px-5 py-3 border-t border-[#1a1a1a]">
                                <button
                                  type="button"
                                  aria-expanded={explanationRaceId === r.race_id}
                                  onClick={() => setExplanationRaceId(current => current === r.race_id ? null : r.race_id)}
                                  className="inline-flex items-center gap-1.5 text-xs text-[#7dd3fc] hover:text-[#bae6fd] transition-colors"
                                >
                                  {explanationRaceId === r.race_id ? 'AIの判断を閉じる' : 'AIの判断を見る'}
                                  <span className="rounded border border-[#3b2f64] bg-[#211b36] px-1.5 py-0.5 text-[9px] text-[#c4b5fd]">
                                    Premium
                                  </span>
                                </button>
                              </div>
                            )}

                            {isPremium && explanationRaceId === r.race_id && res.data && (
                              <div className="border-t border-[#1a1a1a] bg-[#0d0d0d]">
                                <RaceExplanationPanel result={res.data as RacePredictResult} />
                              </div>
                            )}

                            {/* AIが決定した買い目の読み取り専用表示 */}
                            {res.success && (() => {
                              const betType = String(res.data?.best_bet_type || '単勝')
                              const candidates: any[] = res.data?.bet_types?.[betType] ?? []
                              const requestedCount = Math.max(0, Number(rec?.purchase_count ?? 0))
                              const selectedCombos = candidates.slice(0, Math.min(requestedCount, candidates.length))
                              const unitPrice = Math.max(0, Number(rec?.unit_price ?? 0))
                              const totalCost = unitPrice * selectedCombos.length
                              const isSkip = raceLevel === 'skip' || selectedCombos.length === 0
                              return (
                                <div className="border-t border-[#1a1a1a]">

                                  <div className="px-5 pt-4 pb-3 border-b border-[#141414] flex items-center gap-3">
                                    <span className="text-[10px] font-semibold tracking-wider text-[#facc15] uppercase">AI 買い目</span>
                                    <span className="text-sm font-semibold text-white">{isSkip ? '見送り' : betType}</span>
                                  </div>

                                  <div className="divide-y divide-[#141414]">

                                    {/* ── AIが選んだ組み合わせ ── */}
                                    <div className="px-5 py-3 space-y-2">
                                      {!isSkip ? (
                                        <div className="flex flex-wrap gap-1.5">
                                          {selectedCombos.map((combo: any, index: number) => {
                                            const expectedValue = combo.expected_value ?? combo.ev
                                            return (
                                              <span
                                                key={`${String(combo.combination ?? combo)}-${index}`}
                                                className="text-xs px-2.5 py-1 rounded font-mono border bg-[#1a2a10] text-[#facc15] border-[#3a4a10]"
                                              >
                                                {String(combo.combination ?? combo)}
                                                {expectedValue != null && (
                                                  <span className="ml-1 opacity-50 text-[10px]">EV {Number(expectedValue).toFixed(2)}</span>
                                                )}
                                              </span>
                                            )
                                          })}
                                        </div>
                                      ) : (
                                        <p className="text-xs text-[#666]">購入条件に達していません</p>
                                      )}
                                    </div>

                                  </div>

                                  {!isSkip && (
                                    <div className="px-5 py-3 border-t border-[#141414] flex flex-wrap items-center gap-2 text-xs">
                                      <span className="text-[#888]">¥{unitPrice.toLocaleString()} × {selectedCombos.length}点</span>
                                      <span className="text-[#444]">=</span>
                                      <span className="text-white font-bold">合計 ¥{totalCost.toLocaleString()}</span>
                                    </div>
                                  )}
                                </div>
                              )
                            })()}
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
            <div className="text-sm font-medium">成績確認</div>
            <div className="text-xs text-[#555] mt-0.5">予測結果と成績を確認します</div>
          </div>
          <Link
            href="/dashboard"
            className="shrink-0 flex items-center gap-1.5 bg-white text-black text-sm font-medium px-5 py-2.5 rounded hover:bg-[#eee] transition-colors"
          >
            成績確認へ
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

