'use client'

import { useState, useEffect } from 'react'
import Link from 'next/link'
import { Logo } from '@/components/Logo'
import { Toast } from '@/components/Toast'
import { authFetch } from '@/lib/auth-fetch'
import { useJobPoller } from '@/hooks/useJobPoller'
import { useBatchScrape } from '@/hooks/useBatchScrape'

type ScrapeHealthStatus = 'healthy' | 'degraded' | 'unhealthy' | 'unknown'
type LocalApiStatus = 'checking' | ScrapeHealthStatus

type ScrapeDryRunSummary = {
  total_target_count: number
  unique_url_count: number
  estimated_request_count: number
  cache_hit_count: number
  cache_miss_count: number
  resume_hit_count: number
  skipped_count: number
  db_existing_skip_count: number
  db_existing_race_count: number
  db_existing_horse_count: number
  db_existing_result_count: number
  db_existing_pedigree_count: number
  new_fetch_required_count: number
  already_covered_count: number
  estimated_runtime_sec: number
}

type ScrapeDryRunResult = {
  dry_run: ScrapeDryRunSummary
  rate_limit_policy?: {
    min_interval_sec?: number
    scope?: string
    note?: string
  }
  retry_backoff_policy?: {
    max_retries?: number
    retry_statuses?: number[]
    backoff?: { type?: string; base_sec?: number; jitter_sec?: number }
    retry_after?: string
  }
  circuit_breaker_policy?: {
    failure_threshold?: number
    cooldown_sec?: number
    scope?: string
  }
}

type FetchSummaryHistoryItem = {
  job_id: string
  status: string
  created_at?: string
  updated_at?: string
  fetch_summary?: {
    mode?: string
    start_date?: string
    end_date?: string
    saved_races?: number
    saved_horses?: number
    elapsed_time_sec?: number
    dry_run?: {
      estimated_request_count?: number
      cache_hit_count?: number
      cache_miss_count?: number
      resume_hit_count?: number
      db_existing_skip_count?: number
      new_fetch_required_count?: number
      already_covered_count?: number
      estimated_runtime_sec?: number
    }
    metrics?: {
      network_requests?: number
      cache_hits?: number
      resume_hits?: number
      retry_count?: number
      status_429?: number
      status_403?: number
      status_500?: number
      status_503?: number
      timeout_count?: number
    }
  }
}

export default function DataCollectionPage() {
  // 期間指定用
  const now = new Date()
  const [startPeriod, setStartPeriod] = useState(`${now.getFullYear() - 1}-01`)
  const [endPeriod, setEndPeriod] = useState(
    `${now.getFullYear()}-${String(now.getMonth() + 1).padStart(2, '0')}`
  )
  const [forceRescrape, setForceRescrape] = useState(false)
  const [dryRunLoading, setDryRunLoading] = useState(false)
  const [dryRunStartedAt, setDryRunStartedAt] = useState<number | null>(null)
  const [dryRunElapsedSeconds, setDryRunElapsedSeconds] = useState(0)
  const [dryRunError, setDryRunError] = useState('')
  const [dryRunResultReady, setDryRunResultReady] = useState(false)
  const [dryRunResult, setDryRunResult] = useState<ScrapeDryRunResult | null>(null)
  const [dryRunExecuted, setDryRunExecuted] = useState(false)
  const [executeWarn, setExecuteWarn] = useState('')
  const [fetchHistory, setFetchHistory] = useState<FetchSummaryHistoryItem[]>([])
  const [fetchHistoryLoading, setFetchHistoryLoading] = useState(false)
  const [toast, setToast] = useState({ visible: false, message: '', type: 'success' as 'success' | 'error' })
  const showToast = (message: string, type: 'success' | 'error' = 'success') =>
    setToast({ visible: true, message, type })

  // バッチスクレイピング（月単位ループ + ポーリングをフックが担当）
  const { loading: batchLoading, progress: batchProgress, result: batchResult, start: startBatchScrape } = useBatchScrape()

  // データ統計と表示
  const [dataStats, setDataStats] = useState({ totalRaces: 0, totalResults: 0, latestDate: '' })
  const [showCollectedData, setShowCollectedData] = useState(false)
  const [collectedRaces, setCollectedRaces] = useState<any[]>([])
  const [selectedRaceDetail, setSelectedRaceDetail] = useState<any>(null)

  // プロファイリング
  const [showProfiling, setShowProfiling] = useState(false)
  const [profilingJobId, setProfilingJobId] = useState<string | null>(null)
  const [useOptimized, setUseOptimized] = useState(true)

  // useJobPoller でプロファイリングのポーリングを管理
  const { status: profilingStatus, progress: profilingMessage } = useJobPoller({
    jobId: profilingJobId,
    getStatusUrl: id => `/api/profiling/status/${id}`,
    intervalMs: 5000,
  })

  const inferProgress = (msg: string): number => {
    if (msg.includes('読み込み')) return 15
    if (msg.includes('エンジニアリング')) return 35
    if (msg.includes('最適化')) return 55
    if (msg.includes('ydata-profiling') || msg.includes('生成中')) return 75
    if (msg.includes('完了')) return 100
    return 5
  }
  const profilingProgress =
    profilingStatus === 'completed' ? 100
    : profilingStatus === 'error' ? 0
    : profilingMessage ? inferProgress(profilingMessage) : 0

  // ローカルAPI稼働チェック
  const [localApiStatus, setLocalApiStatus] = useState<LocalApiStatus>('checking')
  const [localApiReason, setLocalApiReason] = useState('')

  const statusMeta: Record<LocalApiStatus, { label: string; dotClass: string; textClass: string }> = {
    checking: { label: '確認中', dotClass: 'bg-[#555] animate-pulse', textClass: 'text-[#555]' },
    healthy: { label: '稼働中', dotClass: 'bg-[#4ade80]', textClass: 'text-[#4ade80]' },
    degraded: { label: '不安定', dotClass: 'bg-[#facc15]', textClass: 'text-[#facc15]' },
    unhealthy: { label: '停止中', dotClass: 'bg-[#f87171]', textClass: 'text-[#f87171]' },
    unknown: { label: '確認不可', dotClass: 'bg-[#9ca3af]', textClass: 'text-[#9ca3af]' },
  }

  const isApiUnavailable = localApiStatus === 'unhealthy' || localApiStatus === 'unknown'
  const dryRunUiLocked = dryRunLoading || batchLoading

  useEffect(() => {
    if (!dryRunLoading || dryRunStartedAt == null) return
    const timer = setInterval(() => {
      setDryRunElapsedSeconds(Math.floor((Date.now() - dryRunStartedAt) / 1000))
    }, 1000)
    return () => clearInterval(timer)
  }, [dryRunLoading, dryRunStartedAt])

  const checkLocalApi = async () => {
    setLocalApiStatus('checking')
    setLocalApiReason('')
    try {
      const res = await authFetch('/api/scrape/health', { signal: AbortSignal.timeout(4000) })
      const data = await res.json().catch(() => ({}))
      const status = String(data?.status || '') as ScrapeHealthStatus
      const resolved: ScrapeHealthStatus = ['healthy', 'degraded', 'unhealthy', 'unknown'].includes(status)
        ? status
        : res.ok
          ? 'unknown'
          : 'unhealthy'
      setLocalApiStatus(resolved)
      setLocalApiReason(typeof data?.reason === 'string' ? data.reason : '')
    } catch {
      setLocalApiStatus('unknown')
      setLocalApiReason('health check request failed')
    }
  }

  useEffect(() => {
    loadStats()
    checkLocalApi()
    loadFetchSummaryHistory()
  }, [])

  const loadFetchSummaryHistory = async () => {
    setFetchHistoryLoading(true)
    try {
      const res = await authFetch('/api/scrape/history?limit=10')
      if (!res.ok) return
      const data = await res.json().catch(() => ({}))
      const jobs = Array.isArray(data?.jobs) ? data.jobs : []
      setFetchHistory(jobs.filter((j: any) => !!j?.fetch_summary))
    } catch (error) {
      console.error('fetch history load error:', error)
    } finally {
      setFetchHistoryLoading(false)
    }
  }

  const loadStats = async () => {
    try {
      const res = await authFetch('/api/data-stats?ultimate=true')
      if (!res.ok) return
      const stats = await res.json()
      setDataStats({
        totalRaces: stats.total_races || 0,
        totalResults: stats.total_horses || 0,
        latestDate: stats.latest_date || ''
      })
    } catch (error) {
      console.error('統計取得エラー:', error)
    }
  }

  const fetchCollectedData = async () => {
    try {
      const res = await authFetch('/api/races/recent?limit=50')
      if (!res.ok) return
      const data = await res.json()
      setCollectedRaces(data.races || [])
    } catch (error) {
      console.error('データ取得エラー:', error)
    }
  }

  const fetchRaceDetail = async (raceId: string) => {
    try {
      const res = await authFetch(`/api/races/${raceId}/horses`)
      if (!res.ok) {
        const err = await res.json().catch(() => ({}))
        throw new Error(err.error || `HTTP ${res.status}`)
      }
      const data = await res.json()
      setSelectedRaceDetail({ raceId, results: data.horses || [] })
    } catch (error) {
      console.error('レース詳細取得エラー:', error)
    }
  }

  // 🚀 期間指定バッチスクレイピング（バリデーション・確認ダイアログのみ担当）
  const handlePeriodBatchScrape = async () => {
    const [startYearStr, startMonthStr] = startPeriod.split('-')
    const [endYearStr, endMonthStr] = endPeriod.split('-')
    const startYear = parseInt(startYearStr, 10)
    const startMonth = parseInt(startMonthStr, 10)
    const endYear = parseInt(endYearStr, 10)
    const endMonth = parseInt(endMonthStr, 10)

    if (new Date(startYear, startMonth - 1) > new Date(endYear, endMonth - 1)) {
      alert('開始年月が終了年月より後になっています')
      return
    }

    // 月数カウント（確認ダイアログ用）
    let totalMonths = 0
    let y = startYear, m = startMonth
    while (y < endYear || (y === endYear && m <= endMonth)) {
      totalMonths++; m++; if (m > 12) { m = 1; y++ }
    }

    if (!dryRunExecuted) {
      setExecuteWarn('Dry-run未実行です。本実行は可能ですが、推定アクセス数の確認を推奨します。')
    } else {
      setExecuteWarn('')
    }

    const _dryRunState = dryRunExecuted ? 'Dry-run実行済み' : 'Dry-run未実行（推奨）'
    if (!confirm(`${startYear}年${startMonth}月 ～ ${endYear}年${endMonth}月（${totalMonths}ヶ月分）を月単位で順次取得します。\n中断するにはページをリロードしてください。\n\n${_dryRunState}\n続行しますか？`)) return

    try {
      const result = await startBatchScrape(startPeriod, endPeriod, forceRescrape)
      showToast(`取得完了 — ${result.stats.total_months}ヶ月 / ${result.races_collected}レース / 所要: ${result.elapsed_time}秒`)
      loadStats()
      loadFetchSummaryHistory()
    } catch (error: any) {
      showToast(`取得エラー: ${error.message}`, 'error')
    }
  }

  const periodToDateRange = (startPeriodValue: string, endPeriodValue: string) => {
    const [startYearStr, startMonthStr] = startPeriodValue.split('-')
    const [endYearStr, endMonthStr] = endPeriodValue.split('-')
    const sy = parseInt(startYearStr, 10)
    const sm = parseInt(startMonthStr, 10)
    const ey = parseInt(endYearStr, 10)
    const em = parseInt(endMonthStr, 10)
    const pad = (n: number) => String(n).padStart(2, '0')
    const startDateStr = `${sy}${pad(sm)}01`
    const endLastDay = new Date(ey, em, 0).getDate()
    const endDateStr = `${ey}${pad(em)}${pad(endLastDay)}`
    return { startDateStr, endDateStr }
  }

  const periodMonthSpan = (startPeriodValue: string, endPeriodValue: string) => {
    const [startYearStr, startMonthStr] = startPeriodValue.split('-')
    const [endYearStr, endMonthStr] = endPeriodValue.split('-')
    const sy = parseInt(startYearStr, 10)
    const sm = parseInt(startMonthStr, 10)
    const ey = parseInt(endYearStr, 10)
    const em = parseInt(endMonthStr, 10)
    if (!Number.isFinite(sy) || !Number.isFinite(sm) || !Number.isFinite(ey) || !Number.isFinite(em)) return 0
    return Math.max(0, (ey - sy) * 12 + (em - sm) + 1)
  }

  const formatMaybeNumber = (value: unknown): string => {
    if (value == null) return '-'
    if (typeof value === 'number') {
      return Number.isFinite(value) ? String(value) : '-'
    }
    const parsed = Number(value)
    return Number.isFinite(parsed) ? String(parsed) : '-'
  }

  const formatMaybeSeconds = (value: unknown): string => {
    if (value == null) return '-'
    const parsed = Number(value)
    if (!Number.isFinite(parsed)) return '-'
    return `${Math.ceil(parsed)} sec`
  }

  const handleDryRun = async () => {
    const dryRunTimeoutMessage = 'Dry-run結果を取得できませんでした。期間を短くするか、再実行してください。'

    setDryRunLoading(true)
    setDryRunStartedAt(Date.now())
    setDryRunElapsedSeconds(0)
    setDryRunError('')
    setDryRunResultReady(false)
    setDryRunResult(null)
    setExecuteWarn('')
    try {
      const { startDateStr, endDateStr } = periodToDateRange(startPeriod, endPeriod)
      const startRes = await authFetch('/api/scrape', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          start_date: startDateStr,
          end_date: endDateStr,
          force_rescrape: forceRescrape,
          dry_run: true,
        }),
      })

      if (!startRes.ok) {
        const err = await startRes.json().catch(() => ({}))
        throw new Error(err.detail || `HTTP ${startRes.status}`)
      }

      const { job_id } = await startRes.json()
      let resultPayload: any = null
      // Dry-run can take longer for wide month ranges because backend preloads calendar days.
      // Keep polling until completion (up to ~90s) instead of falling back to zeroed defaults.
      const maxPollCount = 90
      for (let i = 0; i < maxPollCount; i++) {
        await new Promise(resolve => setTimeout(resolve, 1000))
        const statusRes = await authFetch(`/api/scrape/status/${job_id}`)
        if (!statusRes.ok) continue
        const statusData = await statusRes.json().catch(() => ({}))
        if (statusData?.status === 'completed') {
          resultPayload = statusData?.result
          break
        }
        if (statusData?.status === 'error') {
          throw new Error(statusData?.error || 'Dry-run failed')
        }
      }

      if (!resultPayload) {
        throw new Error(dryRunTimeoutMessage)
      }

      const fetchSummary = resultPayload?.fetch_summary || {}
      const dryRun = fetchSummary?.dry_run || {}
      const normalized: ScrapeDryRunResult = {
        dry_run: {
          total_target_count: Number(dryRun.total_target_count || 0),
          unique_url_count: Number(dryRun.unique_url_count || 0),
          estimated_request_count: Number(dryRun.estimated_request_count || 0),
          cache_hit_count: Number(dryRun.cache_hit_count || 0),
          cache_miss_count: Number(dryRun.cache_miss_count || 0),
          resume_hit_count: Number(dryRun.resume_hit_count || 0),
          skipped_count: Number(dryRun.skipped_count || 0),
          db_existing_skip_count: Number(dryRun.db_existing_skip_count || 0),
          db_existing_race_count: Number(dryRun.db_existing_race_count || 0),
          db_existing_horse_count: Number(dryRun.db_existing_horse_count || 0),
          db_existing_result_count: Number(dryRun.db_existing_result_count || 0),
          db_existing_pedigree_count: Number(dryRun.db_existing_pedigree_count || 0),
          new_fetch_required_count: Number(dryRun.new_fetch_required_count || 0),
          already_covered_count: Number(dryRun.already_covered_count || 0),
          estimated_runtime_sec: Number(dryRun.estimated_runtime_sec || 0),
        },
        rate_limit_policy: fetchSummary?.rate_limit_policy || {},
        retry_backoff_policy: fetchSummary?.retry_backoff_policy || {},
        circuit_breaker_policy: fetchSummary?.circuit_breaker_policy || {},
      }
      setDryRunResult(normalized)
      setDryRunResultReady(true)
      setDryRunExecuted(true)
      showToast('Dry-run完了（HTTPアクセスなし）')
      loadFetchSummaryHistory()
    } catch (error: any) {
      setDryRunResult(null)
      setDryRunResultReady(false)
      setDryRunExecuted(false)
      const message = typeof error?.message === 'string' ? error.message : dryRunTimeoutMessage
      setDryRunError(message)
      showToast(`Dry-runエラー: ${error.message}`, 'error')
    } finally {
      setDryRunLoading(false)
      setDryRunStartedAt(null)
    }
  }

  const handleStartProfiling = async () => {
    setProfilingJobId(null)
    try {
      const res = await authFetch('/api/profiling', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ use_optimized: useOptimized }),
      })
      if (!res.ok) throw new Error(`HTTP ${res.status}`)
      const { job_id } = await res.json()
      setProfilingJobId(job_id)
    } catch (e: any) {
      showToast(e.message, 'error')
    }
  }

  return (
    <div className="min-h-screen bg-[#0a0a0a] text-white">
      <header className="border-b border-[#1e1e1e] px-6 py-4 flex items-center justify-between">
        <Logo href="/home" />
        <div className="flex items-center gap-4">
          <Link href="/data-collection/refresh-plan" className="text-xs text-[#666] hover:text-white transition-colors">
            Refresh Plan
          </Link>
          <Link href="/data-collection/p0-repair-plan" className="text-xs text-[#666] hover:text-white transition-colors">
            P0 Repair Plan
          </Link>
          <Link href="/home" className="flex items-center gap-1 text-xs text-[#555] hover:text-white transition-colors">
            <svg className="w-3 h-3" fill="none" viewBox="0 0 24 24" stroke="currentColor">
              <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M15 19l-7-7 7-7" />
            </svg>
            ホーム
          </Link>
          {/* コンパクトAPI状態 */}
          <div className="flex items-center gap-2 px-3 py-1.5 bg-[#111] border border-[#1e1e1e] rounded-full">
            <span className={`w-1.5 h-1.5 rounded-full ${statusMeta[localApiStatus].dotClass}`} />
            <span className={`text-xs font-medium ${statusMeta[localApiStatus].textClass}`}>
              ローカルAPI {statusMeta[localApiStatus].label}
            </span>
            <button onClick={checkLocalApi} className="text-[#444] hover:text-[#888] transition-colors ml-1">
              <svg className="w-3 h-3" fill="none" viewBox="0 0 24 24" stroke="currentColor">
                <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M4 4v5h.582m15.356 2A8.001 8.001 0 004.582 9m0 0H9m11 11v-5h-.581m0 0a8.003 8.003 0 01-15.357-2m15.357 2H15" />
              </svg>
            </button>
          </div>
          <span className="text-sm text-[#888]">データ取得</span>
        </div>
      </header>

      <main className="max-w-3xl mx-auto px-6 py-10 space-y-5">
        {/* API停止中の案内 */}
        {isApiUnavailable && (
          <div className="bg-[#111] border border-[#332200] rounded-lg px-4 py-3 flex items-center gap-3">
            <span className="w-1.5 h-1.5 rounded-full bg-[#f87171] shrink-0" />
            <div className="flex-1 min-w-0">
              <p className="text-xs text-[#f87171]">スクレイプ API の状態を確認できません</p>
              <p className="text-xs text-[#555] mt-0.5">VS Code タスク「Start FastAPI」を実行するか、<code className="text-[#7dd3fc] font-mono">cd python-api; python main.py</code> を実行してください</p>
              {localApiReason && <p className="text-xs text-[#666] mt-1">reason: {localApiReason}</p>}
            </div>
          </div>
        )}

        {/* データ取得フォーム */}
        <div className="bg-[#111] border border-[#1e1e1e] rounded-lg p-6 space-y-4">
          <div className="flex items-center justify-between">
            <h2 className="text-sm font-medium text-white">期間指定一括取得</h2>
            <p className="text-xs text-[#555]">月単位で自動分割して順次取得</p>
          </div>

          <div className="grid grid-cols-2 gap-4">
            <div>
              <label className="block text-xs text-[#666] mb-2">開始年月</label>
              <input
                type="month"
                value={startPeriod}
                onChange={e => setStartPeriod(e.target.value)}
                max={endPeriod}
                disabled={dryRunUiLocked}
                className="w-full px-4 py-3 bg-[#0a0a0a] border border-[#1e1e1e] rounded-lg text-white focus:outline-none focus:border-[#333] transition-colors"
              />
            </div>
            <div>
              <label className="block text-xs text-[#666] mb-2">終了年月</label>
              <input
                type="month"
                value={endPeriod}
                onChange={e => setEndPeriod(e.target.value)}
                min={startPeriod}
                disabled={dryRunUiLocked}
                className="w-full px-4 py-3 bg-[#0a0a0a] border border-[#1e1e1e] rounded-lg text-white focus:outline-none focus:border-[#333] transition-colors"
              />
            </div>
          </div>

          {/* 期間情報 */}
          {(() => {
            const [sy, sm] = startPeriod.split('-').map(Number)
            const [ey, em] = endPeriod.split('-').map(Number)
            if (!sy || !ey) return null
            const months = (ey - sy) * 12 + (em - sm) + 1
            if (months <= 2) return null
            return null
          })()}

          <div className="rounded border border-[#1e1e1e] bg-[#0b0f14] px-3 py-2.5 text-xs text-[#9db4cc]">
            Dry-run は HTTPアクセスを実行しません。アクセス予定数とポリシーを事前確認するためのプレビューです。
          </div>

          <div className="flex items-center justify-between pt-1 gap-3">
            <label className="flex items-center gap-2 cursor-pointer select-none">
              <input
                type="checkbox"
                checked={forceRescrape}
                onChange={e => setForceRescrape(e.target.checked)}
                disabled={dryRunUiLocked}
                className="w-3.5 h-3.5 accent-white"
              />
              <span className="text-xs text-[#888]">強制再取得（取得済みを上書き）</span>
            </label>

            <div className="flex items-center gap-2">
              <button
                onClick={handleDryRun}
                disabled={batchLoading || dryRunLoading || isApiUnavailable}
                className={`flex items-center gap-2 px-4 py-2.5 rounded-lg font-medium text-sm transition-colors ${
                  batchLoading || dryRunLoading || isApiUnavailable
                    ? 'bg-[#222] text-[#555] cursor-not-allowed'
                    : 'bg-[#1e293b] text-[#dbeafe] hover:bg-[#334155]'
                }`}
              >
                {dryRunLoading ? 'Dry-run中...' : 'Dry-run'}
              </button>

              <button
                onClick={handlePeriodBatchScrape}
                disabled={batchLoading || dryRunLoading || isApiUnavailable}
                className={`flex items-center gap-2 px-6 py-2.5 rounded-lg font-medium text-sm transition-colors ${
                  batchLoading || dryRunLoading || isApiUnavailable
                    ? 'bg-[#222] text-[#555] cursor-not-allowed'
                    : 'bg-white text-black hover:bg-[#eee]'
                }`}
              >
                {batchLoading ? (
                  <>
                    <svg className="animate-spin h-3.5 w-3.5" viewBox="0 0 24 24">
                      <circle className="opacity-25" cx="12" cy="12" r="10" stroke="currentColor" strokeWidth="4" fill="none" />
                      <path className="opacity-75" fill="currentColor" d="M4 12a8 8 0 018-8V0C5.373 0 0 5.373 0 12h4z" />
                    </svg>
                    取得中...
                  </>
                ) : isApiUnavailable ? 'API確認不可' : '取得開始'}
              </button>
            </div>
          </div>

          {executeWarn && (
            <div className="rounded border border-[#4a3b0f] bg-[#201a08] px-3 py-2 text-xs text-[#facc15]">
              {executeWarn}
            </div>
          )}

          {dryRunLoading && (
            <div className="rounded-lg border border-[#1e1e1e] bg-[#0a0a0a] p-4 space-y-2">
              <div className="flex items-center justify-between">
                <h3 className="text-xs font-medium text-[#9db4cc]">Dry-run 実行中</h3>
                <span className="text-[11px] text-[#6b7280]">経過秒: {dryRunElapsedSeconds} sec</span>
              </div>
              <div className="flex items-center justify-between">
                <h3 className="text-xs font-medium text-white">見積もり生成中</h3>
              </div>
              <div className="text-xs text-[#9db4cc]">
                HTTPアクセスは実行していません
              </div>
              {periodMonthSpan(startPeriod, endPeriod) >= 6 && (
                <div className="text-xs text-[#facc15]">
                  長期間の場合、月次カレンダー確認により数十秒かかる場合があります
                </div>
              )}
            </div>
          )}

          {!dryRunLoading && dryRunError && (
            <div className="rounded border border-[#5b1e1e] bg-[#1f0d0d] px-3 py-2 text-xs text-[#fca5a5]">
              {dryRunError}
            </div>
          )}

          {!dryRunLoading && dryRunResultReady && dryRunResult && (
            <div className="rounded-lg border border-[#1e1e1e] bg-[#0a0a0a] p-4 space-y-3">
              <div className="flex items-center justify-between">
                <h3 className="text-xs font-medium text-white">Dry-run 結果（実取得なし）</h3>
                <span className="text-[11px] text-[#6b7280]">HTTPアクセスしないプレビュー</span>
              </div>

              <div className="grid grid-cols-1 md:grid-cols-2 gap-3 text-[11px]">
                <div className="rounded border border-[#1e1e1e] bg-[#0b0f14] p-3 space-y-2">
                  <div className="text-[#7dd3fc]">取得対象</div>
                  <div className="text-[#aaa]">total target count: <span className="text-white">{dryRunResult.dry_run.total_target_count}</span></div>
                  <div className="text-[#aaa]">unique URL count: <span className="text-white">{dryRunResult.dry_run.unique_url_count}</span></div>
                </div>

                <div className="rounded border border-[#1e1e1e] bg-[#0b0f14] p-3 space-y-2">
                  <div className="text-[#7dd3fc]">新規取得が必要</div>
                  <div className="text-[#aaa]">new fetch required count: <span className="text-white">{dryRunResult.dry_run.new_fetch_required_count}</span></div>
                  <div className="text-[#aaa]">estimated request count: <span className="text-white">{dryRunResult.dry_run.estimated_request_count}</span></div>
                  <div className="text-[#aaa]">cache miss count: <span className="text-white">{dryRunResult.dry_run.cache_miss_count}</span></div>
                </div>

                <div className="rounded border border-[#1e1e1e] bg-[#0b0f14] p-3 space-y-2">
                  <div className="text-[#7dd3fc]">既存DBでカバー済み</div>
                  <div className="text-[#aaa]">already covered count: <span className="text-white">{dryRunResult.dry_run.already_covered_count}</span></div>
                  <div className="text-[#aaa]">DB existing skip count: <span className="text-white">{dryRunResult.dry_run.db_existing_skip_count}</span></div>
                  <div className="text-[#aaa]">DB existing race count: <span className="text-white">{dryRunResult.dry_run.db_existing_race_count}</span></div>
                  <div className="text-[#aaa]">DB existing horse count: <span className="text-white">{dryRunResult.dry_run.db_existing_horse_count}</span></div>
                  <div className="text-[#aaa]">DB existing result count: <span className="text-white">{dryRunResult.dry_run.db_existing_result_count}</span></div>
                  <div className="text-[#aaa]">DB existing pedigree count: <span className="text-white">{dryRunResult.dry_run.db_existing_pedigree_count}</span></div>
                </div>

                <div className="rounded border border-[#1e1e1e] bg-[#0b0f14] p-3 space-y-2">
                  <div className="text-[#7dd3fc]">HTTPキャッシュ / resume でスキップ</div>
                  <div className="text-[#aaa]">cache hit count: <span className="text-white">{dryRunResult.dry_run.cache_hit_count}</span></div>
                  <div className="text-[#aaa]">resume hit count: <span className="text-white">{dryRunResult.dry_run.resume_hit_count}</span></div>
                  <div className="text-[#aaa]">skipped count: <span className="text-white">{dryRunResult.dry_run.skipped_count}</span></div>
                </div>

                <div className="rounded border border-[#1e1e1e] bg-[#0b0f14] p-3 space-y-2 md:col-span-2">
                  <div className="text-[#7dd3fc]">推定</div>
                  <div className="text-[#aaa]">推定実行時間: <span className="text-white">{Math.ceil(dryRunResult.dry_run.estimated_runtime_sec)} sec</span></div>
                </div>
              </div>

              <div className="rounded border border-[#1e1e1e] bg-[#0b0f14] px-3 py-2 text-xs text-[#9db4cc] space-y-1">
                <div>DB existing skip count は、既にDBに保存済みのため再取得不要と判定された件数です。</div>
                <div>cache hit はHTTPキャッシュで再取得不要と判定された件数です。</div>
                <div>resume hit は過去に成功済みのURLとして再実行をスキップできる件数です。</div>
                <div>new fetch required は今回新たに取得が必要と推定される件数です。</div>
              </div>

              <div className="grid grid-cols-1 md:grid-cols-3 gap-2 text-[11px]">
                <div className="rounded border border-[#1e1e1e] p-2">
                  <div className="text-[#7dd3fc] mb-1">rate limit policy</div>
                  <div className="text-[#aaa]">min_interval_sec: {dryRunResult.rate_limit_policy?.min_interval_sec ?? '-'}</div>
                  <div className="text-[#aaa]">scope: {dryRunResult.rate_limit_policy?.scope || '-'}</div>
                </div>
                <div className="rounded border border-[#1e1e1e] p-2">
                  <div className="text-[#7dd3fc] mb-1">retry/backoff policy</div>
                  <div className="text-[#aaa]">max_retries: {dryRunResult.retry_backoff_policy?.max_retries ?? '-'}</div>
                  <div className="text-[#aaa]">backoff: {dryRunResult.retry_backoff_policy?.backoff?.type || '-'}</div>
                  <div className="text-[#aaa]">retry_after: {dryRunResult.retry_backoff_policy?.retry_after || '-'}</div>
                </div>
                <div className="rounded border border-[#1e1e1e] p-2">
                  <div className="text-[#7dd3fc] mb-1">circuit breaker policy</div>
                  <div className="text-[#aaa]">failure_threshold: {dryRunResult.circuit_breaker_policy?.failure_threshold ?? '-'}</div>
                  <div className="text-[#aaa]">cooldown_sec: {dryRunResult.circuit_breaker_policy?.cooldown_sec ?? '-'}</div>
                </div>
              </div>
            </div>
          )}

          {/* 進捗バー */}
          {batchLoading && (
            <div className="space-y-1.5">
              <div className="flex justify-between text-xs text-[#888]">
                <span>{batchProgress.message}</span>
                <span className="flex gap-3">
                  {batchProgress.eta && <span className="text-yellow-400">{batchProgress.eta}</span>}
                  <span>{batchProgress.current}%</span>
                </span>
              </div>
              <div className="w-full bg-[#1e1e1e] rounded-full h-1.5 overflow-hidden">
                <div className="bg-white h-1.5 rounded-full transition-all duration-500" style={{ width: `${batchProgress.current}%` }} />
              </div>
            </div>
          )}
        </div>

        {/* 取得完了サマリー */}
        {batchResult && batchResult.stats?.period && (
          <div className="bg-[#0a1a0a] border border-[#1a3a1a] rounded-lg px-5 py-4 flex flex-wrap gap-5 items-center">
            <span className="text-xs text-[#4ade80] font-medium">✓ 取得完了</span>
            <span className="text-xs text-[#888]">{batchResult.stats.period} · {batchResult.stats.total_months}ヶ月</span>
            <span className="text-xs text-white font-medium">{batchResult.races_collected}レース</span>
            <span className="text-xs text-[#555]">{batchResult.elapsed_time}秒</span>
          </div>
        )}

        {/* fetch summary 履歴 */}
        <div className="bg-[#111] border border-[#1e1e1e] rounded-lg p-5">
          <div className="flex items-center justify-between mb-3">
            <h2 className="text-sm font-medium text-white">fetch summary 履歴</h2>
            <button
              onClick={loadFetchSummaryHistory}
              className="text-xs text-[#555] hover:text-[#888] transition-colors"
            >
              {fetchHistoryLoading ? '更新中...' : '更新'}
            </button>
          </div>

          {fetchHistory.length === 0 ? (
            <div className="text-xs text-[#555] py-3">履歴がありません（Dry-run または 取得実行後に表示されます）</div>
          ) : (
            <div className="space-y-2">
              {fetchHistory.map((item) => {
                const summary = item.fetch_summary || {}
                const metrics = summary.metrics || {}
                const dry = summary.dry_run || {}
                const mode = summary.mode || '-'
                return (
                  <div key={item.job_id} className="rounded border border-[#1e1e1e] bg-[#0a0a0a] p-3">
                    <div className="flex flex-wrap items-center gap-2 mb-2">
                      <span className="text-[11px] px-2 py-0.5 rounded bg-[#1f2937] text-[#dbeafe]">{mode}</span>
                      <span className="text-[11px] text-[#888]">job: {item.job_id}</span>
                      <span className="text-[11px] text-[#666]">{summary.start_date || '-'} ~ {summary.end_date || '-'}</span>
                      <span className="text-[11px] text-[#666]">updated: {item.updated_at || '-'}</span>
                    </div>
                    {mode === 'dry-run' ? (
                      <div className="grid grid-cols-2 md:grid-cols-6 gap-2 text-[11px]">
                        <div className="text-[#aaa]">est req: <span className="text-white">{formatMaybeNumber(dry.estimated_request_count)}</span></div>
                        <div className="text-[#aaa]">new fetch: <span className="text-white">{formatMaybeNumber(dry.new_fetch_required_count)}</span></div>
                        <div className="text-[#aaa]">already covered: <span className="text-white">{formatMaybeNumber(dry.already_covered_count)}</span></div>
                        <div className="text-[#aaa]">cache hit: <span className="text-white">{formatMaybeNumber(dry.cache_hit_count)}</span></div>
                        <div className="text-[#aaa]">cache miss: <span className="text-white">{formatMaybeNumber(dry.cache_miss_count)}</span></div>
                        <div className="text-[#aaa]">resume hit: <span className="text-white">{formatMaybeNumber(dry.resume_hit_count)}</span></div>
                        <div className="text-[#aaa]">db existing: <span className="text-white">{formatMaybeNumber(dry.db_existing_skip_count)}</span></div>
                        <div className="text-[#aaa]">est runtime: <span className="text-white">{formatMaybeSeconds(dry.estimated_runtime_sec)}</span></div>
                      </div>
                    ) : (
                      <div className="grid grid-cols-2 md:grid-cols-5 gap-2 text-[11px]">
                        <div className="text-[#aaa]">saved races: <span className="text-white">{summary.saved_races ?? '-'}</span></div>
                        <div className="text-[#aaa]">saved horses: <span className="text-white">{summary.saved_horses ?? '-'}</span></div>
                        <div className="text-[#aaa]">elapsed: <span className="text-white">{Math.ceil(Number(summary.elapsed_time_sec || 0))} sec</span></div>
                        <div className="text-[#aaa]">network req: <span className="text-white">{metrics.network_requests ?? '-'}</span></div>
                        <div className="text-[#aaa]">retries: <span className="text-white">{metrics.retry_count ?? '-'}</span></div>
                      </div>
                    )}
                  </div>
                )
              })}
            </div>
          )}
        </div>

        {/* 取得済みデータ統計 */}
        <div className="bg-[#111] border border-[#1e1e1e] rounded-lg p-5">
          <div className="flex items-center justify-between mb-4">
            <h2 className="text-sm font-medium text-white">取得済みデータ</h2>
            <button
              onClick={() => { setShowCollectedData(v => !v); if (!showCollectedData) fetchCollectedData() }}
              className="text-xs text-[#555] hover:text-[#888] transition-colors"
            >
              {showCollectedData ? '閉じる ▲' : 'レース一覧を見る ▼'}
            </button>
          </div>

          <div className="grid grid-cols-3 gap-3">
            <div className="bg-[#0a0a0a] border border-[#1e1e1e] rounded-lg p-3">
              <div className="text-xs text-[#666] mb-1">総レース数</div>
              <div className="text-xl font-bold text-white">{dataStats.totalRaces.toLocaleString()}</div>
            </div>
            <div className="bg-[#0a0a0a] border border-[#1e1e1e] rounded-lg p-3">
              <div className="text-xs text-[#666] mb-1">総出走馬数</div>
              <div className="text-xl font-bold text-white">{dataStats.totalResults.toLocaleString()}</div>
            </div>
            <div className="bg-[#0a0a0a] border border-[#1e1e1e] rounded-lg p-3">
              <div className="text-xs text-[#666] mb-1">最終取得日</div>
              <div className="text-sm font-medium text-[#aaa]">
                {dataStats.latestDate ? new Date(dataStats.latestDate).toLocaleDateString('ja-JP') : '未取得'}
              </div>
            </div>
          </div>

          {/* レース一覧（折りたたみ） */}
          {showCollectedData && (
            <div className="mt-4 border-t border-[#1e1e1e] pt-4">
              <div className="flex justify-between items-center mb-3">
                <span className="text-xs text-[#555]">最近取得したレース（最新50件）</span>
                <button onClick={fetchCollectedData} className="text-xs text-[#555] hover:text-[#888] transition-colors">更新</button>
              </div>
              <div className="space-y-1 max-h-96 overflow-y-auto">
                {collectedRaces.length === 0 ? (
                  <div className="py-8 text-center text-xs text-[#444]">データがまだ取得されていません</div>
                ) : collectedRaces.map(race => (
                  <div key={race.race_id} className="flex items-center justify-between px-3 py-2.5 rounded bg-[#0a0a0a] hover:bg-[#161616] transition-colors group">
                    <div className="min-w-0 flex-1">
                      <div className="flex items-center gap-2 flex-wrap">
                        <span className="text-xs font-medium text-white">{race.race_name || `${race.venue} ${race.race_no}R`}</span>
                        <span className="text-xs text-[#555]">{race.venue}</span>
                        <span className="text-xs text-[#444]">{race.track_type} {race.distance}m</span>
                      </div>
                      <div className="text-[10px] text-[#333] mt-0.5">{race.date || race.created_at?.slice(0, 10) || ''}</div>
                    </div>
                    <button
                      onClick={() => fetchRaceDetail(race.race_id)}
                      className="text-xs text-[#444] hover:text-[#888] transition-colors opacity-0 group-hover:opacity-100 ml-3 shrink-0"
                    >
                      詳細
                    </button>
                  </div>
                ))}
              </div>
            </div>
          )}
        </div>

        {/* レース詳細モーダル */}
        {selectedRaceDetail && (
          <div className="fixed inset-0 bg-black/60 flex items-center justify-center z-50 p-4" onClick={() => setSelectedRaceDetail(null)}>
            <div className="bg-[#111] border border-[#1e1e1e] rounded-lg max-w-4xl w-full max-h-[85vh] overflow-hidden" onClick={e => e.stopPropagation()}>
              <div className="flex items-center justify-between px-5 py-4 border-b border-[#1e1e1e]">
                <h3 className="text-sm font-medium">レース詳細</h3>
                <button onClick={() => setSelectedRaceDetail(null)} className="text-[#555] hover:text-white text-xl leading-none">×</button>
              </div>
              <div className="overflow-auto max-h-[calc(85vh-56px)]">
                <table className="w-full text-xs">
                  <thead className="bg-[#0a0a0a] sticky top-0">
                    <tr>
                      {['着', '枠', '馬番', '馬名', '性齢', '斤量', '騎手', 'タイム', 'オッズ', '人気'].map(h => (
                        <th key={h} className="px-3 py-2.5 text-left font-medium text-[#555] first:pl-5">{h}</th>
                      ))}
                    </tr>
                  </thead>
                  <tbody>
                    {selectedRaceDetail.results.map((result: any, i: number) => (
                      <tr key={i} className={`border-b border-[#1a1a1a] hover:bg-[#161616] transition-colors ${result.finish_position === 1 ? 'bg-[#0a1500]' : ''}`}>
                        <td className={`px-3 py-2.5 pl-5 font-bold ${result.finish_position <= 3 ? 'text-[#4ade80]' : 'text-[#888]'}`}>{result.finish_position}</td>
                        <td className="px-3 py-2.5 text-[#666]">{result.bracket_number}</td>
                        <td className="px-3 py-2.5 font-bold">{result.horse_number}</td>
                        <td className="px-3 py-2.5 font-medium text-white">{result.horse_name}</td>
                        <td className="px-3 py-2.5 text-[#888]">{result.sex_age || `${result.sex || ''}${result.age || ''}`}</td>
                        <td className="px-3 py-2.5 text-[#888]">{result.jockey_weight}kg</td>
                        <td className="px-3 py-2.5 text-[#888]">{result.jockey_name}</td>
                        <td className="px-3 py-2.5 font-mono text-[#aaa]">{result.finish_time?.toFixed(1)}</td>
                        <td className="px-3 py-2.5 font-medium">{result.odds?.toFixed(1)}</td>
                        <td className="px-3 py-2.5 text-[#666]">{result.popularity}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            </div>
          </div>
        )}

        {/* プロファイリング（折りたたみ） */}
        <div className="border border-[#1e1e1e] rounded-lg overflow-hidden">
          <button
            onClick={() => setShowProfiling(v => !v)}
            className="w-full flex items-center justify-between px-5 py-3.5 bg-[#111] hover:bg-[#161616] transition-colors"
          >
            <span className="text-xs text-[#555]">特徴量プロファイリングレポート（オプション）</span>
            <svg className={`w-3 h-3 text-[#444] transition-transform ${showProfiling ? 'rotate-180' : ''}`} fill="none" viewBox="0 0 24 24" stroke="currentColor">
              <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M19 9l-7 7-7-7" />
            </svg>
          </button>
          {showProfiling && (
            <div className="px-5 pb-5 pt-4 bg-[#0d0d0d] border-t border-[#1e1e1e] space-y-3">
              <label className="flex items-center gap-2 text-xs text-[#888] cursor-pointer select-none">
                <input type="checkbox" checked={useOptimized} onChange={e => setUseOptimized(e.target.checked)} className="w-3.5 h-3.5 accent-white" />
                LightGBM最適化済み（リーク除去・変換適用）
              </label>
              <div className="flex items-center gap-3">
                <button
                  onClick={handleStartProfiling}
                  disabled={profilingStatus === 'running'}
                  className={`px-4 py-2 rounded text-xs font-medium transition-colors ${profilingStatus === 'running' ? 'bg-[#1a1a1a] text-[#555] cursor-not-allowed' : 'bg-white text-black hover:bg-[#eee]'}`}
                >
                  {profilingStatus === 'running' ? '生成中...' : 'レポート生成'}
                </button>
                {profilingStatus === 'completed' && profilingJobId && (
                  <a href={`/api/profiling/html/${profilingJobId}`} target="_blank" rel="noopener noreferrer" className="text-xs text-[#4ade80] hover:underline">レポートを開く →</a>
                )}
              </div>
              {profilingStatus === 'running' && (
                <div className="space-y-1">
                  <div className="flex justify-between text-xs text-[#555]">
                    <span>{profilingMessage}</span>
                    <span>{profilingProgress}%</span>
                  </div>
                  <div className="w-full bg-[#1e1e1e] rounded-full h-1 overflow-hidden">
                    <div className="bg-[#555] h-1 rounded-full transition-all duration-700" style={{ width: `${profilingProgress}%` }} />
                  </div>
                </div>
              )}
              {profilingStatus === 'error' && <p className="text-xs text-red-400">{profilingMessage}</p>}
            </div>
          )}
        </div>

        {/* 次のステップ */}
        <div className="p-5 bg-[#111] border border-[#1e1e1e] rounded-lg flex items-center justify-between gap-4">
          <div>
            <div className="text-xs text-[#666] mb-0.5">次のステップ — 02</div>
            <div className="text-sm font-medium">モデル学習</div>
            <div className="text-xs text-[#555] mt-0.5">収集したデータでAIモデルをトレーニング</div>
          </div>
          <Link
            href="/train"
            className="shrink-0 flex items-center gap-1.5 bg-white text-black text-sm font-medium px-5 py-2.5 rounded hover:bg-[#eee] transition-colors"
          >
            モデル学習へ
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

