'use client'
import { useState, useEffect, useCallback } from 'react'
import { Logo } from '@/components/Logo'
import Link from 'next/link'
import type { RaceItem } from '@/lib/types'
import { todayStr } from '@/lib/types'
import { useRaceCache } from '@/hooks/useRaceCache'
import { RacePredictionPanel } from '@/components/RacePredictionPanel'
import { RaceFeaturePanel } from '@/components/RaceFeaturePanel'
import type { RacePredictResult, FeatureData } from '@/lib/race-analysis-types'

export default function RaceAnalysisPage() {
  const [date, setDate] = useState(todayStr())
  const [races, setRaces] = useState<RaceItem[]>([])
  const [racesLoading, setRacesLoading] = useState(false)
  const [selectedRaceId, setSelectedRaceId] = useState('')
  const [tab, setTab] = useState<'predict' | 'features'>('predict')

  const [predictResult, setPredictResult] = useState<RacePredictResult | null>(null)
  const [featData, setFeatData] = useState<FeatureData | null>(null)
  const [dataLoading, setDataLoading] = useState(false)
  const [error, setError] = useState('')
  const [fromCache, setFromCache] = useState(false)
  const [cachedAt, setCachedAt] = useState<number | null>(null)

  const raceCache = useRaceCache()

  const loadRaces = useCallback(async () => {
    setRacesLoading(true)
    setRaces([])
    setSelectedRaceId('')
    setPredictResult(null)
    setFeatData(null)
    setFromCache(false)
    setCachedAt(null)
    setError('')
    try {
      const d = date.replace(/-/g, '')
      const res = await fetch(`/api/races/by-date?date=${d}`)
      if (res.ok) setRaces((await res.json()).races || [])
    } catch { }
    finally { setRacesLoading(false) }
  }, [date])

  useEffect(() => { loadRaces() }, [loadRaces])

  const loadRaceData = useCallback(async (raceId: string, forceRefresh = false) => {
    setSelectedRaceId(raceId)
    setError('')

    if (!forceRefresh) {
      const cached = raceCache.get(raceId)
      if (cached) {
        setPredictResult(cached.predictResult)
        setFeatData(cached.featData)
        setFromCache(true)
        setCachedAt(cached.cachedAt)
        if (!cached.featData) {
          fetch(`/api/debug/race/${raceId}/features`)
            .then(r => r.ok ? r.json() : null)
            .then(feat => { if (feat) { setFeatData(feat); raceCache.updateFeat(raceId, feat) } })
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
    try {
      const [predRes, featRes] = await Promise.all([
        fetch('/api/analyze-race', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ race_id: raceId, bankroll: 10000, risk_mode: 'balanced' }),
        }),
        fetch(`/api/debug/race/${raceId}/features`),
      ])
      if (!predRes.ok) {
        const e = await predRes.json()
        throw new Error(e.detail || `HTTP ${predRes.status}`)
      }
      const [pred, feat] = await Promise.all([predRes.json(), featRes.ok ? featRes.json() : null])
      const now = Date.now()
      raceCache.set(raceId, { predictResult: pred, featData: feat, cachedAt: now })
      setPredictResult(pred)
      setFeatData(feat)
      setFromCache(false)
      setCachedAt(now)
    } catch (e: unknown) {
      setError(e instanceof Error ? e.message : 'エラーが発生しました')
    } finally { setDataLoading(false) }
  }, [raceCache])

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
        {/* 左サイドバー: 日付ピッカー + レース一覧 */}
        <aside className="w-64 shrink-0 border-r border-[#1e1e1e] flex flex-col">
          <div className="p-4 border-b border-[#1e1e1e]">
            <label className="text-xs text-[#666] block mb-2">日付</label>
            <input
              type="date"
              value={date}
              onChange={e => setDate(e.target.value)}
              className="w-full px-3 py-2 bg-[#111] border border-[#1e1e1e] rounded text-white text-sm focus:outline-none focus:border-[#333]"
            />
          </div>
          <div className="flex-1 overflow-y-auto">
            {racesLoading && (
              <div className="p-4 flex items-center gap-2 text-xs text-[#555]">
                <div className="w-3 h-3 border border-[#555] border-t-transparent rounded-full animate-spin" />
                取得中...
              </div>
            )}
            {!racesLoading && races.length === 0 && (
              <div className="p-4 space-y-1">
                <p className="text-xs text-[#555]">レースが見つかりません</p>
                <p className="text-[10px] text-[#444]">日付を変更するか、データ取得ページで先にデータを取得してください</p>
              </div>
            )}
            {races.map(r => (
              <button
                key={r.race_id}
                onClick={() => loadRaceData(r.race_id)}
                disabled={dataLoading}
                className={`w-full text-left px-4 py-3 border-b border-[#111] transition-colors ${
                  selectedRaceId === r.race_id
                    ? 'bg-[#1a1a1a] border-l-2 border-l-white'
                    : 'hover:bg-[#111] border-l-2 border-l-transparent'
                }`}
              >
                <div className="flex items-center justify-between mb-0.5">
                  <span className="text-xs text-[#888]">{r.venue}</span>
                  <span className="text-xs font-bold">{r.race_no}R</span>
                </div>
                <div className="text-xs text-white truncate">{r.race_name || `${r.race_no}レース`}</div>
                <div className="text-[10px] text-[#555] mt-0.5">
                  {r.track_type}{r.distance ? ` ${r.distance}m` : ''} · {r.num_horses}頭
                </div>
              </button>
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
          {dataLoading && (
            <div className="flex-1 flex flex-col items-center justify-center gap-3 text-[#555]">
              <div className="w-6 h-6 border-2 border-[#555] border-t-white rounded-full animate-spin" />
              <span className="text-sm">予測計算中...</span>
            </div>
          )}
          {error && (
            <div className="m-4 p-3 bg-[#1a0000] border border-[#f87171] rounded text-[#f87171] text-sm">{error}</div>
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
                ] as const).map(t => (
                  <button
                    key={t.key}
                    onClick={() => setTab(t.key)}
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
            </>
          )}
        </main>
      </div>
    </div>
  )
}

