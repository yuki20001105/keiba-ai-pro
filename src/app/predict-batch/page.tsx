'use client'

import { useState, useEffect, useCallback, useMemo, useRef } from 'react'
import Link from 'next/link'
import { Logo } from '@/components/Logo'
import { Toast } from '@/components/Toast'
import { authFetch } from '@/lib/auth-fetch'
import { JRA_VENUES, todayStr, toInputDate, fromInputDate } from '@/lib/types'
import type { RaceItem } from '@/lib/types'
import { useScrape } from '@/hooks/useScrape'
import { useJobPoller } from '@/hooks/useJobPoller'
import { useAuth } from '@/contexts/AuthContext'
import { formatModelOptionLabel, type ModelSummary } from '@/lib/model-display'
import { RaceExplanationPanel } from '@/components/RaceExplanationPanel'
import { PredictionInputNotice } from '@/components/PredictionInputNotice'
import type { RacePredictResult, Recommendation } from '@/lib/race-analysis-types'
import { BettingRecommendationPanel, bettingDecisionLabel } from '@/components/BettingRecommendationPanel'
import { allocateDayBudget, isCurrentBettingCache, perRaceCeiling, RISK_MODES, roundBetBudget, safeRecommendationForInput, selectedTickets } from '@/lib/betting-policy'
import { predictionInputVerified } from '@/lib/prediction-input-contract'
import { predictionExpectationDisplay, REFERENCE_EXPECTATION_CAVEAT, REFERENCE_EXPECTATION_NOTE } from '@/lib/prediction-expectation-display'
import { scrapeCompletionOutcome, type ScrapeCompletionOutcome } from '@/lib/scrape-completion-outcome'
import { buildScrapeProgressDisplay } from '@/lib/scrape-progress-display'
import { useBettingClock } from '@/hooks/useBettingClock'
import { savePerformanceRecord } from '@/lib/performance-records'
import { hasBoundedPredictionRuntime, predictionErrorMessage, predictionTimingLabel, resolvePredictionConcurrency, runPredictionQueue } from '@/lib/prediction-queue'
import { parsePredictionFreshness, predictionCacheStatus, readPredictionCache, savedPredictionForDisplay, supportsSavedDbRecompute, writePredictionCache, type PredictionFreshness } from '@/lib/prediction-freshness'
import { PredictionFreshnessNotice } from '@/components/PredictionFreshnessNotice'

export default function PredictBatchPage() {
  const { isPremium, userId, loading: authLoading } = useAuth()
  const [date, setDate] = useState(todayStr())
  const [venueFilter, setVenueFilter] = useState<Set<string>>(new Set())
  const [races, setRaces] = useState<RaceItem[]>([])
  const [racesLoading, setRacesLoading] = useState(false)
  const [racesError, setRacesError] = useState('')
  const [racesMissing, setRacesMissing] = useState(false)
  const [scrapeOutcome, setScrapeOutcome] = useState<ScrapeCompletionOutcome | null>(null)
  const [scrapeStatusData, setScrapeStatusData] = useState<unknown>(null)
  const activeScrapeDate = useRef<string | null>(null)
  const viewGeneration = useRef(0)
  const raceReadGeneration = useRef(0)
  const [selectedIds, setSelectedIds] = useState<Set<string>>(new Set())
  const [models, setModels] = useState<ModelSummary[]>([])
  const [modelId, setModelId] = useState<string>('')
  const [predicting, setPredicting] = useState(false)
  const [predictProgress, setPredictProgress] = useState({ done: 0, total: 0, active: 0, current: '' })
  const [predictElapsedMs, setPredictElapsedMs] = useState(0)
  const [predictConcurrency, setPredictConcurrency] = useState(1)
  const [predictionRuntimeNotice, setPredictionRuntimeNotice] = useState('')
  const predictionGeneration = useRef(0)
  const predictionController = useRef<AbortController | null>(null)
  const predictionRunning = useRef(false)
  const predictionStartedAt = useRef(0)
  const mounted = useRef(true)
  const previousOwner = useRef(userId)
  const [results, setResults] = useState<Record<string, any>>({})
  const [frozenRecommendations, setFrozenRecommendations] = useState<Record<string, Recommendation | null | undefined> | null>(null)
  const bettingNow = useBettingClock(Object.keys(results).length > 0)
  const [bankroll, setBankroll] = useState(10000)
  const [riskMode, setRiskMode] = useState<'conservative' | 'balanced' | 'aggressive'>('balanced')
  const [dayBudget, setDayBudget] = useState(1000)
  const [bettingNeedsRefresh, setBettingNeedsRefresh] = useState(false)
  const [performanceNotice, setPerformanceNotice] = useState('')
  const [performanceError, setPerformanceError] = useState(false)
  const [expandedRace, setExpandedRace] = useState<string | null>(null)
  const [explanationRaceId, setExplanationRaceId] = useState<string | null>(null)
  const [toast, setToast] = useState({ visible: false, message: '', type: 'success' as 'success' | 'error' })
  const [showBettingSettings, setShowBettingSettings] = useState(false)
  const showToast = (message: string, type: 'success' | 'error' = 'success') =>
    setToast({ visible: true, message, type })

  const stopPredictionRun = useCallback(() => {
    ++predictionGeneration.current
    predictionController.current?.abort()
    predictionController.current = null
    predictionRunning.current = false
  }, [])

  useEffect(() => {
    mounted.current = true
    return () => {
      mounted.current = false
      stopPredictionRun()
    }
  }, [stopPredictionRun])

  useEffect(() => {
    if (previousOwner.current === userId) return
    previousOwner.current = userId
    ++raceReadGeneration.current
    stopPredictionRun()
    setPredicting(false)
    setRacesLoading(false)
    setRaces([])
    setSelectedIds(new Set())
    setResults({})
    setFrozenRecommendations(null)
    setPerformanceNotice('')
  }, [userId, stopPredictionRun])

  useEffect(() => {
    if (!predicting) return
    const timer = setInterval(() => setPredictElapsedMs(Date.now() - predictionStartedAt.current), 1000)
    return () => clearInterval(timer)
  }, [predicting])

  const cancelPrediction = useCallback(() => {
    stopPredictionRun()
    setPredicting(false)
    setPredictProgress({ done: 0, total: 0, active: 0, current: '' })
    setPredictElapsedMs(0)
    setPredictionRuntimeNotice('')
  }, [stopPredictionRun])

  // スクレイプ（useScrape + useJobPoller）
  const scrape = useScrape()
  useJobPoller({
    jobId: scrape.jobId,
    getStatusUrl: id => `/api/scrape/status/${id}`,
    onCompleted: async data => {
      if (activeScrapeDate.current !== date || (data.job_id && data.job_id !== scrape.jobId)) return
      const generation = viewGeneration.current
      scrape.setStatus('done')
      setScrapeStatusData(data)
      const outcome = scrapeCompletionOutcome(data)
      // Confirm usable rows before claiming success; retain failures during the read.
      setScrapeOutcome(outcome.kind === 'warning' ? outcome : null)
      const count = await loadRaces()
      if (generation === viewGeneration.current && count !== null) {
        setScrapeOutcome(scrapeCompletionOutcome(data, count))
      }
    },
    onProgress: (message, data) => {
      if (activeScrapeDate.current !== date || (data.job_id && data.job_id !== scrape.jobId)) return
      scrape.setStatus('scraping')
      scrape.setMessage(message)
      setScrapeStatusData(data)
    },
    onError: msg => {
      if (activeScrapeDate.current !== date) return
      scrape.setStatus('error')
      scrape.setMessage(msg)
    },
  })

  useEffect(() => {
    loadModels()
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
  const restoreResultsFromCache = useCallback(async (raceIds: string[], generation: number) => {
    const entries = raceIds.map(raceId => ({ raceId, parsed: readPredictionCache(modelId ? `${raceId}__${modelId}` : raceId, userId) }))
    if (!entries.some(entry => entry.parsed)) return
    let freshness: PredictionFreshness | null = null
    try {
      const res = await authFetch(`/api/prediction-freshness${modelId ? `?model_id=${encodeURIComponent(modelId)}` : ''}`, {
        cache: 'no-store', signal: AbortSignal.timeout(5000),
      })
      if (res.ok) {
        const data = await res.json()
        freshness = parsePredictionFreshness(data.freshness ?? data)
      }
    } catch { /* Old/offline servers cannot verify saved predictions. */ }
    if (generation !== raceReadGeneration.current) return
    const restored: Record<string, { success: boolean; data?: any; error?: string; cacheStatus?: string; cachedAt?: number }> = {}
    let staleRecommendation = false
    entries.forEach(({ raceId, parsed }) => {
      try {
        if (!parsed) return
        if (parsed?.data && (!modelId || parsed.data.model_id === modelId)) {
          const allocation = parsed.data.performance_allocation
          const settingsMatch = (!allocation || allocation.day_budget === Math.min(dayBudget, bankroll))
            && isCurrentBettingCache(parsed.data.recommendation, bankroll, riskMode, parsed.data.input_diagnostics)
          const cacheStatus = settingsMatch ? predictionCacheStatus(parsed.data, freshness, parsed.cachedAt, parsed.ownerMatches) : 'stale'
          restored[raceId] = { success: true, data: savedPredictionForDisplay(parsed.data, cacheStatus), cacheStatus, cachedAt: parsed.cachedAt }
          if (cacheStatus !== 'current') staleRecommendation = true
          if (!allocation) staleRecommendation = true
        } else if (parsed?.data) {
          staleRecommendation = true
        }
      } catch {}
    })
    if (Object.keys(restored).length > 0) {
      // Old raw caches may show rankings but must not become an actual recording source.
      const fixed = allocateDayBudget(Object.fromEntries(Object.entries(restored).map(([id, result]) => [id, result.data.recommendation])), Math.min(dayBudget, bankroll))
      for (const [id, result] of Object.entries(restored)) {
        const previous = result.data.recommendation
        if (JSON.stringify(previous?.tickets) !== JSON.stringify(fixed[id]?.tickets)) {
          result.data.performance_snapshot_id = null
          staleRecommendation = true
        }
        result.data.recommendation = fixed[id]
      }
      setFrozenRecommendations(fixed)
      setResults(restored)
    }
    if (staleRecommendation) setBettingNeedsRefresh(true)
  }, [bankroll, riskMode, modelId, dayBudget, userId])

  // Returning from acquisition/training checks metadata only; never auto-run.
  useEffect(() => {
    if (!Object.keys(results).length) return
    let disposed = false
    let checking = false
    const check = async () => {
      if (disposed || checking || predictionRunning.current || document.visibilityState === 'hidden') return
      checking = true
      const generation = raceReadGeneration.current
      const predictionVersion = predictionGeneration.current
      let current: PredictionFreshness | null = null
      try {
        const response = await authFetch(`/api/prediction-freshness${modelId ? `?model_id=${encodeURIComponent(modelId)}` : ''}`, {
          cache: 'no-store', signal: AbortSignal.timeout(5000),
        })
        if (response.ok) { const data = await response.json(); current = parsePredictionFreshness(data.freshness ?? data) }
      } catch { /* Fail closed for cache authority, preserve rankings. */ }
      checking = false
      if (disposed || generation !== raceReadGeneration.current || predictionVersion !== predictionGeneration.current) return
      let stale = false
      const next = Object.fromEntries(Object.entries(results).map(([id, result]) => {
        if (!result.success || !result.data) return [id, result]
        const age = result.cachedAt ?? Date.parse(result.data.freshness?.generated_at ?? '')
        const status = predictionCacheStatus(result.data, current, age, !!userId)
        if (status === 'current') return [id, result]
        stale = true
        return [id, { ...result, data: savedPredictionForDisplay(result.data, status), cacheStatus: status, cachedAt: age }]
      }))
      if (stale) { setResults(next); setFrozenRecommendations(null); setBettingNeedsRefresh(true) }
    }
    const onReturn = () => { void check() }
    window.addEventListener('focus', onReturn)
    document.addEventListener('visibilitychange', onReturn)
    return () => {
      disposed = true
      window.removeEventListener('focus', onReturn)
      document.removeEventListener('visibilitychange', onReturn)
    }
  }, [results, modelId, userId])

  const invalidateBettingResults = () => {
    ++raceReadGeneration.current
    if (predictionRunning.current) cancelPrediction()
    setFrozenRecommendations(null)
    setResults({})
    setExplanationRaceId(null)
    setBettingNeedsRefresh(true)
    // Changing a view setting never deletes other races/users or recorded selections.
  }

  const loadRaces = useCallback(async () => {
    if (predictionRunning.current) cancelPrediction()
    const generation = ++raceReadGeneration.current
    setRacesLoading(true)
    setRacesError('')
    setRacesMissing(false)
    setSelectedIds(new Set())
    setResults({})
    setExpandedRace(null)
    setFrozenRecommendations(null)
    setExplanationRaceId(null)
    try {
      const res = await authFetch(`/api/races/by-date?date=${date}`, { cache: 'no-store' })
      const data = await res.json()
      if (generation !== raceReadGeneration.current) return null
      if (!res.ok) throw new Error(data.detail || `HTTP ${res.status}`)
      if (data.error || !Array.isArray(data.races)) {
        throw new Error('保存データを読み込めませんでした。再読み込みしてください。')
      }
      const fetched = data.races
      setRaces(fetched)
      if (fetched.length === 0) {
        setRacesMissing(true)
      } else {
        await restoreResultsFromCache(fetched.map((r: any) => r.race_id), generation)
      }
      return fetched.length as number
    } catch (e: any) {
      if (generation === raceReadGeneration.current) setRacesError(e.message)
      return null
    } finally {
      if (generation === raceReadGeneration.current) setRacesLoading(false)
    }
  }, [cancelPrediction, date, restoreResultsFromCache])

  const triggerScrape = (force = false) => {
    ++viewGeneration.current
    activeScrapeDate.current = date
    setScrapeOutcome(null)
    setRacesError('')
    setScrapeStatusData(null)
    scrape.startScrape({ startDate: date, endDate: date, force })
  }

  const changeDate = (nextDate: string) => {
    cancelPrediction()
    ++viewGeneration.current
    ++raceReadGeneration.current
    activeScrapeDate.current = null
    setDate(nextDate)
    scrape.reset()
    setScrapeOutcome(null)
    setScrapeStatusData(null)
    setRaces([])
    setRacesMissing(false)
    setRacesError('')
    setRacesLoading(false)
    setSelectedIds(new Set())
    setResults({})
    setFrozenRecommendations(null)
    setExplanationRaceId(null)
  }

  // 場所フィルター適用後のレース一覧
  const filteredRaces = venueFilter.size === 0
    ? races
    : races.filter(r => venueFilter.has(r.venue_code))
  const scrapeDisplay = buildScrapeProgressDisplay(scrapeStatusData)
  const provisionalRecommendations = useMemo(() => allocateDayBudget(Object.fromEntries(
    Object.entries(results).filter(([, result]) => result.success).map(([id, result]) => [id, safeRecommendationForInput(result.data?.recommendation as Recommendation | undefined, result.data?.input_diagnostics)]),
  ), Math.min(dayBudget, bankroll)), [results, dayBudget, bankroll])
  const dayRecommendations = frozenRecommendations ?? provisionalRecommendations
  const dayTotal = Object.values(dayRecommendations).reduce((sum, rec) => sum + selectedTickets(rec, bettingNow).reduce((cost, ticket) => cost + ticket.stake, 0), 0)

  const toggleVenue = (code: string) => {
    setVenueFilter(prev => {
      const n = new Set(prev)
      if (n.has(code)) n.delete(code)
      else n.add(code)
      return n
    })
  }

  const toggleRace = (id: string) => {
    setSelectedIds(prev => {
      const n = new Set(prev)
      if (n.has(id)) n.delete(id)
      else n.add(id)
      return n
    })
  }

  const selectAll = () => setSelectedIds(new Set(filteredRaces.map(r => r.race_id)))
  const deselectAll = () => setSelectedIds(new Set())

  const handleBatchPredict = async () => {
    // React state updates are asynchronous: the ref also rejects two clicks in the same frame.
    if (predictionRunning.current) return
    if (selectedIds.size === 0) { showToast('予測するレースを選択してください', 'error'); return }
    const ids = Array.from(selectedIds)
    const savedBeforeRefresh = results
    predictionRunning.current = true
    const generation = ++predictionGeneration.current
    const controller = new AbortController()
    predictionController.current = controller
    predictionStartedAt.current = Date.now()
    const current = () => mounted.current && generation === predictionGeneration.current && !controller.signal.aborted
    setPredicting(true)
    setPredictElapsedMs(0)
    setPredictConcurrency(1)
    setPredictionRuntimeNotice('')
    setPerformanceNotice('')
    setPerformanceError(false)
    setResults({})
    setFrozenRecommendations(null)
    setBettingNeedsRefresh(false)
    setExplanationRaceId(null)
    setPredictProgress({ done: 0, total: ids.length, active: 0, current: '実行環境を確認中' })

    let firstOk: string | undefined
    const completedResults: Record<string, { success: boolean; data?: RacePredictResult; error?: string }> = {}

    try {
      const capabilityResponse = await authFetch(`/api/prediction-freshness${modelId ? `?model_id=${encodeURIComponent(modelId)}` : ''}`, {
        cache: 'no-store', signal: AbortSignal.any([controller.signal, AbortSignal.timeout(5000)]),
      })
      const capability = capabilityResponse.ok ? await capabilityResponse.json() : null
      if (!supportsSavedDbRecompute(capability)) throw new Error('最新データで再予測できるか確認できません。APIの更新・接続状態を確認してください。')
      if (!current()) return
      let runtime: unknown = null
      try {
        const health = await authFetch('/api/health', {
          cache: 'no-store', signal: AbortSignal.any([controller.signal, AbortSignal.timeout(5000)]),
        })
        if (health.ok) runtime = await health.json()
      } catch { /* Offline/old servers stay sequential; never infer CPU isolation from the UI build. */ }
      if (!current()) return
      const concurrency = resolvePredictionConcurrency(runtime)
      setPredictConcurrency(concurrency)
      setPredictionRuntimeNotice(hasBoundedPredictionRuntime(runtime) ? '' : 'サーバー更新待ち · 1件ずつ')
      setPredictProgress(prev => ({ ...prev, current: '' }))
      await runPredictionQueue<string, { success: boolean; data?: RacePredictResult; error?: string }>(ids, async raceId => {
        const res = await authFetch('/api/analyze-race', {
              method: 'POST',
              headers: { 'Content-Type': 'application/json' },
              signal: AbortSignal.any([controller.signal, AbortSignal.timeout(180000)]),
              body: JSON.stringify({
                race_id: raceId,
                model_id: modelId || null,
                bankroll,
                risk_mode: riskMode,
                include_explanation: isPremium,
                force_recompute: true,
                data_refresh: 'saved_db',
              }),
            })
        const data = await res.json()
        return res.ok && data.success !== false
          ? { success: true, data: data as RacePredictResult }
          : { success: false, error: predictionErrorMessage(data, res.status) }
      }, {
        concurrency,
        signal: controller.signal,
        onProgress: progress => {
          if (!current()) return
          setPredictProgress(prev => ({ ...prev, done: progress.completed, total: progress.total, active: progress.active }))
          setPredictElapsedMs(Date.now() - predictionStartedAt.current)
        },
        onSettled: (outcome, raceId) => {
          if (!current()) return
          const result: { success: boolean; data?: RacePredictResult; error?: string } = outcome.status === 'fulfilled' ? outcome.value
            : { success: false, error: outcome.reason instanceof Error ? outcome.reason.message : '予測に失敗しました。' }
          completedResults[raceId] = result
          if (result.success && !firstOk) firstOk = raceId
          if (result.success && result.data) {
            try {
              const cachedAt = Date.now()
              // Do not expose unallocated stakes to a detail view during the batch.
              const provisional: RacePredictResult = { ...result.data, performance_snapshot_id: null,
                performance_recording_error: 'この日の配分を確認中です。',
                recommendation: result.data.recommendation ? { ...result.data.recommendation, decision: 'skip', action: '見送り', reason: 'この日の配分を確認中', tickets: [], total_cost: 0, purchase_count: 0, unit_price: 0, expected_return: 0 } : null }
              writePredictionCache(raceId, provisional, cachedAt, userId)
              const resolvedModelId = result.data.model_id || modelId
              if (resolvedModelId) {
                writePredictionCache(`${raceId}__${resolvedModelId}`, provisional, cachedAt, userId)
              }
            } catch {}
          }
          setResults(prev => ({ ...prev, [raceId]: result }))
        },
      })
      if (!current()) return

      // Freeze the final day's allocation once. Cache restoration and clock ticks never write records.
      const allocationTime = Date.now()
      const finalRecommendations = allocateDayBudget(Object.fromEntries(
        Object.entries(completedResults).filter(([, result]) => result.success)
          .map(([id, result]) => [id, safeRecommendationForInput(result.data?.recommendation, result.data?.input_diagnostics)]),
      ), Math.min(dayBudget, bankroll), allocationTime)
      const successes = Object.entries(completedResults).filter(([, result]) => result.success && result.data)
      setFrozenRecommendations(finalRecommendations)
      for (const [id, result] of successes) {
        result.data = { ...result.data!, recommendation: finalRecommendations[id] ?? null,
          performance_allocation: { day_budget: Math.min(dayBudget, bankroll), allocated_at: new Date(allocationTime).toISOString() } }
        try {
          const cachedAt = Date.now()
          writePredictionCache(id, result.data!, cachedAt, userId)
          const resolvedModelId = result.data.model_id || modelId
          if (resolvedModelId) writePredictionCache(`${id}__${resolvedModelId}`, result.data!, cachedAt, userId)
        } catch { /* The frozen in-memory selection remains available. */ }
      }
      setResults({ ...completedResults })
      if (successes.length > 0) {
        setPredictProgress(prev => ({ ...prev, current: '成績を記録中' }))
        // SQLite writes and auth checks also stay bounded rather than bursting all 24 requests.
        const { outcomes } = await runPredictionQueue(successes, ([id, result]) =>
          savePerformanceRecord(result.data?.performance_snapshot_id, finalRecommendations[id], 'paper', allocationTime),
        { concurrency, signal: controller.signal })
        if (!current()) return
        const failed = outcomes.filter(result => result?.status === 'rejected').length
        const alreadyRecorded = outcomes.filter(result => result?.status === 'fulfilled' && result.value.existing).length
        outcomes.forEach((saved, index) => {
          if (saved?.status !== 'fulfilled' || saved.value.selection_matches) return
          const [id, result] = successes[index]
          result.data = { ...result.data!, performance_snapshot_id: null }
          try {
            const cachedAt = Date.now()
            writePredictionCache(id, result.data!, cachedAt, userId)
            const resolvedModelId = result.data.model_id || modelId
            if (resolvedModelId) writePredictionCache(`${id}__${resolvedModelId}`, result.data!, cachedAt, userId)
          } catch { /* Never re-enable actual recording from an unmatched cached selection. */ }
        })
        setResults({ ...completedResults })
        setPerformanceError(failed > 0)
        setPerformanceNotice(failed > 0 ? `成績の記録に失敗: ${failed}レース。予測を再実行してください。`
          : `AI参考として記録しました${alreadyRecorded ? `（記録済み ${alreadyRecorded}レースは最初の買い目を保持）` : ''}。`)
      }
      setPredictProgress(prev => ({ ...prev, done: ids.length, active: 0, current: '' }))
      if (firstOk) setExpandedRace(firstOk)
    } catch (error) {
      if (current()) {
        const message = error instanceof Error ? error.message : '一括予測に失敗しました。'
        setPredictionRuntimeNotice(message)
        setPredictProgress(prev => ({ ...prev, active: 0, current: '予測を開始・完了できませんでした' }))
        showToast(message, 'error')
        if (Object.keys(completedResults).length === 0) {
          setResults(Object.fromEntries(Object.entries(savedBeforeRefresh).map(([id, result]) => [id,
            result.success && result.data ? { ...result, data: savedPredictionForDisplay(result.data, 'unverified'), cacheStatus: 'unverified' } : result])))
        }
      }
    } finally {
      if (current()) {
        setPredictElapsedMs(Date.now() - predictionStartedAt.current)
        setPredicting(false)
        predictionRunning.current = false
        predictionController.current = null
      }
    }
  }

  const presentVenueCodes = new Set(races.map(r => r.venue_code))
  const activeReferenceModel = models.find(model => model.is_active
    && model.feature_pipeline === 'local-win-nll-reference-v1')
  const selectedReferenceModel = modelId
    ? models.find(model => model.model_id === modelId && model.feature_pipeline === 'local-win-nll-reference-v1')
    : activeReferenceModel

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
              <label htmlFor="prediction-date" className="text-xs text-[#666] block mb-2">日付</label>
              <input
                id="prediction-date"
                type="date"
                value={toInputDate(date)}
                onChange={e => changeDate(fromInputDate(e.target.value))}
                disabled={scrape.status === 'scraping' || predicting}
                className="w-full px-4 py-3 bg-[#0a0a0a] border border-[#1e1e1e] rounded-lg text-white focus:outline-none focus:border-[#333] transition-colors"
              />
            </div>
            <div className="flex-1">
              <label htmlFor="prediction-model" className="text-xs text-[#666] block mb-2">使用モデル</label>
              <select
                id="prediction-model"
                value={modelId}
                onChange={e => { setModelId(e.target.value); invalidateBettingResults() }}
                disabled={predicting}
                className="w-full px-4 py-3 bg-[#0a0a0a] border border-[#1e1e1e] rounded-lg text-white focus:outline-none focus:border-[#333] transition-colors"
              >
                <option value="">{activeReferenceModel ? `既定：${formatModelOptionLabel(activeReferenceModel)}` : '既定モデル（使用中）'}</option>
                {models.map(m => (
                  <option key={m.model_id} value={m.model_id}>{formatModelOptionLabel(m)}</option>
                ))}
              </select>
            </div>
          </div>

          {selectedReferenceModel && (
            <p className="rounded border border-[#665120] bg-[#211b0d] px-3 py-2 text-xs leading-5 text-[#fde68a]" data-testid="selected-model-reference-notice">
              参考予測として使用します。発走前オッズ・未知期間は未検証のため、購入判断には使用しません。
            </p>
          )}

          {/* 賭け設定（折りたたみ） */}
          <div className="border border-[#1e1e1e] rounded-lg overflow-hidden">
            <button
              onClick={() => setShowBettingSettings(v => !v)}
              className="w-full flex items-center justify-between px-4 py-3 bg-[#0d0d0d] hover:bg-[#161616] transition-colors"
            >
              <span className="text-xs text-[#555]">
                賭け設定 — 総資金 ¥{bankroll.toLocaleString()} · {RISK_MODES[riskMode].label}
              </span>
              <svg className={`w-3 h-3 text-[#444] transition-transform ${showBettingSettings ? 'rotate-180' : ''}`} fill="none" viewBox="0 0 24 24" stroke="currentColor">
                <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M19 9l-7 7-7-7" />
              </svg>
            </button>
            {showBettingSettings && (
              <div className="px-4 pb-4 pt-3 border-t border-[#1e1e1e] space-y-4">
                <div className="grid grid-cols-1 sm:grid-cols-2 gap-4">
                  <div>
                    <label htmlFor="betting-bankroll" className="text-xs text-[#666] block mb-2">総資金</label>
                    <div className="relative">
                      <span className="absolute left-4 top-1/2 -translate-y-1/2 text-[#555] text-sm">¥</span>
                      <input
                        id="betting-bankroll"
                        type="number"
                        min={1000}
                        step={1000}
                        value={bankroll}
                        onChange={e => {
                          const value = Math.max(1000, Number(e.target.value))
                          setBankroll(value)
                          setDayBudget(roundBetBudget(value * 0.1))
                          invalidateBettingResults()
                        }}
                        disabled={predicting}
                        className="w-full pl-8 pr-4 py-3 bg-[#0a0a0a] border border-[#1e1e1e] rounded-lg text-white focus:outline-none focus:border-[#333] transition-colors"
                      />
                    </div>
                  </div>
                  <div>
                    <label className="text-xs text-[#666] block mb-2">リスクモード</label>
                    <div className="grid grid-cols-3 gap-2 h-[50px]">
                      {([
                        ['conservative', '控えめ', '2%'],
                        ['balanced',     '標準',  '3.5%'],
                        ['aggressive',   '積極',   '5%'],
                      ] as const).map(([mode, name, pct]) => (
                        <button
                          key={mode}
                          onClick={() => { setRiskMode(mode); invalidateBettingResults() }}
                          disabled={predicting}
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
                  1レース上限: ¥{perRaceCeiling(bankroll, riskMode).toLocaleString()}
                </p>
                <div>
                  <label htmlFor="betting-day-budget" className="text-xs text-[#666] block mb-2">この日の予算</label>
                  <input id="betting-day-budget" type="number" min={0} max={bankroll} step={100} value={dayBudget}
                    onChange={e => { setDayBudget(Math.min(bankroll, roundBetBudget(Number(e.target.value)))); invalidateBettingResults() }}
                    disabled={predicting}
                    className="w-full px-4 py-3 bg-[#0a0a0a] border border-[#1e1e1e] rounded-lg text-white" />
                </div>
              </div>
            )}
          </div>

          {bettingNeedsRefresh && <p role="status" className="text-xs text-[#aaa]">買い目は再判定が必要です。予測を再実行してください。</p>}

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
            onClick={() => { void loadRaces() }}
            disabled={authLoading === true || racesLoading || scrape.status === 'scraping' || predicting}
            className="px-6 py-2.5 bg-[#1e1e1e] text-white text-sm rounded-lg hover:bg-[#2a2a2a] disabled:opacity-40 disabled:cursor-not-allowed transition-colors"
          >
            {racesLoading ? 'レース一覧取得中...' : 'レース一覧を取得'}
          </button>
        </div>

        {/* ── Layer 2: レース一覧 ── */}
        {/* スクレイプ進捗 */}
        {scrape.status === 'scraping' && (
          <div role="status" data-testid="scrape-live-progress" className="bg-[#0a1a2a] border border-[#1a3a5a] rounded-lg p-4 space-y-3">
            <div className="flex items-center gap-3">
              <div className="w-4 h-4 border-2 border-[#60a5fa] border-t-transparent rounded-full animate-spin shrink-0" />
              <div>
                <p className="text-sm font-medium text-[#93c5fd]">{scrapeDisplay?.title || '取得開始中'}</p>
                <p className="text-xs text-[#6f94b8]">{scrape.message || 'サーバーへ接続中'}</p>
              </div>
            </div>
            {scrapeDisplay && (
              <div className="grid grid-cols-2 sm:grid-cols-3 lg:grid-cols-6 gap-2 text-xs">
                <div className="bg-[#081522] rounded-md px-3 py-2"><span className="text-[#58748f] block">間隔</span><span className="text-white">{scrapeDisplay.interval}</span></div>
                <div className="bg-[#081522] rounded-md px-3 py-2"><span className="text-[#58748f] block">速度</span><span className="text-white">{scrapeDisplay.requestSpeed}</span></div>
                <div className="bg-[#081522] rounded-md px-3 py-2"><span className="text-[#58748f] block">HTTP</span><span className="text-white">{scrapeDisplay.httpStatus}</span></div>
                <div className="bg-[#081522] rounded-md px-3 py-2"><span className="text-[#58748f] block">保存</span><span className="text-white">{scrapeDisplay.saved}</span></div>
                {scrapeDisplay.raceProgress && <div className="bg-[#081522] rounded-md px-3 py-2"><span className="text-[#58748f] block">進捗</span><span className="text-white">{scrapeDisplay.raceProgress}</span></div>}
                {scrapeDisplay.eta && <div className="bg-[#081522] rounded-md px-3 py-2"><span className="text-[#58748f] block">目安</span><span className="text-white">{scrapeDisplay.eta}</span></div>}
              </div>
            )}
          </div>
        )}
        {scrape.status === 'error' && (
          <div className="bg-[#1a0a0a] border border-[#3a1a1a] rounded-lg p-4 text-sm text-[#f87171]">
            スクレイプエラー: {scrape.message}
          </div>
        )}
        {scrapeOutcome && (
          <div role="status" data-testid="scrape-completion-outcome" className={`border rounded-lg p-4 text-sm space-y-3 ${scrapeOutcome.kind === 'warning' ? 'bg-[#231c09] border-[#5a4515] text-[#fcd77d]' : 'bg-[#052e10] border-[#0a5a20] text-[#4ade80]'}`}>
            <p>{scrapeOutcome.message}</p>
            {scrapeOutcome.kind === 'warning' && (
              <>
                {races.length > 0 && <p className="text-xs">取得済みのレースは下の一覧から利用できます。</p>}
                <button onClick={() => triggerScrape(false)} disabled={racesLoading} className="px-4 py-2 bg-[#35290e] rounded-lg disabled:opacity-40">
                  再試行
                </button>
              </>
            )}
          </div>
        )}

        {/* データ未取得 + スクレイプ誘導 */}
        {racesMissing && scrape.status === 'idle' && !scrapeOutcome && (
          <div className="bg-[#0d141c] border border-[#1e3448] rounded-lg p-5 space-y-3">
            <p className="text-sm text-[#93c5fd]">この日のデータはまだ取得されていません。</p>
            <button
              onClick={() => triggerScrape(false)}
              className="px-5 py-2.5 bg-[#1a3a5a] text-[#60a5fa] text-sm rounded-lg hover:bg-[#1e4a6a] transition-colors border border-[#2a5a8a]"
            >
              この日付をスクレイプして取得
            </button>
          </div>
        )}

        {racesError && (
          <div className="bg-[#1a0a0a] border border-[#3a1a1a] rounded-lg p-4 text-sm text-[#f87171]">
            レース一覧を取得できませんでした: {racesError}
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
                        {results[r.race_id].success ? results[r.race_id].cacheStatus ? '保存予測' : '予測済' : 'エラー'}
                      </span>
                    )}
                  </div>
                </label>
                </div>
              ))}
            </div>

            <div className="px-5 py-4 border-t border-[#1e1e1e]">
              {/* 進捗バー */}
              {(predicting || predictProgress.total > 0) && (
                <div role="status" aria-live="polite" data-testid="prediction-live-progress" className="mb-3 space-y-1.5">
                  <div className="flex justify-between items-center text-xs text-[#888]">
                    <span>{predictProgress.current || (predicting ? `予測中 · 並列リクエスト ${predictProgress.active}件（最大${predictConcurrency}件）` : '予測完了')}</span>
                    <span className="tabular-nums">処理 {predictProgress.done}/{predictProgress.total}</span>
                  </div>
                  <p className="text-xs text-[#666] tabular-nums">{predictionTimingLabel({ completed: predictProgress.done, total: predictProgress.total, active: predictProgress.active }, predictElapsedMs)}</p>
                  {predictionRuntimeNotice && <p className="text-xs text-[#888]">{predictionRuntimeNotice}</p>}
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
                  : Object.keys(results).length > 0 ? `選択 ${selectedIds.size} レースを最新データで再予測` : `選択 ${selectedIds.size} レースを一括予測`}
              </button>
              <p className="mt-2 text-xs text-[#888]">保存済みデータを読み直して計算します。オッズ等の再取得・モデルの再学習は別操作です。</p>
            </div>
          </div>
        )}

        {/* ── Layer 3: 一括予測結果 ── */}
        {Object.keys(results).length > 0 && (
          <div className="space-y-3">
            <div className="flex flex-wrap items-center justify-between gap-2">
              <h2 className="text-sm font-semibold text-white">③ 予測結果・買い目</h2>
              <span className="text-xs text-[#888]">合計 ¥{dayTotal.toLocaleString()} / 予算 ¥{Math.min(dayBudget, bankroll).toLocaleString()}</span>
            </div>
            {performanceNotice && <p role={performanceError ? 'alert' : 'status'} className={`text-xs ${performanceError ? 'text-[#f87171]' : 'text-[#888]'}`}>{performanceNotice} <Link href="/dashboard" className="text-[#7dd3fc] hover:underline">成績確認へ</Link></p>}
            {filteredRaces
              .filter(r => results[r.race_id])
              .map(r => {
                const res = results[r.race_id]
                const isOpen = expandedRace === r.race_id
                const preds = res.data?.predictions || []
                const rec = dayRecommendations[r.race_id]
                const calibrated = rec?.probability_basis === 'calibrated' && rec.input_verified === true && predictionInputVerified(res.data?.input_diagnostics)
                const expectation = predictionExpectationDisplay(res.data, rec, bettingNow, r.race_id)

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
                        {res.success && <span className="text-xs text-[#aaa]">{bettingDecisionLabel(rec, bettingNow, res.data?.input_diagnostics)}</span>}
                      </div>
                      <div className="flex items-center gap-3">
                        {!res.success && <span className="text-xs text-[#f87171]">エラー</span>}
                        {res.success && preds.length > 0 && (
                          <span className="text-xs text-[#4ade80]">
                            ◎{preds[0]?.horse_number}番
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
                            <div className="px-5 pt-3"><PredictionFreshnessNotice result={res.data} status={res.cacheStatus ?? 'computed'} cachedAt={res.cachedAt} /></div>
                            <div className="px-5 pt-3"><PredictionInputNotice diagnostics={res.data?.input_diagnostics} /></div>
                            <div className="border-b border-[#1a1a1a]"><BettingRecommendationPanel recommendation={rec} diagnostics={res.data?.input_diagnostics} snapshotId={!predicting ? res.data?.performance_snapshot_id : null} nowMs={bettingNow} referenceBets={res.data?.reference_bets} /></div>
                            {expectation.mode === 'reference' && <p className="px-5 py-3 text-xs text-[#888]" title={REFERENCE_EXPECTATION_CAVEAT}>{REFERENCE_EXPECTATION_NOTE}</p>}
                            {/* 予測テーブル */}
                            <div className="overflow-x-auto">
                              <table className="w-full text-sm">
                                <thead>
                                  <tr className="border-b border-[#1e1e1e]">
                                    {['順位', '馬番', '馬名', '騎手', 'スコア', calibrated ? '勝率' : '参考比率', '複勝参考', '合成参考', expectation.label, 'オッズ', '人気'].map(h => (
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
                                      const ev = expectation.values.get(p.horse_number) ?? null
                                      const evColor = ev == null ? 'text-[#555]' : expectation.mode === 'reference' ? 'text-[#888]' : ev >= 1.2 ? 'text-[#4ade80]' : ev >= 1.0 ? 'text-[#facc15]' : 'text-[#888]'
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

