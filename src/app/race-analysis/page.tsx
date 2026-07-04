'use client'
import { useState, useEffect, useCallback, useMemo } from 'react'
import { useSearchParams } from 'next/navigation'
import { Logo } from '@/components/Logo'
import Link from 'next/link'
import type { RaceItem } from '@/lib/types'
import { todayStr } from '@/lib/types'
import { useRaceCache } from '@/hooks/useRaceCache'
import { RacePredictionPanel } from '@/components/RacePredictionPanel'
import { RaceFeaturePanel } from '@/components/RaceFeaturePanel'
import type { RacePredictResult, FeatureData } from '@/lib/race-analysis-types'
import { authFetch } from '@/lib/auth-fetch'

// ── 日付・週ナビヘルパー ─────────────────────────────────────────────────
function shiftDays(dateStr: string, days: number): string {
  const y = parseInt(dateStr.slice(0, 4))
  const m = parseInt(dateStr.slice(4, 6)) - 1
  const d = parseInt(dateStr.slice(6, 8))
  const dt = new Date(y, m, d)
  dt.setDate(dt.getDate() + days)
  return `${dt.getFullYear()}${String(dt.getMonth() + 1).padStart(2, '0')}${String(dt.getDate()).padStart(2, '0')}`
}

function getWeekTabs(dateStr: string): Array<{ str: string; label: string }> {
  const y = parseInt(dateStr.slice(0, 4))
  const m = parseInt(dateStr.slice(4, 6)) - 1
  const d = parseInt(dateStr.slice(6, 8))
  const dt = new Date(y, m, d)
  const dow = dt.getDay() // 0=Sun, 6=Sat
  const satOffset = dow === 0 ? -1 : 6 - dow
  const sat = new Date(dt); sat.setDate(dt.getDate() + satOffset)
  const sun = new Date(sat); sun.setDate(sat.getDate() + 1)
  const fmt = (x: Date) =>
    `${x.getFullYear()}${String(x.getMonth() + 1).padStart(2, '0')}${String(x.getDate()).padStart(2, '0')}`
  const DOW = ['日', '月', '火', '水', '木', '金', '土']
  const fmtLabel = (x: Date) => `${x.getMonth() + 1}/${x.getDate()}(${DOW[x.getDay()]})`
  return [
    { str: fmt(sat), label: fmtLabel(sat) },
    { str: fmt(sun), label: fmtLabel(sun) },
  ]
}

function formatDateDisplay(dateStr: string): string {
  const y = parseInt(dateStr.slice(0, 4))
  const m = parseInt(dateStr.slice(4, 6))
  const d = parseInt(dateStr.slice(6, 8))
  const dt = new Date(y, m - 1, d)
  const DOW = ['日', '月', '火', '水', '木', '金', '土']
  return `${y}年${m}月${d}日（${DOW[dt.getDay()]}）`
}

function raceBadgeStyle(trackType: string): string {
  if (trackType?.startsWith('芝')) return 'bg-[#0f4a30] border border-[#1a7a4a] text-[#33dd88]'
  if (trackType?.startsWith('障')) return 'bg-[#4a0f0f] border border-[#7a1a1a] text-[#dd4444]'
  return 'bg-[#3a2a0a] border border-[#6a4a12] text-[#ddaa33]'
}
function raceTrackColor(trackType: string): string {
  if (trackType?.startsWith('芝')) return 'text-[#33cc77]'
  if (trackType?.startsWith('障')) return 'text-[#ff5555]'
  return 'text-[#ddaa33]'
}

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


// ── 結果照合タブの型 ─────────────────────────────────────────────────
type PredictionLogEntry = {
  horse_id: string
  horse_name: string
  horse_number: number
  predicted_rank: number
  win_probability: number
  p_raw: number
  odds: number | null
  popularity: number | null
  model_id: string
  predicted_at: string
  actual_finish: number | null
  finish_time: string | null
  actual_last3f: number | null
  actual_odds: number | null
}
type PredictionHistoryResult = {
  race_id: string
  has_prediction: boolean
  has_result: boolean
  top1_win: boolean
  top1_place3: boolean
  predictions: PredictionLogEntry[]
}

export default function RaceAnalysisPage() {
  const searchParams = useSearchParams()
  const initialDate = searchParams.get('date') ?? todayStr()
  const initialRaceId = searchParams.get('race_id') ?? ''

  const [date, setDate] = useState(initialDate)
  const [races, setRaces] = useState<RaceItem[]>([])
  const [racesLoading, setRacesLoading] = useState(false)
  const [selectedRaceId, setSelectedRaceId] = useState('')
  const [selectedModelId, setSelectedModelId] = useState<string>('')
  const [selectedPlace3ModelId, setSelectedPlace3ModelId] = useState<string>('')
  const [models, setModels] = useState<{ model_id: string; target: string; cv_auc_mean: number }[]>([])
  const [tab, setTab] = useState<'predict' | 'features' | 'result'>('predict')
  const [venueFilter, setVenueFilter] = useState<string>('全て')

  // 結果照合タブ
  const [resultData, setResultData] = useState<PredictionHistoryResult | null>(null)
  const [resultLoading, setResultLoading] = useState(false)

  // 競馬場フィルタ済みレース一覧
  const filteredRaces = useMemo(() => {
    if (venueFilter === '全て') return races
    return races.filter(r => r.venue === venueFilter)
  }, [races, venueFilter])

  // 現在選択レースの前後インデックス
  const currentIdx = filteredRaces.findIndex(r => r.race_id === selectedRaceId)
  const prevRace = currentIdx > 0 ? filteredRaces[currentIdx - 1] : null
  const nextRace = currentIdx >= 0 && currentIdx < filteredRaces.length - 1 ? filteredRaces[currentIdx + 1] : null

  // 開催競馬場の一覧（重複排除）
  const venueOptions = useMemo(() => {
    const venues = Array.from(new Set(races.map(r => r.venue).filter(Boolean)))
    return ['全て', ...venues]
  }, [races])

  // 競馬場グループ（SPAIA風ヘッダー付き）
  const venueGroups = useMemo(() => {
    const grouped = new Map<string, typeof races>()
    filteredRaces.forEach(r => {
      if (!grouped.has(r.venue)) grouped.set(r.venue, [])
      grouped.get(r.venue)!.push(r)
    })
    return Array.from(grouped.entries()).map(([venue, rs]) => {
      const first = rs[0]
      const header =
        first?.kai && first?.day
          ? `${first.kai}回${venue}${first.day}日`
          : venue
      return { venue, header, races: rs }
    })
  }, [filteredRaces])

  const [predictResult, setPredictResult] = useState<RacePredictResult | null>(null)
  const [featData, setFeatData] = useState<FeatureData | null>(null)
  const [dataLoading, setDataLoading] = useState(false)
  const [error, setError] = useState('')
  const [fromCache, setFromCache] = useState(false)
  const [cachedAt, setCachedAt] = useState<number | null>(null)
  const [fallbackHorses, setFallbackHorses] = useState<{
    race_name: string; venue: string; date: string; distance: number; track_type: string
    horses: { horse_number: number; bracket_number: number; horse_name: string; sex_age: string; jockey_name: string; odds: number | null; popularity: number | null; trainer_name: string }[]
  } | null>(null)

  const raceCache = useRaceCache()

  // 結果照合データを取得
  const loadResultData = useCallback(async (raceId: string) => {
    setResultLoading(true)
    setResultData(null)
    try {
      const res = await authFetch(`/api/prediction-history/${raceId}`, {
        signal: AbortSignal.timeout(15_000),
      })
      if (res.ok) setResultData(await res.json())
    } catch { }
    finally { setResultLoading(false) }
  }, [])

  // モデル一覧を取得（初回のみ）
  useEffect(() => {
    authFetch('/api/models?ultimate=true')
      .then(r => r.ok ? r.json() : null)
      .then(data => {
        if (data?.models?.length) {
          // no_odds モデルは除外し、末尾の作成日時降順（最新が先頭）に並べる
          const filtered = data.models
            .filter((m: any) => !m.model_id.includes('no_odds'))
            .sort((a: any, b: any) => modelCreatedAt(b.model_id).localeCompare(modelCreatedAt(a.model_id)))
          setModels(filtered)
        }
      })
      .catch(() => {})
  }, [])

  const loadRaces = useCallback(async () => {
    setRacesLoading(true)
    setRaces([])
    setSelectedRaceId('')
    setPredictResult(null)
    setFeatData(null)
    setFallbackHorses(null)
    setFromCache(false)
    setCachedAt(null)
    setError('')
    try {
      const d = date.replace(/-/g, '')
      const res = await authFetch(`/api/races/by-date?date=${d}`)
      if (res.ok) {
        const fetchedRaces: RaceItem[] = (await res.json()).races || []
        setRaces(fetchedRaces)
        if (initialRaceId && fetchedRaces.some(r => r.race_id === initialRaceId)) {
          // useEffect(レース監視)がロードするのでここでは設定のみ
          setSelectedRaceId(initialRaceId)
        }
      }
    } catch { }
    finally { setRacesLoading(false) }
  }, [date, initialRaceId])

  useEffect(() => { loadRaces() }, [loadRaces])

  // URL パラメータで race_id が指定されている場合、レース一覧ロード後に自動選択
  useEffect(() => {
    if (initialRaceId && races.length > 0 && !selectedRaceId) {
      if (races.some(r => r.race_id === initialRaceId)) {
        loadRaceData(initialRaceId)
      }
    }
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [races])

  const loadRaceData = useCallback(async (raceId: string, forceRefresh = false, modelId?: string) => {
    const effectiveModelId = modelId ?? selectedModelId
    setSelectedRaceId(raceId)
    setError('')
    setResultData(null)  // レース切り替え時に結果照合をリセット

    // キャッシュキーにモデルIDを含めることで、モデル切り替え時は必ず再予測
    const cacheKey = effectiveModelId ? `${raceId}__${effectiveModelId}` : raceId
    if (!forceRefresh) {
      const cached = raceCache.get(cacheKey)
      if (cached) {
        setPredictResult(cached.predictResult)
        setFeatData(cached.featData)
        setFromCache(true)
        setCachedAt(cached.cachedAt)
        if (!cached.featData) {
          authFetch(`/api/debug/race/${raceId}/features`)
            .then(r => r.ok ? r.json() : null)
            .then(feat => { if (feat) { setFeatData(feat); raceCache.updateFeat(cacheKey, feat) } })
            .catch(() => {})
        }
        return
      }
    }

    setDataLoading(true)
    setFromCache(false)
    setCachedAt(null)
    setPredictResult(null)
    setFeatData(null)
    setFallbackHorses(null)
    try {
      const body: Record<string, unknown> = { race_id: raceId, bankroll: 10000, risk_mode: 'balanced' }
      if (effectiveModelId) body.model_id = effectiveModelId
      if (selectedPlace3ModelId) body.place3_model_id = selectedPlace3ModelId
      const [predRes, featRes] = await Promise.all([
        authFetch('/api/analyze-race', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify(body),
        }),
        authFetch(`/api/debug/race/${raceId}/features`),
      ])
      if (!predRes.ok) {
        const e = await predRes.json()
        throw new Error(e.detail || `HTTP ${predRes.status}`)
      }
      const [pred, feat] = await Promise.all([predRes.json(), featRes.ok ? featRes.json() : null])
      const now = Date.now()
      raceCache.set(cacheKey, { predictResult: pred, featData: feat, cachedAt: now })
      setPredictResult(pred)
      setFeatData(feat)
      setFromCache(false)
      setCachedAt(now)
    } catch (e: unknown) {
      setError(e instanceof Error ? e.message : 'エラーが発生しました')
      // 予測失敗時でもDBの出走馬データを取得して表示する
      try {
        const fbRes = await authFetch(`/api/races/${raceId}/horses`)
        if (fbRes.ok) {
          const fb = await fbRes.json()
          if (fb.horses?.length > 0) setFallbackHorses(fb)
        }
      } catch { }
    } finally { setDataLoading(false) }
  }, [raceCache, selectedModelId])

  const ri = predictResult?.race_info
  const preds = predictResult?.predictions ?? []

  return (
    <div className="min-h-screen bg-[#0a0a0a] text-white flex flex-col">
      <header className="border-b border-[#1e1e1e] px-6 py-4 flex items-center justify-between shrink-0">
        <div className="flex items-center gap-4">
          <Logo href="/home" />
          <span className="text-sm text-[#888]">予測結果確認</span>
          {fromCache && cachedAt && (
            <div className="flex items-center gap-2">
              <span className="text-[10px] px-2 py-0.5 bg-[#1a1a00] border border-[#333300] rounded text-[#bbbb00]">
                キャッシュ済み · {(() => {
                  const mins = Math.round((Date.now() - cachedAt) / 60000)
                  return mins < 1 ? 'たった今' : `${mins}分前`
                })()}
              </span>
              <button
                onClick={() => loadRaceData(selectedRaceId, true)}
                className="text-[10px] text-[#555] hover:text-[#888] border border-[#222] rounded px-2 py-0.5 hover:border-[#333] transition-colors"
              >
                再計算
              </button>
            </div>
          )}
        </div>
        <Link href="/home" className="flex items-center gap-1 text-xs text-[#555] hover:text-white transition-colors">
          <svg className="w-3 h-3" fill="none" viewBox="0 0 24 24" stroke="currentColor">
            <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M15 19l-7-7 7-7" />
          </svg>
          ホーム
        </Link>
      </header>

      <div className="flex flex-1 overflow-hidden">
        {/* 左サイドバー: 週ナビ + SPAIA風レース一覧 */}
        <aside className="w-64 shrink-0 border-r border-[#1a1a1a] flex flex-col bg-[#0c0c0c]">

          {/* ── 週ナビゲーション ── */}
          <div className="px-3 pt-3 pb-2 border-b border-[#1a1a1a]">
            <div className="flex items-center justify-between mb-2">
              <button
                onClick={() => setDate(shiftDays(date, -7))}
                className="flex items-center gap-0.5 text-[11px] text-[#555] hover:text-[#aaa] transition-colors px-1"
              >
                <svg className="w-3 h-3" fill="none" viewBox="0 0 24 24" stroke="currentColor">
                  <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M15 19l-7-7 7-7" />
                </svg>
                前週
              </button>
              <div className="flex gap-0.5">
                {getWeekTabs(date).map(tab => (
                  <button
                    key={tab.str}
                    onClick={() => setDate(tab.str)}
                    className={`px-2.5 py-1 rounded text-[11px] font-medium transition-colors ${
                      date === tab.str
                        ? 'bg-[#007a70] text-white'
                        : 'text-[#555] hover:text-white hover:bg-[#1a1a1a]'
                    }`}
                  >
                    {tab.label}
                  </button>
                ))}
              </div>
              <button
                onClick={() => setDate(shiftDays(date, 7))}
                className="flex items-center gap-0.5 text-[11px] text-[#555] hover:text-[#aaa] transition-colors px-1"
              >
                翌週
                <svg className="w-3 h-3" fill="none" viewBox="0 0 24 24" stroke="currentColor">
                  <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M9 5l7 7-7 7" />
                </svg>
              </button>
            </div>
            <p className="text-center text-[10px] text-[#3a3a3a]">{formatDateDisplay(date)}</p>
          </div>

          {/* ── モデル選択 ── */}
          <div className="px-3 py-2 space-y-1 border-b border-[#1a1a1a]">
            <select
              value={selectedModelId}
              onChange={e => {
                setSelectedModelId(e.target.value)
                if (selectedRaceId) loadRaceData(selectedRaceId, true, e.target.value)
              }}
              className="w-full px-2 py-1 bg-[#111] border border-[#1e1e1e] rounded text-white text-[11px] focus:outline-none focus:border-[#2a2a2a] truncate"
            >
              <option value="">
                最新モデル（自動）{models.length > 0 ? ` — ${modelLabel(models[0].model_id, models[0].target)}` : ''}
              </option>
              {models.map(m => (
                <option key={m.model_id} value={m.model_id}>
                  {modelLabel(m.model_id, m.target, m.cv_auc_mean)}
                </option>
              ))}
            </select>
            <select
              value={selectedPlace3ModelId}
              onChange={e => {
                setSelectedPlace3ModelId(e.target.value)
                if (selectedRaceId) loadRaceData(selectedRaceId, true, selectedModelId)
              }}
              className="w-full px-2 py-1 bg-[#111] border border-[#1e1e1e] rounded text-white text-[11px] focus:outline-none focus:border-[#2a2a2a] truncate"
            >
              <option value="">最新 place3 自動</option>
              {models.filter(m => m.target === 'place3').map(m => (
                <option key={m.model_id} value={m.model_id}>
                  {modelLabel(m.model_id, m.target, m.cv_auc_mean)}
                </option>
              ))}
            </select>
          </div>

          {/* ── SPAIA風 レースカードリスト ── */}
          <div className="flex-1 overflow-y-auto">
            {racesLoading && (
              <div className="pt-1">
                {[1, 2, 3, 4, 5].map(i => (
                  <div key={i} className="px-3 py-2.5 border-b border-[#0e0e0e] animate-pulse flex gap-2.5">
                    <div className="w-9 h-9 rounded bg-[#1a1a1a] shrink-0" />
                    <div className="flex-1 pt-0.5 space-y-1.5">
                      <div className="h-3 w-28 bg-[#1e1e1e] rounded" />
                      <div className="h-2 w-20 bg-[#161616] rounded" />
                    </div>
                  </div>
                ))}
              </div>
            )}
            {!racesLoading && races.length === 0 && (
              <div className="p-4 space-y-1">
                <p className="text-xs text-[#555]">レースが見つかりません</p>
                <p className="text-[10px] text-[#444]">日付を変更するか、データ取得ページでデータを取得してください</p>
              </div>
            )}
            {!racesLoading && venueGroups.map(group => (
              <div key={group.venue}>
                {/* 競馬場ヘッダー */}
                <div className="px-3 py-1.5 bg-[#0e0e0e] border-b border-[#181818] sticky top-0 z-10 flex items-center gap-2">
                  <span className="text-[11px] font-bold text-[#009a8a]">{group.header}</span>
                  <span className="text-[10px] text-[#333] ml-auto">{group.races.length}R</span>
                </div>
                {/* レースカード */}
                {group.races.map(r => {
                  const isSelected = selectedRaceId === r.race_id
                  return (
                    <button
                      key={r.race_id}
                      onClick={() => loadRaceData(r.race_id)}
                      disabled={dataLoading}
                      className={`w-full text-left px-3 py-2.5 border-b border-[#0e0e0e] transition-all ${
                        isSelected
                          ? 'bg-[#0c1e1c] border-l-2 border-l-[#00a890]'
                          : 'hover:bg-[#111] border-l-2 border-l-transparent'
                      }`}
                    >
                      <div className="flex items-start gap-2.5">
                        {/* レース番号バッジ（SPAIA風） */}
                        <div className={`w-9 h-9 rounded flex flex-col items-center justify-center shrink-0 ${raceBadgeStyle(r.track_type)}`}>
                          <span className="text-[8px] opacity-60 leading-none">R</span>
                          <span className="text-[14px] font-black leading-tight">{r.race_no}</span>
                        </div>
                        {/* レース情報 */}
                        <div className="flex-1 min-w-0 pt-0.5">
                          <div className="text-xs text-white font-medium truncate leading-snug">
                            {r.race_name || `${r.race_no}レース`}
                          </div>
                          <div className="flex items-center gap-1 mt-1 flex-wrap">
                            {r.post_time && (
                              <span className="text-[10px] text-[#666] font-mono">{r.post_time}</span>
                            )}
                            {r.post_time && <span className="text-[10px] text-[#333]">·</span>}
                            <span className={`text-[10px] font-semibold ${raceTrackColor(r.track_type)}`}>
                              {r.track_type}
                            </span>
                            {r.distance > 0 && (
                              <span className="text-[10px] text-[#555]">{r.distance}m</span>
                            )}
                            <span className="text-[10px] text-[#333]">·</span>
                            <span className="text-[10px] text-[#555]">{r.num_horses}頭</span>
                          </div>
                        </div>
                      </div>
                    </button>
                  )
                })}
              </div>
            ))}
          </div>
        </aside>

        {/* 右パネル: レースカード + タブ + コンテンツ */}
        <main className="flex-1 overflow-hidden flex flex-col">
          {!selectedRaceId && !dataLoading && (
            <div className="flex-1 flex items-center justify-center text-[#333] text-sm">
              ← 左のレースを選択してください
            </div>
          )}
          {/* 前/次レースナビゲーション */}
          {(prevRace || nextRace) && !dataLoading && (
            <div className="flex items-center justify-between px-6 py-2 border-b border-[#1a1a1a] shrink-0">
              <button
                onClick={() => prevRace && loadRaceData(prevRace.race_id)}
                disabled={!prevRace || dataLoading}
                className="flex items-center gap-1 text-xs text-[#555] hover:text-white disabled:opacity-30 transition-colors"
              >
                ← {prevRace ? `${prevRace.venue} ${prevRace.race_no}R` : ''}
              </button>
              <span className="text-[10px] text-[#444]">
                {currentIdx + 1} / {filteredRaces.length}
              </span>
              <button
                onClick={() => nextRace && loadRaceData(nextRace.race_id)}
                disabled={!nextRace || dataLoading}
                className="flex items-center gap-1 text-xs text-[#555] hover:text-white disabled:opacity-30 transition-colors"
              >
                {nextRace ? `${nextRace.venue} ${nextRace.race_no}R` : ''} →
              </button>
            </div>
          )}

          {dataLoading && (
            <div className="flex-1 flex flex-col p-6 gap-4">
              {/* スケルトン: レース情報カード */}
              <div className="animate-pulse">
                <div className="flex justify-between items-start">
                  <div className="space-y-2">
                    <div className="h-2.5 w-32 bg-[#1e1e1e] rounded" />
                    <div className="h-5 w-48 bg-[#222] rounded" />
                  </div>
                  <div className="flex gap-3">
                    {[1,2,3].map(i => (
                      <div key={i} className="bg-[#111] border border-[#1e1e1e] rounded px-5 py-4">
                        <div className="h-2 w-8 bg-[#1e1e1e] rounded mb-2" />
                        <div className="h-3.5 w-10 bg-[#222] rounded" />
                      </div>
                    ))}
                  </div>
                </div>
              </div>
              {/* スケルトン: 馬リスト */}
              <div className="space-y-2 animate-pulse mt-6">
                {[1,2,3,4,5].map(i => (
                  <div key={i} className="flex items-center gap-3 p-3 bg-[#111] rounded border border-[#1e1e1e]">
                    <div className="w-7 h-7 rounded-full bg-[#1e1e1e] shrink-0" />
                    <div className="flex-1 space-y-1.5">
                      <div className="h-3 w-24 bg-[#222] rounded" />
                      <div className="h-2 w-16 bg-[#1a1a1a] rounded" />
                    </div>
                    <div className="h-6 w-20 bg-[#1a1a1a] rounded" />
                    <div className="h-3.5 w-10 bg-[#1e1e1e] rounded" />
                  </div>
                ))}
              </div>
            </div>
          )}
          {error && (
            <div className="m-4 p-3 bg-[#1a0000] border border-[#f87171] rounded text-[#f87171] text-sm">{error}</div>
          )}

          {/* 予測失敗時のフォールバック: DBの出走馬データ表示 */}
          {error && fallbackHorses && !dataLoading && (
            <div className="m-4 space-y-3">
              <div className="flex items-center gap-2">
                <span className="text-xs text-[#666]">{fallbackHorses.venue}</span>
                <span className="text-xs text-[#555]">·</span>
                <span className="text-xs text-[#666]">{fallbackHorses.date}</span>
                <span className="text-xs text-[#555]">·</span>
                <span className="text-xs text-[#666]">{fallbackHorses.track_type}{fallbackHorses.distance ? ` ${fallbackHorses.distance}m` : ''}</span>
                <span className="ml-auto text-[10px] text-[#555] bg-[#1a1a1a] border border-[#2a2a2a] rounded px-2 py-0.5">ML予測なし ・ 出走表のみ</span>
              </div>
              <div className="bg-[#111] border border-[#1e1e1e] rounded overflow-hidden">
                <table className="w-full text-xs">
                  <thead>
                    <tr className="border-b border-[#1a1a1a]">
                      <th className="px-3 py-2 text-left text-[#555] font-normal w-8">湧</th>
                      <th className="px-3 py-2 text-left text-[#555] font-normal w-6">番</th>
                      <th className="px-3 py-2 text-left text-[#555] font-normal">馬名</th>
                      <th className="px-3 py-2 text-left text-[#555] font-normal">性齢</th>
                      <th className="px-3 py-2 text-left text-[#555] font-normal">騎手</th>
                      <th className="px-3 py-2 text-left text-[#555] font-normal">調教師</th>
                      <th className="px-3 py-2 text-right text-[#555] font-normal">人気</th>
                      <th className="px-3 py-2 text-right text-[#555] font-normal">オッズ</th>
                    </tr>
                  </thead>
                  <tbody>
                    {fallbackHorses.horses.map(h => (
                      <tr key={h.horse_number} className="border-b border-[#0d0d0d] hover:bg-[#141414]">
                        <td className="px-3 py-2 text-[#555]">{h.bracket_number}</td>
                        <td className="px-3 py-2 font-bold">{h.horse_number}</td>
                        <td className="px-3 py-2 text-white">{h.horse_name}</td>
                        <td className="px-3 py-2 text-[#888]">{h.sex_age}</td>
                        <td className="px-3 py-2 text-[#aaa]">{h.jockey_name}</td>
                        <td className="px-3 py-2 text-[#666]">{h.trainer_name}</td>
                        <td className="px-3 py-2 text-right text-[#888]">{h.popularity ?? '—'}</td>
                        <td className="px-3 py-2 text-right font-mono">{h.odds != null ? h.odds.toFixed(1) : '—'}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            </div>
          )}

          {predictResult && !dataLoading && (
            <>
              {/* レース情報カード */}
              <div className="px-6 pt-5 pb-4 border-b border-[#1e1e1e] shrink-0">
                <div className="flex items-start justify-between gap-4">
                  <div>
                    <div className="flex items-center gap-2 mb-1">
                      <span className="text-xs text-[#666]">{ri?.venue}</span>
                      <span className="text-xs text-[#666]">·</span>
                      <span className="text-xs text-[#666]">{ri?.date}</span>
                      {predictResult.pro_evaluation && (
                        <span className="ml-1 text-[10px] px-1.5 py-0.5 bg-[#1e1e1e] border border-[#333] rounded text-[#888]">
                          {predictResult.pro_evaluation.race_level} · 信頼度 {Math.round(predictResult.pro_evaluation.confidence * 100)}%
                        </span>
                      )}
                    </div>
                    <h2 className="text-lg font-bold">{ri?.race_name || `${ri?.venue} レース`}</h2>
                  </div>
                  <div className="flex gap-3 shrink-0">
                    {[
                      { label: 'コース', value: ri?.track_type || '—' },
                      { label: '距離', value: ri?.distance ? `${ri.distance}m` : '—' },
                      { label: '頭数', value: preds.length > 0 ? `${preds.length}頭` : '—' },
                    ].map(s => (
                      <div key={s.label} className="bg-[#111] border border-[#1e1e1e] rounded px-3 py-2 text-center min-w-[60px]">
                        <div className="text-[10px] text-[#555] mb-0.5">{s.label}</div>
                        <div className="text-sm font-medium">{s.value}</div>
                      </div>
                    ))}
                  </div>
                </div>
              </div>

              {/* タブバー */}
              <div className="flex gap-0 border-b border-[#1e1e1e] shrink-0 px-6">
                {([
                  { key: 'predict', label: `予測結果（${preds.length}頭）` },
                  { key: 'features', label: `特徴量分析${featData ? `（${featData.feature_count}列）` : ''}` },
                  { key: 'result', label: '結果照合' },
                ] as const).map(t => (
                  <button
                    key={t.key}
                    onClick={() => {
                      setTab(t.key)
                      if (t.key === 'result' && !resultData && !resultLoading) {
                        loadResultData(selectedRaceId)
                      }
                    }}
                    className={`px-4 py-2.5 text-xs font-semibold border-b-2 transition-colors ${tab === t.key ? 'border-white text-white' : 'border-transparent text-[#555] hover:text-[#888]'}`}
                  >
                    {t.label}
                  </button>
                ))}
              </div>

              {/* タブコンテンツ */}
              {tab === 'predict' && <RacePredictionPanel result={predictResult} />}
              {tab === 'features' && (
                featData
                  ? <RaceFeaturePanel featData={featData} predictions={preds} />
                  : <div className="flex-1 flex items-center justify-center text-[#555] text-sm">特徴量データがありません</div>
              )}
              {tab === 'result' && (
                <div className="flex-1 overflow-y-auto p-6">
                  <ResultComparePanel
                    raceId={selectedRaceId}
                    data={resultData}
                    loading={resultLoading}
                    onRefresh={() => loadResultData(selectedRaceId)}
                  />
                </div>
              )}
            </>
          )}
        </main>
      </div>
    </div>
  )
}

// ── 結果照合パネル ────────────────────────────────────────────────────
function ResultComparePanel({
  raceId, data, loading, onRefresh,
}: {
  raceId: string
  data: PredictionHistoryResult | null
  loading: boolean
  onRefresh: () => void
}) {
  if (loading) {
    return (
      <div className="flex items-center justify-center gap-2 text-[#555] text-sm py-16">
        <div className="w-4 h-4 border border-[#555] border-t-transparent rounded-full animate-spin" />
        照合中...
      </div>
    )
  }

  if (!data) {
    return (
      <div className="flex flex-col items-center justify-center gap-3 text-[#555] py-16">
        <p className="text-sm">まだ読み込まれていません</p>
        <button onClick={onRefresh}
          className="text-xs px-3 py-1.5 rounded border border-[#333] text-[#888] hover:text-white hover:border-[#555] transition-colors">
          照合する
        </button>
      </div>
    )
  }

  if (!data.has_prediction) {
    return (
      <div className="text-center text-[#555] py-16">
        <p className="text-sm mb-1">このレースはまだ予測していません</p>
        <p className="text-xs text-[#444]">「予測結果」タブで予測を実行すると自動的に記録されます</p>
      </div>
    )
  }

  const decided = data.has_result
  const top1 = data.predictions.find(p => p.predicted_rank === 1)

  return (
    <div className="max-w-2xl space-y-4">
      {/* サマリーバッジ */}
      <div className="flex items-center gap-3">
        {decided ? (
          data.top1_win ? (
            <span className="text-sm px-3 py-1 rounded bg-yellow-500/20 text-yellow-400 font-bold">
              ✓ 予測1位 WIN 的中
            </span>
          ) : data.top1_place3 ? (
            <span className="text-sm px-3 py-1 rounded bg-green-500/20 text-green-400 font-medium">
              ✓ 予測1位 複勝圏内
            </span>
          ) : (
            <span className="text-sm px-3 py-1 rounded bg-[#1e1e1e] text-[#666]">
              予測1位は圏外
            </span>
          )
        ) : (
          <span className="text-xs px-2 py-1 rounded bg-[#1a1a1a] text-[#555] border border-[#222]">
            結果待ち
          </span>
        )}
        <button onClick={onRefresh}
          className="ml-auto text-xs text-[#555] hover:text-[#888] transition-colors">
          更新
        </button>
      </div>

      {/* 予測テーブル */}
      <div className="bg-[#111] border border-[#1e1e1e] rounded-lg overflow-hidden">
        <div className="px-4 py-2.5 border-b border-[#1a1a1a] flex items-center justify-between">
          <span className="text-xs font-semibold text-[#888]">予測 vs 実績</span>
          {top1?.model_id && (
            <span className="text-[10px] text-[#444]">
              {top1.model_id.replace(/_ultimate$/, '').replace(/^model_/, '')}
            </span>
          )}
        </div>
        <table className="w-full text-xs">
          <thead>
            <tr className="border-b border-[#1a1a1a] text-[#444]">
              <th className="px-4 py-2 text-left font-normal w-8">予</th>
              <th className="px-4 py-2 text-left font-normal">馬名</th>
              <th className="px-4 py-2 text-right font-normal">勝率</th>
              <th className="px-4 py-2 text-right font-normal">予オッズ</th>
              <th className="px-4 py-2 text-right font-normal w-20">実際の着</th>
              <th className="px-4 py-2 text-right font-normal">タイム</th>
              <th className="px-4 py-2 text-right font-normal w-16"></th>
            </tr>
          </thead>
          <tbody>
            {data.predictions.map((p, idx) => {
              const isTop = p.predicted_rank <= 3
              const won = p.actual_finish === 1
              const placed = (p.actual_finish ?? 99) <= 3
              return (
                <tr key={`${p.horse_id ?? p.horse_number}-${idx}`}
                  className={`border-t border-[#111] ${isTop ? 'text-white' : 'text-[#666]'}`}>
                  <td className="px-4 py-2 text-[#444]">{p.predicted_rank}</td>
                  <td className="px-4 py-2">
                    <span className="text-[#555] mr-1">{p.horse_number}.</span>
                    <span className={isTop ? 'text-white' : ''}>{p.horse_name}</span>
                  </td>
                  <td className="px-4 py-2 text-right font-mono text-[#4a9eff]">
                    {p.win_probability != null ? `${(p.win_probability * 100).toFixed(1)}%` : '—'}
                  </td>
                  <td className="px-4 py-2 text-right text-[#888]">
                    {p.odds != null ? `${p.odds.toFixed(1)}倍` : '—'}
                  </td>
                  <td className={`px-4 py-2 text-right font-semibold ${
                    won ? 'text-yellow-400' : placed ? 'text-green-400' : p.actual_finish != null ? 'text-[#555]' : 'text-[#333]'
                  }`}>
                    {p.actual_finish != null ? `${p.actual_finish}着` : '—'}
                  </td>
                  <td className="px-4 py-2 text-right text-[#555] font-mono text-[10px]">
                    {p.finish_time ?? '—'}
                  </td>
                  <td className="px-4 py-2 text-right">
                    {p.predicted_rank === 1 && won && (
                      <span className="text-[10px] px-1.5 py-0.5 rounded bg-yellow-500/20 text-yellow-400">WIN</span>
                    )}
                    {p.predicted_rank === 1 && !won && placed && (
                      <span className="text-[10px] px-1.5 py-0.5 rounded bg-green-500/20 text-green-400">複勝</span>
                    )}
                    {p.predicted_rank <= 3 && (p.actual_finish ?? 99) <= 3 && p.predicted_rank !== 1 && (
                      <span className="text-[10px] px-1.5 py-0.5 rounded bg-blue-500/20 text-blue-400">圏内</span>
                    )}
                  </td>
                </tr>
              )
            })}
          </tbody>
        </table>
      </div>

      {!decided && (
        <p className="text-[11px] text-[#444] text-center">
          レース後に当日スクレイプ（6:00・9〜21時おき）で結果が反映されます
        </p>
      )}
    </div>
  )
}

