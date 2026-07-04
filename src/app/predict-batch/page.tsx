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

// モデルID末尾の YYYYMMDD_HHMM を抽出（日付順ソート・表示用）
function modelCreatedAt(model_id: string): string {
  const m = model_id.match(/_(\d{8})_(\d{4})$/)
  return m ? m[1] + m[2] : model_id
}
function modelLabel(model_id: string, target?: string, auc?: number): string {
  const m = model_id.match(/_(\d{8})_(\d{4})$/)
  const tgt = target || model_id.replace(/^model_/, '').replace(/_lightgbm.*/, '').replace(/_lgbm.*/, '')
  if (!m) return auc != null ? `${tgt} (AUC: ${auc.toFixed(3)})` : tgt
  const d = m[1], t = m[2]
  const dateStr = `${d.slice(0,4)}-${d.slice(4,6)}-${d.slice(6,8)} ${t.slice(0,2)}:${t.slice(2,4)}`
  return auc != null ? `${tgt} · ${dateStr} (AUC: ${auc.toFixed(3)})` : `${tgt} · ${dateStr}`
}

// 馬番配列 + 券種 → 組み合わせ文字列配列を生成
function genManualCombos(nos: number[], betType: string): string[] {
  if (nos.length === 0) return []
  if (betType === '単勝' || betType === '複勝') return nos.map(n => String(n))
  if (betType === '馬連' || betType === 'ワイド') {
    const r: string[] = []
    for (let i = 0; i < nos.length; i++)
      for (let j = i + 1; j < nos.length; j++)
        r.push(`${nos[i]}-${nos[j]}`)
    return r
  }
  if (betType === '馬単') {
    const r: string[] = []
    for (let i = 0; i < nos.length; i++)
      for (let j = 0; j < nos.length; j++)
        if (i !== j) r.push(`${nos[i]}-${nos[j]}`)
    return r
  }
  if (betType === '三連複') {
    const r: string[] = []
    for (let i = 0; i < nos.length; i++)
      for (let j = i + 1; j < nos.length; j++)
        for (let k = j + 1; k < nos.length; k++)
          r.push(`${nos[i]}-${nos[j]}-${nos[k]}`)
    return r
  }
  if (betType === '三連単') {
    const r: string[] = []
    for (let i = 0; i < nos.length; i++)
      for (let j = 0; j < nos.length; j++)
        for (let k = 0; k < nos.length; k++)
          if (i !== j && j !== k && i !== k)
            r.push(`${nos[i]}-${nos[j]}-${nos[k]}`)
    return r
  }
  return nos.map(n => String(n))
}

// 券種ごとに最低何頭必要か
const MIN_HORSES: Record<string, number> = {
  '単勝': 1, '複勝': 1, '馬連': 2, 'ワイド': 2, '馬単': 2, '三連複': 3, '三連単': 3,
}

export default function PredictBatchPage() {
  const [date, setDate] = useState(todayStr())
  const [activeVenueTab, setActiveVenueTab] = useState<string>('all')
  const [races, setRaces] = useState<RaceItem[]>([])
  const [racesLoading, setRacesLoading] = useState(false)
  const [racesError, setRacesError] = useState('')
  const [selectedIds, setSelectedIds] = useState<Set<string>>(new Set())
  const [models, setModels] = useState<any[]>([])
  const [modelId, setModelId] = useState<string>('')
  const [place3ModelId, setPlace3ModelId] = useState<string>('')
  const [predicting, setPredicting] = useState(false)
  const [predictProgress, setPredictProgress] = useState({ done: 0, total: 0, current: '' })
  const [results, setResults] = useState<Record<string, any>>({})
  const [purchased, setPurchased] = useState<Set<string>>(new Set())
  const [purchasing, setPurchasing] = useState<Set<string>>(new Set())
  // 買い目編集 state
  type BetEdit = { betType: string; selectedIdxs: number[]; unitPrice: number; manualNos?: number[]; manualSelCombos?: string[] | null }
  const [betEdits, setBetEdits] = useState<Record<string, BetEdit>>({})
  const [bankroll, setBankroll] = useState(10000)
  const [riskMode, setRiskMode] = useState<'conservative' | 'balanced' | 'aggressive'>('balanced')
  const [activeRaceId, setActiveRaceId] = useState<string | null>(null)
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
    maxMs: 30 * 60 * 1000,  // 当日24R×~25秒≈10分 + 余裕で30分
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
        // 末尾の作成日時（YYYYMMDD_HHMM）降順で最新モデルを先頭に
        const sorted = (data.models || []).sort((a: any, b: any) => modelCreatedAt(b.model_id).localeCompare(modelCreatedAt(a.model_id)))
        setModels(sorted)
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
    setActiveVenueTab('all')
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
        // 最初の場所タブを自動選択（SPAIA風UIで 'all' では race ボタンが曖昧になるため）
        const firstVenue = [...new Set(fetched.map((r: any) => r.venue_code as string))][0]
        if (firstVenue) setActiveVenueTab(firstVenue)
        setActiveRaceId(null)
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
    setActiveVenueTab('all')
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
        const firstVenue = [...new Set(fetched.map((r: any) => r.venue_code as string))][0]
        if (firstVenue) setActiveVenueTab(firstVenue)
        setActiveRaceId(null)
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

  // 場所タブでフィルター
  const filteredRaces = activeVenueTab === 'all'
    ? races
    : races.filter(r => r.venue_code === activeVenueTab)

  const toggleRace = (id: string) => {
    setSelectedIds(prev => {
      const n = new Set(prev)
      n.has(id) ? n.delete(id) : n.add(id)
      return n
    })
  }

  const selectAll = () => setSelectedIds(new Set(filteredRaces.map(r => r.race_id)))
  const deselectAll = () => setSelectedIds(new Set())

  // 1レース単体予測（レースボタン押下時）
  const predictSingle = (raceId: string) => handleBatchPredict([raceId])

  const handleBatchPredict = async (overrideIds?: string[]) => {
    const ids = overrideIds ?? Array.from(selectedIds)
    if (ids.length === 0) { showToast('予測するレースを選択してください', 'error'); return }
    setPredicting(true)
    setResults({})
    setPredictProgress({ done: 0, total: ids.length, current: '' })

    // [高速化] 当日・未来レースはオッズを事前一括取得（Playwright 1インスタンスで全レース）
    // analyze_race のキャッシュに乗ることで各レースの Playwright 再スクレイプを回避できる
    if (isCurrentOrFutureDate && ids.length > 1) {
      setPredictProgress(prev => ({ ...prev, current: `オッズ一括取得中... (${ids.length}R)` }))
      try {
        await authFetch('/api/realtime-odds/refresh', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          signal: AbortSignal.timeout(150_000),  // Playwright batch: 24R × ~5s + 起動 = ~2分
          body: JSON.stringify({ race_ids: ids, types: 'tansho' }),
        })
      } catch {
        // prefetch 失敗は無視（各 analyze_race が個別に取得するフォールバック）
      }
    }

    let done = 0
    let firstOk: string | undefined
    let abortedByIPBlock = false

    const CONCURRENCY = 1  // FastAPI は単一プロセス: 並列するとGIL競合でタイムアウト。逐次処理で2本目以降はHistキャッシュが効き高速化
    for (let i = 0; i < ids.length; i += CONCURRENCY) {
      if (abortedByIPBlock) break
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
              body: JSON.stringify({ race_id: raceId, model_id: modelId || null, place3_model_id: place3ModelId || null, bankroll: bankroll, risk_mode: riskMode }),
            })
            const data = await res.json()
            // HTTP 503 + IPブロックメッセージ → 即中止
            if (res.status === 503 && String(data.detail ?? '').includes('IPブロック')) {
              abortedByIPBlock = true
              setResults(prev => ({ ...prev, [raceId]: { success: false, error: data.detail } }))
              showToast(`⚠️ IPブロック検知 — 予測を中止しました`, 'error')
              return
            }
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
    if (firstOk) setActiveRaceId(firstOk)
    setPredicting(false)
  }

  const recordPurchase = async (raceId: string, venue: string, res: { success: boolean; data?: any; error?: string }) => {
    if (!res.data) return
    const rec = res.data.recommendation
    const edit = betEdits[raceId]
    const betType = edit?.betType ?? res.data.best_bet_type ?? '単勝'
    const allCombos: any[] = res.data.bet_types?.[betType] ?? []
    const selIdxs: number[] = edit?.selectedIdxs ?? Array.from(
      { length: Math.min(rec?.purchase_count ?? 1, allCombos.length) }, (_, i) => i
    )
    const aiCombos = selIdxs.map(i => allCombos[i]).filter(Boolean)
    const manualNos: number[] = edit?.manualNos ?? []
    const allManualCombos = genManualCombos(manualNos, betType)
    const manualCombos: string[] = edit?.manualSelCombos ?? allManualCombos
    const unitPrice = edit?.unitPrice ?? rec?.unit_price ?? 100
    const combinations = [
      ...aiCombos.map((c: any) => String(c.combination ?? c)),
      ...manualCombos
    ]
    const total_cost = unitPrice * Math.max(combinations.length, 1)
    const topPred = res.data.predictions?.[0]
    const ev: number = topPred?.expected_value ?? 1.0
    setPurchasing(prev => new Set(prev).add(raceId))
    try {
      const body = {
        race_id: raceId,
        venue,
        bet_type: betType,
        combinations,
        strategy_type: rec?.strategy_explanation || 'AI推奨',
        purchase_count: combinations.length,
        unit_price: unitPrice,
        total_cost,
        expected_value: ev,
        expected_return: total_cost * ev,
      }
      const r = await authFetch('/api/purchase', {
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

  // 買い目編集ヘルパー
  const changeBetType = (raceId: string, newType: string, data: any) => {
    const combos: any[] = data?.bet_types?.[newType] ?? []
    const rec = data?.recommendation
    const count = Math.min(rec?.purchase_count ?? 1, combos.length)
    setBetEdits(prev => ({
      ...prev,
      [raceId]: {
        betType: newType,
        selectedIdxs: Array.from({ length: count }, (_, i) => i),
        unitPrice: prev[raceId]?.unitPrice ?? rec?.unit_price ?? 100,
      }
    }))
  }
  const toggleCombo = (raceId: string, idx: number) => {
    setBetEdits(prev => {
      const cur = prev[raceId]
      if (!cur) return prev
      const set = new Set(cur.selectedIdxs)
      set.has(idx) ? set.delete(idx) : set.add(idx)
      return { ...prev, [raceId]: { ...cur, selectedIdxs: [...set].sort((a, b) => a - b) } }
    })
  }
  const setBetUnitPrice = (raceId: string, price: number) => {
    setBetEdits(prev => prev[raceId]
      ? { ...prev, [raceId]: { ...prev[raceId], unitPrice: price } }
      : prev
    )
  }
  const toggleManualNo = (raceId: string, no: number, ctx: { betType: string; selectedIdxs: number[]; unitPrice: number }) => {
    setBetEdits(prev => {
      const cur = prev[raceId] ?? { ...ctx, manualNos: [], manualSelCombos: null }
      const set = new Set(cur.manualNos ?? [])
      set.has(no) ? set.delete(no) : set.add(no)
      // 馬番が変わったら組み合わせ選択をリセット(null=全選択)
      return { ...prev, [raceId]: { ...cur, manualNos: [...set].sort((a, b) => a - b), manualSelCombos: null } }
    })
  }
  const toggleManualCombo = (raceId: string, combo: string, allCombos: string[], ctx: { betType: string; selectedIdxs: number[]; unitPrice: number }) => {
    setBetEdits(prev => {
      const cur = prev[raceId] ?? { ...ctx, manualNos: [], manualSelCombos: null }
      const current: string[] = cur.manualSelCombos ?? allCombos
      const set = new Set(current)
      set.has(combo) ? set.delete(combo) : set.add(combo)
      return { ...prev, [raceId]: { ...cur, manualSelCombos: [...set] } }
    })
  }

  const handleRefreshOdds = async () => {
    const successIds = filteredRaces
      .filter(r => results[r.race_id]?.success)
      .map(r => r.race_id)
    if (successIds.length === 0) { showToast('先に予測を実行してください', 'error'); return }
    setOddsRefreshing(true)
    try {
      // force_refresh=true でキャッシュをバイパスして最新オッズを取得
      // レスポンスに odds 値が含まれるため N 本 GET が不要
      const res = await authFetch('/api/realtime-odds/refresh', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        signal: AbortSignal.timeout(300_000),
        body: JSON.stringify({ race_ids: successIds, types: 'tansho', force_refresh: false }),
      })
      if (!res.ok) throw new Error(`HTTP ${res.status}`)
      const data = await res.json()
      // レスポンスの odds を直接パース（別途 GET 不要）
      const updated: Record<string, Record<string, number>> = { ...realtimeOdds }
      for (const [raceId, result] of Object.entries(data.results as Record<string, any>)) {
        if (result.success && result.odds?.tansho) {
          updated[raceId] = result.odds.tansho
        }
      }
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
      const res = await authFetch('/api/export/bet-list', {
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
      const res = await authFetch('/api/export/bet-list?format=csv', {
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
            <h2 className="text-sm font-semibold text-white">日付・モデル設定</h2>
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
              <label className="text-xs text-[#666] block mb-2">メインモデル</label>
              <select
                value={modelId}
                onChange={e => setModelId(e.target.value)}
                className="w-full px-4 py-3 bg-[#0a0a0a] border border-[#1e1e1e] rounded-lg text-white focus:outline-none focus:border-[#333] transition-colors"
              >
                <option value="">最新モデルを自動選択</option>
                {models.filter(m => m.target === 'win' || (m.target && (m.target as string).startsWith('speed'))).map((m, i) => (
                  <option key={i} value={m.model_id}>{modelLabel(m.model_id, m.target ?? undefined, m.auc)}</option>
                ))}
              </select>
            </div>
            <div className="flex-1">
              <label className="text-xs text-[#666] block mb-2">複勝圏モデル</label>
              <select
                value={place3ModelId}
                onChange={e => setPlace3ModelId(e.target.value)}
                className="w-full px-4 py-3 bg-[#0a0a0a] border border-[#1e1e1e] rounded-lg text-white focus:outline-none focus:border-[#333] transition-colors"
              >
                <option value="">最新 place3 モデルを自動選択</option>
                {models.filter(m => m.target === 'place3').map((m, i) => (
                  <option key={i} value={m.model_id}>{modelLabel(m.model_id, m.target ?? undefined, m.auc)}</option>
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
            <button
              onClick={() => triggerScrape(false)}
              className="px-5 py-2.5 bg-[#1a3a5a] text-[#60a5fa] text-sm rounded-lg hover:bg-[#1e4a6a] transition-colors border border-[#2a5a8a]"
            >
              この日付をスクレイプして取得
            </button>
          </div>
        )}

        {/* ── SPAIA風 レースナビゲーション ── */}
        {races.length > 0 && (
          <div className="bg-[#111] border border-[#1e1e1e] rounded-lg overflow-hidden">
            {/* 場所タブ（複数場の場合のみ表示） */}
            {presentVenueCodes.size > 1 && (
              <div className="flex border-b border-[#1e1e1e]">
                {JRA_VENUES.filter(v => presentVenueCodes.has(v.code)).map(v => (
                  <button
                    key={v.code}
                    onClick={() => { setActiveVenueTab(v.code); setActiveRaceId(null) }}
                    className={`flex-1 py-3 text-sm font-semibold tracking-wide transition-colors ${
                      activeVenueTab === v.code
                        ? 'bg-[#22c55e] text-black'
                        : 'bg-transparent text-[#555] hover:text-white hover:bg-[#161616]'
                    }`}
                  >
                    {v.name}
                  </button>
                ))}
              </div>
            )}

            {/* レース番号ボタングリッド */}
            <div className="p-4">
              <div className="flex flex-wrap gap-2">
                {filteredRaces.map(r => {
                  const isActive = activeRaceId === r.race_id
                  const result = results[r.race_id]
                  const isPurchased = purchased.has(r.race_id)
                  return (
                    <button
                      key={r.race_id}
                      onClick={() => setActiveRaceId(r.race_id)}
                      className={`relative flex flex-col items-center justify-center w-14 h-14 rounded-lg font-bold text-sm transition-all border ${
                        isActive
                          ? 'bg-[#22c55e] text-black border-[#22c55e] shadow-lg shadow-[#22c55e22]'
                          : result?.success
                          ? 'bg-[#052e10] text-[#4ade80] border-[#0a5a20] hover:bg-[#073d15]'
                          : result && !result.success
                          ? 'bg-[#1a0808] text-[#f87171] border-[#3a1010]'
                          : 'bg-[#161616] text-[#888] border-[#222] hover:bg-[#222] hover:text-white'
                      }`}
                    >
                      <span>{r.race_no}R</span>
                      {r.post_time && (
                        <span className={`text-[9px] font-normal mt-0.5 ${isActive ? 'text-black/60' : 'text-[#444]'}`}>
                          {r.post_time}
                        </span>
                      )}
                      {isPurchased && (
                        <span className="absolute -top-1 -right-1 w-3 h-3 bg-[#facc15] rounded-full flex items-center justify-center text-[7px] font-bold text-black border border-[#0a0a0a]">¥</span>
                      )}
                      {result?.success && !isActive && !isPurchased && (
                        <span className="absolute -top-1 -right-1 w-2.5 h-2.5 bg-[#4ade80] rounded-full border border-[#0a0a0a]" />
                      )}
                    </button>
                  )
                })}
              </div>
            </div>

            {/* アクションバー */}
            <div className="px-4 py-3 border-t border-[#1e1e1e] flex items-center justify-between flex-wrap gap-3">
              <div className="flex items-center gap-4">
                <span className="text-xs text-[#555]">
                  <span className="text-[#ccc]">{Object.values(results).filter(r => r.success).length}</span>
                  {' / '}{races.length} 予測済み
                </span>
                {isCurrentOrFutureDate && (
                  <button
                    onClick={handleRefreshOdds}
                    disabled={oddsRefreshing}
                    className="flex items-center gap-1 text-xs text-[#60a5fa] hover:text-[#93c5fd] disabled:opacity-40 transition-colors"
                  >
                    {oddsRefreshing ? (
                      <svg className="animate-spin w-3 h-3" viewBox="0 0 24 24" fill="none">
                        <circle className="opacity-25" cx="12" cy="12" r="10" stroke="currentColor" strokeWidth="4" />
                        <path className="opacity-75" fill="currentColor" d="M4 12a8 8 0 018-8V0C5.373 0 0 5.373 0 12h4z" />
                      </svg>
                    ) : (
                      <svg className="w-3 h-3" fill="none" viewBox="0 0 24 24" stroke="currentColor">
                        <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M4 4v5h.582m15.356 2A8.001 8.001 0 004.582 9m0 0H9m11 11v-5h-.581m0 0a8.003 8.003 0 01-15.357-2m15.357 2H15" />
                      </svg>
                    )}
                    {oddsRefreshing ? '更新中...' : 'オッズ更新'}
                  </button>
                )}
              </div>
              <button
                onClick={() => handleBatchPredict(filteredRaces.map(r => r.race_id))}
                disabled={predicting}
                className="px-4 py-2 bg-white text-black text-xs font-semibold rounded-lg hover:bg-[#eee] disabled:opacity-40 disabled:cursor-not-allowed transition-colors"
              >
                {predicting
                  ? `予測中... (${predictProgress.done}/${predictProgress.total})`
                  : `全${filteredRaces.length}Rを一括予測`}
              </button>
            </div>

            {/* 進捗バー（予測中のみ） */}
            {predicting && (
              <div className="px-4 pb-3 space-y-1.5 border-t border-[#0a0a0a]">
                <div className="flex justify-between items-center text-xs text-[#888] pt-2.5">
                  <span>{predictProgress.current ? `${predictProgress.current}` : '予測中...'}</span>
                  <span className="tabular-nums">{predictProgress.done}/{predictProgress.total}</span>
                </div>
                <div className="w-full h-1.5 bg-[#1e1e1e] rounded-full overflow-hidden">
                  <div
                    className="h-full bg-[#22c55e] rounded-full transition-all duration-300"
                    style={{ width: predictProgress.total > 0 ? `${(predictProgress.done / predictProgress.total) * 100}%` : '0%' }}
                  />
                </div>
              </div>
            )}
          </div>
        )}

        {/* ── アクティブレース予測パネル ── */}
        {activeRaceId && (() => {
          const r = races.find(rc => rc.race_id === activeRaceId)
          if (!r) return null
          const res = results[activeRaceId]
          const preds = res?.data?.predictions || []
          const rec = res?.data?.recommendation
          const raceLevel = res?.data?.race_level ?? 'normal'
          const trackColor = r.track_type === '芝' ? 'text-[#4ade80]'
            : r.track_type === 'ダート' ? 'text-[#fbbf24]'
            : 'text-[#a78bfa]'
          const idxInFiltered = filteredRaces.findIndex(rc => rc.race_id === activeRaceId)
          const prevRace = filteredRaces[idxInFiltered - 1]
          const nextRace = filteredRaces[idxInFiltered + 1]
          return (
            <div className="bg-[#111] border border-[#1e1e1e] rounded-lg overflow-hidden">
              {/* レースヘッダー */}
              <div className="px-5 py-4 border-b border-[#1e1e1e]">
                <div className="flex items-center justify-between flex-wrap gap-3">
                  <div className="space-y-1">
                    <div className="flex items-center gap-3 flex-wrap">
                      <div className="flex items-center gap-2">
                        <span className="inline-flex items-center justify-center w-10 h-10 rounded-lg bg-[#22c55e] text-black font-bold text-base shrink-0">
                          {r.race_no}R
                        </span>
                        <span className="text-[#666] text-sm">{r.venue}</span>
                      </div>
                      {r.race_name && <span className="font-semibold text-white">{r.race_name}</span>}
                      {raceLevel === 'decisive' && res?.success && <span className="text-xs text-yellow-400 px-2 py-0.5 border border-yellow-400/30 rounded-full">🔥 勝負</span>}
                      {raceLevel === 'skip' && res?.success && <span className="text-xs text-[#555] px-2 py-0.5 border border-[#333] rounded-full">見送り</span>}
                    </div>
                    <div className="flex items-center gap-2 text-xs">
                      {r.track_type && <span className={`font-medium ${trackColor}`}>{r.track_type}</span>}
                      {r.distance ? <span className="text-[#666]">{r.distance}m</span> : null}
                      {r.num_horses ? <span className="text-[#555]">{r.num_horses}頭</span> : null}
                      {r.post_time && <span className="text-[#60a5fa]">発走 {r.post_time}</span>}
                    </div>
                  </div>
                  <div className="flex items-center gap-2">
                    {/* 前後ナビ */}
                    <button
                      onClick={() => prevRace && setActiveRaceId(prevRace.race_id)}
                      disabled={!prevRace}
                      className="w-8 h-8 flex items-center justify-center rounded border border-[#222] text-[#555] hover:text-white hover:border-[#444] disabled:opacity-20 disabled:cursor-not-allowed transition-colors"
                    >
                      <svg className="w-3.5 h-3.5" fill="none" viewBox="0 0 24 24" stroke="currentColor">
                        <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M15 19l-7-7 7-7" />
                      </svg>
                    </button>
                    <button
                      onClick={() => nextRace && setActiveRaceId(nextRace.race_id)}
                      disabled={!nextRace}
                      className="w-8 h-8 flex items-center justify-center rounded border border-[#222] text-[#555] hover:text-white hover:border-[#444] disabled:opacity-20 disabled:cursor-not-allowed transition-colors"
                    >
                      <svg className="w-3.5 h-3.5" fill="none" viewBox="0 0 24 24" stroke="currentColor">
                        <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M9 5l7 7-7 7" />
                      </svg>
                    </button>
                    {/* 予測/再予測ボタン */}
                    {res?.success ? (
                      <button
                        onClick={() => predictSingle(activeRaceId)}
                        disabled={predicting}
                        className="text-xs px-3 py-1.5 bg-[#1e1e1e] text-[#888] border border-[#333] rounded-lg hover:bg-[#2a2a2a] hover:text-white disabled:opacity-40 transition-colors"
                      >
                        再予測
                      </button>
                    ) : (
                      <button
                        onClick={() => predictSingle(activeRaceId)}
                        disabled={predicting}
                        className="flex items-center gap-2 px-4 py-2 bg-white text-black text-sm font-semibold rounded-lg hover:bg-[#eee] disabled:opacity-40 disabled:cursor-not-allowed transition-colors"
                      >
                        {predicting ? (
                          <>
                            <svg className="animate-spin w-3.5 h-3.5" viewBox="0 0 24 24" fill="none">
                              <circle className="opacity-25" cx="12" cy="12" r="10" stroke="currentColor" strokeWidth="4" />
                              <path className="opacity-75" fill="currentColor" d="M4 12a8 8 0 018-8V0C5.373 0 0 5.373 0 12h4z" />
                            </svg>
                            予測中...
                          </>
                        ) : (
                          <>
                            <svg className="w-4 h-4" fill="none" viewBox="0 0 24 24" stroke="currentColor">
                              <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M13 10V3L4 14h7v7l9-11h-7z" />
                            </svg>
                            このレースを予測する
                          </>
                        )}
                      </button>
                    )}
                  </div>
                </div>
              </div>

              {/* 未予測 + 非予測中 → 空状態 */}
              {!res && !predicting && (
                <div className="flex flex-col items-center justify-center py-12 gap-4">
                  <div className="w-12 h-12 rounded-full bg-[#1a1a1a] border border-[#2a2a2a] flex items-center justify-center">
                    <svg className="w-5 h-5 text-[#555]" fill="none" viewBox="0 0 24 24" stroke="currentColor">
                      <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={1.5} d="M13 10V3L4 14h7v7l9-11h-7z" />
                    </svg>
                  </div>
                  <p className="text-sm text-[#555]">まだ予測していません</p>
                </div>
              )}

              {/* 予測中 → スピナー */}
              {!res && predicting && (
                <div className="flex flex-col items-center justify-center py-10 gap-3">
                  <svg className="animate-spin w-7 h-7 text-[#22c55e]" viewBox="0 0 24 24" fill="none">
                    <circle className="opacity-25" cx="12" cy="12" r="10" stroke="currentColor" strokeWidth="4" />
                    <path className="opacity-75" fill="currentColor" d="M4 12a8 8 0 018-8V0C5.373 0 0 5.373 0 12h4z" />
                  </svg>
                  <p className="text-sm text-[#888]">{predictProgress.current ? `${predictProgress.current} 予測中...` : '予測中...'}</p>
                </div>
              )}

              {/* エラー */}
              {res && !res.success && (
                <div className="px-5 py-6">
                  <p className="text-sm text-[#f87171] mb-3">{res.error}</p>
                  <button
                    onClick={() => predictSingle(activeRaceId)}
                    disabled={predicting}
                    className="text-xs px-4 py-2 bg-[#1a0808] text-[#f87171] border border-[#3a1a1a] rounded-lg hover:bg-[#220a0a] disabled:opacity-40 transition-colors"
                  >
                    再試行
                  </button>
                </div>
              )}

              {/* 予測成功 → テーブル + 購入セクション */}
              {res?.success && (
                <div className="border-t border-[#1e1e1e]">
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
                            const horseKey = String(p.horse_number ?? p.horse_no ?? '')
                            const liveOdds: number | null = realtimeOdds[r.race_id]?.[horseKey] ?? null
                            const effectiveOdds: number | null = liveOdds ?? p.odds ?? null
                            const ev: number | null = liveOdds != null
                              ? pNorm * liveOdds
                              : (p.expected_value ?? (p.odds != null ? pNorm * p.odds : null))
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
                                <td className="px-4 py-2.5 text-[#888] tabular-nums">
                                  {effectiveOdds != null ? (
                                    <span>{effectiveOdds}{liveOdds != null && <span className="text-[#60a5fa] text-xs ml-0.5">↻</span>}</span>
                                  ) : '—'}
                                </td>
                              </tr>
                            )
                          })
                        })()}
                      </tbody>
                    </table>
                  </div>

                  {/* 購入セクション */}
                  {(() => {
                    const edit = betEdits[r.race_id]
                    const activeBetType = edit?.betType ?? res.data?.best_bet_type ?? '単勝'
                    const allBetTypeKeys: string[] = Object.keys(res.data?.bet_types ?? {})
                      .filter((t: string) => (res.data.bet_types[t] ?? []).length > 0)
                    const activeCombos: any[] = res.data?.bet_types?.[activeBetType] ?? []
                    const selIdxs: number[] = edit?.selectedIdxs ?? Array.from(
                      { length: Math.min(rec?.purchase_count ?? 1, activeCombos.length) }, (_, i) => i
                    )
                    const unitPrice: number = edit?.unitPrice ?? rec?.unit_price ?? 100
                    const manualNos: number[] = edit?.manualNos ?? []
                    const allManualCombos: string[] = genManualCombos(manualNos, activeBetType)
                    const manualCombos: string[] = edit?.manualSelCombos ?? allManualCombos
                    const manualComboSet = new Set(manualCombos)
                    const numHorses: number = r.num_horses > 0 ? r.num_horses : 18
                    const minNeeded: number = MIN_HORSES[activeBetType] ?? 1
                    const totalCount = selIdxs.length + manualCombos.length
                    const totalCost: number = unitPrice * Math.max(totalCount, 0)
                    const selSet = new Set(selIdxs)
                    return (
                      <div className="border-t border-[#1a1a1a]">

                        {/* ── 券種 ── */}
                        <div className="px-5 pt-4 pb-3 border-b border-[#141414]">
                          <div className="flex items-center gap-2 mb-2">
                            <span className="text-[10px] font-semibold tracking-wider text-[#555] uppercase">券種</span>
                          </div>
                          <div className="flex flex-wrap gap-1.5">
                            {allBetTypeKeys.map((t: string) => (
                              <button
                                key={t}
                                onClick={() => changeBetType(r.race_id, t, res.data)}
                                className="text-xs px-3 py-1 rounded border transition-colors"
                                style={activeBetType === t
                                  ? { background: '#facc15', color: '#000', borderColor: '#facc15', fontWeight: 600 }
                                  : { background: 'transparent', color: '#666', borderColor: '#2a2a2a' }
                                }
                              >
                                {t}
                                {t === res.data?.best_bet_type && (
                                  <span className="ml-1 text-[9px] opacity-60">AI推奨</span>
                                )}
                              </button>
                            ))}
                          </div>
                        </div>

                        <div className="divide-y divide-[#141414]">
                          {/* ── AI推奨買い目 ── */}
                          <div className="px-5 py-3 space-y-2">
                            <div className="flex items-center gap-2">
                              <span className="text-[10px] font-semibold tracking-wider text-[#facc15] uppercase">AI 推奨</span>
                              <span className="text-[10px] text-[#555]">{selIdxs.length}点選択中</span>
                            </div>
                            {activeCombos.length > 0 ? (
                              <div className="flex flex-wrap gap-1.5">
                                {activeCombos.map((c: any, ci: number) => {
                                  const checked = selSet.has(ci)
                                  return (
                                    <button
                                      key={ci}
                                      onClick={() => {
                                        if (!edit) {
                                          setBetEdits(prev => ({
                                            ...prev,
                                            [r.race_id]: { betType: activeBetType, selectedIdxs: [...selIdxs], unitPrice }
                                          }))
                                        }
                                        toggleCombo(r.race_id, ci)
                                      }}
                                      className="text-xs px-2.5 py-1 rounded font-mono border transition-colors"
                                      style={checked
                                        ? { background: '#1a2a10', color: '#facc15', borderColor: '#3a4a10' }
                                        : { background: '#0a0a0a', color: '#383838', borderColor: '#1a1a1a' }
                                      }
                                    >
                                      {c.combination}
                                      {c.ev != null && (
                                        <span className="ml-1 opacity-50 text-[10px]">{Number(c.ev).toFixed(2)}</span>
                                      )}
                                    </button>
                                  )
                                })}
                              </div>
                            ) : (
                              <p className="text-xs text-[#333]">この券種の候補なし</p>
                            )}
                          </div>

                          {/* ── 手動追加: 馬番ボタン → 組み合わせ個別選択 ── */}
                          <div className="px-5 py-3 space-y-2">
                            <div className="flex items-center gap-2 flex-wrap">
                              <span className="text-[10px] font-semibold tracking-wider text-[#818cf8] uppercase">手動追加</span>
                              {manualNos.length > 0 && manualNos.length < minNeeded ? (
                                <span className="text-[10px] text-[#555]">あと{minNeeded - manualNos.length}頭</span>
                              ) : manualNos.length >= minNeeded ? (
                                <span className="text-[10px] text-[#818cf8]">{manualCombos.length}/{allManualCombos.length}点選択中</span>
                              ) : null}
                              {manualNos.length >= minNeeded && (
                                <>
                                  <button
                                    onClick={() => setBetEdits(prev => ({ ...prev, [r.race_id]: { ...(prev[r.race_id] ?? { betType: activeBetType, selectedIdxs: [...selIdxs], unitPrice }), manualSelCombos: null } }))}
                                    className="text-[10px] text-[#444] hover:text-[#818cf8] transition-colors"
                                  >全選択</button>
                                  <button
                                    onClick={() => setBetEdits(prev => ({ ...prev, [r.race_id]: { ...(prev[r.race_id] ?? { betType: activeBetType, selectedIdxs: [...selIdxs], unitPrice }), manualSelCombos: [] } }))}
                                    className="text-[10px] text-[#444] hover:text-[#f87171] transition-colors"
                                  >全解除</button>
                                </>
                              )}
                              {manualNos.length > 0 && (
                                <button
                                  onClick={() => setBetEdits(prev => ({
                                    ...prev,
                                    [r.race_id]: { betType: activeBetType, selectedIdxs: [...selIdxs], unitPrice, manualNos: [], manualSelCombos: null }
                                  }))}
                                  className="text-[10px] text-[#444] hover:text-[#f87171] ml-auto transition-colors"
                                >クリア</button>
                              )}
                            </div>
                            <div className="flex flex-wrap gap-1">
                              {Array.from({ length: numHorses }, (_, i) => i + 1).map(no => {
                                const isSelected = manualNos.includes(no)
                                return (
                                  <button
                                    key={no}
                                    onClick={() => toggleManualNo(r.race_id, no, { betType: activeBetType, selectedIdxs: [...selIdxs], unitPrice })}
                                    className="text-xs w-8 h-8 rounded font-mono font-bold border transition-all"
                                    style={isSelected
                                      ? { background: '#1a1a2e', color: '#818cf8', borderColor: '#818cf8', boxShadow: '0 0 6px #818cf844' }
                                      : { background: '#0a0a0a', color: '#444', borderColor: '#1a1a1a' }
                                    }
                                  >
                                    {no}
                                  </button>
                                )
                              })}
                            </div>
                            {manualNos.length >= minNeeded && allManualCombos.length > 0 && (
                              <div className={`flex gap-1 flex-wrap pt-0.5${allManualCombos.length > 24 ? ' max-h-32 overflow-y-auto pr-1' : ''}`}>
                                {allManualCombos.map((c: string) => {
                                  const isComboSel = manualComboSet.has(c)
                                  return (
                                    <button
                                      key={c}
                                      onClick={() => toggleManualCombo(r.race_id, c, allManualCombos, { betType: activeBetType, selectedIdxs: [...selIdxs], unitPrice })}
                                      className="text-xs px-2 py-0.5 rounded font-mono border transition-colors"
                                      style={isComboSel
                                        ? { background: '#1a1a2e', color: '#818cf8', borderColor: '#818cf8' }
                                        : { background: '#0a0a0a', color: '#252525', borderColor: '#111' }
                                      }
                                    >
                                      {c}
                                    </button>
                                  )
                                })}
                              </div>
                            )}
                          </div>
                        </div>

                        {/* ── フッター: 単価・合計・購入ボタン ── */}
                        <div className="px-5 py-3 border-t border-[#141414] flex flex-wrap items-center justify-between gap-3">
                          <div className="flex items-center gap-3 flex-wrap">
                            <div className="flex items-center gap-1.5">
                              <span className="text-xs text-[#555]">単価</span>
                              <span className="text-xs text-[#666]">¥</span>
                              <input
                                type="number"
                                min={100}
                                step={100}
                                value={unitPrice}
                                onChange={e => {
                                  if (!edit) {
                                    setBetEdits(prev => ({
                                      ...prev,
                                      [r.race_id]: { betType: activeBetType, selectedIdxs: [...selIdxs], unitPrice }
                                    }))
                                  }
                                  setBetUnitPrice(r.race_id, Math.max(100, Number(e.target.value)))
                                }}
                                className="w-20 text-xs bg-[#0a0a0a] border border-[#333] rounded px-2 py-1 text-white text-right"
                              />
                            </div>
                            <div className="flex items-center gap-1.5 text-xs">
                              <span className="text-[#444]">×</span>
                              {selIdxs.length > 0 && <span className="text-[#facc15]">{selIdxs.length}点(AI)</span>}
                              {selIdxs.length > 0 && manualCombos.length > 0 && <span className="text-[#444]">+</span>}
                              {manualCombos.length > 0 && <span className="text-[#818cf8]">{manualCombos.length}点(手動)</span>}
                              {totalCount === 0 && <span className="text-[#444]">0点</span>}
                              <span className="text-[#444]">=</span>
                              <span className="text-white font-bold">¥{totalCost.toLocaleString()}</span>
                            </div>
                            {rec?.kelly_recommended_amount != null && (
                              <span className="text-xs text-[#444]">ケリー推奨: <span className="text-[#555]">¥{rec.kelly_recommended_amount.toLocaleString()}</span></span>
                            )}
                          </div>
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
                              onClick={() => recordPurchase(r.race_id, r.venue, res)}
                              disabled={purchasing.has(r.race_id) || totalCount === 0}
                              className="text-xs px-5 py-1.5 bg-[#facc15] text-black font-semibold rounded hover:bg-[#fde047] disabled:opacity-40 disabled:cursor-not-allowed transition-colors"
                            >
                              {purchasing.has(r.race_id) ? '保存中...' : `購入を記録する（¥${totalCost.toLocaleString()}）`}
                            </button>
                          )}
                        </div>
                      </div>
                    )
                  })()}
                </div>
              )}
            </div>
          )
        })()}

        {/* ── Layer 4: リアルタイムオッズ & 購入リストエクスポート ── */}
        {Object.values(results).some(r => r.success) && (
          <div className="bg-[#111] border border-[#1e1e1e] rounded-lg p-6 space-y-5">
            <h2 className="text-sm font-semibold text-white">購入リスト出力</h2>

            {/* 購入リスト出力 */}
            <div className="p-4 bg-[#0a0a0a] border border-[#1e1e1e] rounded-lg space-y-4">
              <div className="grid grid-cols-2 gap-3">
                <div>
                  <label className="text-xs text-[#666] block mb-1.5">EV 最低値</label>
                  <input
                    type="number"
                    min={0.5}
                    max={5.0}
                    step={0.1}
                    value={exportMinEv}
                    onChange={e => setExportMinEv(parseFloat(e.target.value) || 1.0)}
                    className="w-full px-3 py-2 bg-[#111] border border-[#1e1e1e] rounded text-white text-sm focus:outline-none focus:border-[#333]"
                  />
                </div>
                <div>
                  <label className="text-xs text-[#666] block mb-1.5">最大点数 / R</label>
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
                  CSV（IPAT）
                </button>
              </div>
            </div>
          </div>
        )}

        {/* ── ナビ ── */}
        <div className="py-3 flex justify-end">
          <Link
            href="/dashboard"
            className="shrink-0 flex items-center gap-1.5 text-[#555] hover:text-white text-xs transition-colors"
          >
            履歴・統計へ
            <svg className="w-3 h-3" fill="none" viewBox="0 0 24 24" stroke="currentColor">
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

