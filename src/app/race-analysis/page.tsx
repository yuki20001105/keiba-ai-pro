'use client'
import { useState, useEffect, useCallback } from 'react'
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
  const [models, setModels] = useState<{ model_id: string; target: string; cv_auc_mean: number }[]>([])
  const [tab, setTab] = useState<'predict' | 'features' | 'result'>('predict')

  // 結果照合タブ
  const [resultData, setResultData] = useState<PredictionHistoryResult | null>(null)
  const [resultLoading, setResultLoading] = useState(false)

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
          // no_odds モデルは除外し、model_id 降順（最新が先頭）に並べる
          const filtered = data.models
            .filter((m: any) => !m.model_id.includes('no_odds'))
            .sort((a: any, b: any) => b.model_id.localeCompare(a.model_id))
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
        {/* 左サイドバー: 日付ピッカー + レース一覧 */}
        <aside className="w-64 shrink-0 border-r border-[#1e1e1e] flex flex-col">
          <div className="p-4 border-b border-[#1e1e1e] space-y-3">
            <div>
              <label className="text-xs text-[#666] block mb-2">日付</label>
              <input
                type="date"
                value={date}
                onChange={e => setDate(e.target.value)}
                className="w-full px-3 py-2 bg-[#111] border border-[#1e1e1e] rounded text-white text-sm focus:outline-none focus:border-[#333]"
              />
            </div>
            <div>
              <label className="text-xs text-[#666] block mb-1.5">モデル</label>
              <select
                value={selectedModelId}
                onChange={e => {
                  setSelectedModelId(e.target.value)
                  if (selectedRaceId) loadRaceData(selectedRaceId, true, e.target.value)
                }}
                className="w-full px-2 py-1.5 bg-[#111] border border-[#1e1e1e] rounded text-white text-xs focus:outline-none focus:border-[#333] truncate"
              >
                <option value="">
                  最新モデル（自動）{models.length > 0 ? ` — ${models[0].model_id.replace(/_ultimate$/, '').replace(/^model_/, '')}` : ''}
                </option>
                {models.map(m => (
                  <option key={m.model_id} value={m.model_id}>
                    {m.model_id.replace(/_ultimate$/, '').replace(/^model_/, '')}
                  </option>
                ))}
              </select>
            </div>
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

