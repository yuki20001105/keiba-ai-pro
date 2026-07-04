'use client'

import { useState, useCallback } from 'react'
import Link from 'next/link'
import { Logo } from '@/components/Logo'
import { authFetch } from '@/lib/auth-fetch'
import { todayStr, toInputDate, fromInputDate } from '@/lib/types'

// ─── 型定義 ──────────────────────────────────────────────────────────────────

type Win5Race = {
  race_id: string
  race_name: string
  venue: string
  race_no: number
  distance: number
  track_type: string
  post_time: string
  num_horses: number
  in_db: boolean
}

type HorsePick = {
  horse_no: number
  horse_name: string
  win_probability: number
  odds: number | null
  selected: boolean
}

type RaceResult = {
  status: 'idle' | 'loading' | 'done' | 'error'
  horses: HorsePick[]
  error?: string
}

// ─── ユーティリティ ───────────────────────────────────────────────────────────

/** API エラーを常に文字列に変換する */
function toErrStr(detail: unknown): string {
  if (typeof detail === 'string') return detail
  if (Array.isArray(detail)) return detail.map((e: any) => e?.msg ?? JSON.stringify(e)).join(' / ')
  if (detail && typeof detail === 'object') return (detail as any).msg ?? JSON.stringify(detail)
  return String(detail ?? 'Unknown error')
}

/** WIN5 組み合わせ数を計算（各レースの選択頭数の積） */
function calcCombinations(pickCounts: number[]): number {
  if (pickCounts.some(c => c === 0)) return 0
  return pickCounts.reduce((acc, c) => acc * c, 1)
}

/** 選択馬の win_probability 積（全レース正解の理論確率） */
function calcWinProb(races: Win5Race[], results: Record<string, RaceResult>, selections: Record<string, Set<number>>): number {
  let prob = 1
  for (const race of races) {
    const r = results[race.race_id]
    if (!r || r.status !== 'done') return 0
    const sel = selections[race.race_id] ?? new Set()
    if (sel.size === 0) return 0
    const selProb = r.horses.filter(h => sel.has(h.horse_no)).reduce((s, h) => s + h.win_probability, 0)
    prob *= selProb
  }
  return prob
}

// ─── メインコンポーネント ─────────────────────────────────────────────────────

export default function Win5Page() {
  const [date, setDate] = useState(todayStr())
  const [loading, setLoading] = useState(false)
  const [races, setRaces] = useState<Win5Race[]>([])
  const [message, setMessage] = useState('')
  const [results, setResults] = useState<Record<string, RaceResult>>({})
  const [selections, setSelections] = useState<Record<string, Set<number>>>({})
  const [modelId, setModelId] = useState('')
  const [models, setModels] = useState<any[]>([])
  const [modelsLoaded, setModelsLoaded] = useState(false)

  // ─── モデル一覧取得（初回のみ） ───────────────────────────────────────────
  const loadModels = useCallback(async () => {
    if (modelsLoaded) return
    try {
      const res = await authFetch('/api/models?ultimate=true')
      if (res.ok) {
        const data = await res.json()
        const sorted = (data.models || [])
          .filter((m: any) => m.target === 'win' || (m.target && String(m.target).startsWith('speed')))
          .sort((a: any, b: any) => {
            const ta = a.model_id.match(/_(\d{8})_(\d{4})$/)?.[0] ?? ''
            const tb = b.model_id.match(/_(\d{8})_(\d{4})$/)?.[0] ?? ''
            return tb.localeCompare(ta)
          })
        setModels(sorted)
        setModelsLoaded(true)
      }
    } catch {}
  }, [modelsLoaded])

  // ─── WIN5 レース取得 ─────────────────────────────────────────────────────
  const fetchWin5Races = async () => {
    setLoading(true)
    setRaces([])
    setResults({})
    setSelections({})
    setMessage('')
    await loadModels()
    try {
      const res = await authFetch(`/api/win5/races?date=${date}`)
      const data = await res.json()
      if (!res.ok) {
        setMessage(toErrStr(data.detail) || `HTTP ${res.status}`)
        return
      }
      if (!data.races || data.races.length === 0) {
        setMessage(data.message || `${date} の WIN5 レースが見つかりませんでした`)
        return
      }
      setRaces(data.races)
      // 初期選択状態（全レース空）
      const initSel: Record<string, Set<number>> = {}
      data.races.forEach((r: Win5Race) => { initSel[r.race_id] = new Set() })
      setSelections(initSel)
    } catch (e: any) {
      setMessage(e.message)
    } finally {
      setLoading(false)
    }
  }

  // ─── 全レース一括予測 ────────────────────────────────────────────────────
  const predictAll = async () => {
    for (const race of races) {
      await predictRace(race.race_id)
    }
  }

  // ─── 単一レース予測 ──────────────────────────────────────────────────────
  const predictRace = async (race_id: string) => {
    setResults(prev => ({ ...prev, [race_id]: { status: 'loading', horses: [] } }))
    try {
      const res = await authFetch('/api/analyze-race', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        signal: AbortSignal.timeout(180_000),
        body: JSON.stringify({ race_id, model_id: modelId || null, bankroll: 10000, risk_mode: 'balanced' }),
      })
      const data = await res.json()
      if (!res.ok) {
        setResults(prev => ({ ...prev, [race_id]: { status: 'error', horses: [], error: toErrStr(data.detail) || `HTTP ${res.status}` } }))
        return
      }
      const horses: HorsePick[] = (data.predictions || [])
        .sort((a: any, b: any) => b.win_probability - a.win_probability)
        .slice(0, 8)
        .map((h: any) => ({
          horse_no: h.horse_no,
          horse_name: h.horse_name || `${h.horse_no}番`,
          win_probability: h.win_probability ?? 0,
          odds: h.odds ?? null,
          selected: false,
        }))
      setResults(prev => ({ ...prev, [race_id]: { status: 'done', horses } }))
      // デフォルト: 上位1頭を自動選択
      if (horses.length > 0) {
        setSelections(prev => ({ ...prev, [race_id]: new Set([horses[0].horse_no]) }))
      }
    } catch (e: any) {
      setResults(prev => ({ ...prev, [race_id]: { status: 'error', horses: [], error: e.message } }))
    }
  }

  // ─── 選択トグル ──────────────────────────────────────────────────────────
  const toggleHorse = (race_id: string, horse_no: number) => {
    setSelections(prev => {
      const s = new Set(prev[race_id] ?? [])
      if (s.has(horse_no)) s.delete(horse_no)
      else s.add(horse_no)
      return { ...prev, [race_id]: s }
    })
  }

  // ─── 組み合わせ計算 ─────────────────────────────────────────────────────
  const pickCounts = races.map(r => (selections[r.race_id]?.size ?? 0))
  const totalCombos = calcCombinations(pickCounts)
  const totalCost = totalCombos * 100
  const winProb = calcWinProb(races, results, selections)
  const allDone = races.length === 5 && races.every(r => results[r.race_id]?.status === 'done')

  // ─── モデルラベル ────────────────────────────────────────────────────────
  function modelLabel(m: any): string {
    const match = m.model_id.match(/_(\d{8})_(\d{4})$/)
    if (!match) return m.target ?? m.model_id
    const [, d, t] = match
    const ds = `${d.slice(0,4)}-${d.slice(4,6)}-${d.slice(6,8)} ${t.slice(0,2)}:${t.slice(2,4)}`
    return m.auc != null ? `${m.target} · ${ds} (AUC: ${m.auc.toFixed(3)})` : `${m.target} · ${ds}`
  }

  return (
    <div className="min-h-screen bg-[#050505] text-white">
      {/* ヘッダー */}
      <header className="border-b border-[#111] px-6 py-4 flex items-center justify-between">
        <div className="flex items-center gap-4">
          <Logo />
          <span className="text-[#333]">/</span>
          <span className="text-sm font-medium text-white">WIN5 予測</span>
        </div>
        <Link href="/predict-batch" className="text-xs text-[#444] hover:text-[#666] transition-colors">
          ← バッチ予測に戻る
        </Link>
      </header>

      <main className="max-w-5xl mx-auto px-6 py-8 space-y-6">

        {/* タイトル */}
        <div>
          <h1 className="text-xl font-semibold">WIN5 予測</h1>
        </div>

        {/* 設定パネル */}
        <div className="border border-[#1e1e1e] rounded-xl p-5 space-y-4">
          <div className="flex flex-wrap gap-4 items-end">
            {/* 日付 */}
            <div>
              <label className="text-xs text-[#666] block mb-1.5">日付</label>
              <input
                type="date"
                value={toInputDate(date)}
                onChange={e => setDate(fromInputDate(e.target.value))}
                className="px-3 py-2 bg-[#0a0a0a] border border-[#1e1e1e] rounded-lg text-white text-sm focus:outline-none focus:border-[#333]"
              />
            </div>
            {/* モデル */}
            <div className="flex-1 min-w-48">
              <label className="text-xs text-[#666] block mb-1.5">モデル</label>
              <select
                value={modelId}
                onChange={e => setModelId(e.target.value)}
                onFocus={loadModels}
                className="w-full px-3 py-2 bg-[#0a0a0a] border border-[#1e1e1e] rounded-lg text-white text-sm focus:outline-none focus:border-[#333]"
              >
                <option value="">最新モデルを自動選択</option>
                {models.map((m, i) => (
                  <option key={i} value={m.model_id}>{modelLabel(m)}</option>
                ))}
              </select>
            </div>
            {/* 取得ボタン */}
            <button
              onClick={fetchWin5Races}
              disabled={loading}
              className="px-5 py-2 bg-[#1a1a2e] border border-[#333] rounded-lg text-sm font-medium hover:border-[#555] disabled:opacity-50 transition-colors"
            >
              {loading ? '取得中...' : 'WIN5 レースを取得'}
            </button>
          </div>
        </div>

        {/* エラー / メッセージ */}
        {message && (
          <div className="border border-[#2a1a1a] bg-[#1a0e0e] rounded-lg px-4 py-3 text-sm text-[#f87171]">
            {message}
          </div>
        )}

        {/* WIN5 レース一覧 */}
        {races.length > 0 && (
          <>
            <div className="flex items-center justify-between">
              <h2 className="text-sm font-medium text-[#888]">
                対象レース（{races.length} / 5）
              </h2>
              <button
                onClick={predictAll}
                disabled={races.some(r => results[r.race_id]?.status === 'loading')}
                className="px-4 py-1.5 bg-[#0f3460] border border-[#1a4a8a] rounded-lg text-xs font-medium hover:border-[#2a6acc] disabled:opacity-50 transition-colors"
              >
                全レース一括予測
              </button>
            </div>

            <div className="space-y-4">
              {races.map((race, idx) => {
                const result = results[race.race_id]
                const sel = selections[race.race_id] ?? new Set()
                return (
                  <div key={race.race_id} className="border border-[#1e1e1e] rounded-xl overflow-hidden">
                    {/* レースヘッダー */}
                    <div className="flex items-center justify-between px-5 py-3 bg-[#0d0d0d]">
                      <div className="flex items-center gap-3">
                        <span className="w-7 h-7 rounded-full bg-[#1a1a2e] border border-[#333] flex items-center justify-center text-xs font-bold text-[#7dd3fc]">
                          {idx + 1}
                        </span>
                        <div>
                          <span className="text-sm font-medium">
                            {race.venue || '—'} {race.race_no}R
                            {race.race_name && <span className="ml-2 text-[#888]">（{race.race_name}）</span>}
                          </span>
                          <div className="text-xs text-[#555] mt-0.5">
                            {race.distance > 0 && `${race.distance}m`}
                            {race.track_type && ` ${race.track_type}`}
                            {race.post_time && ` 発走 ${race.post_time}`}
                            {race.num_horses > 0 && ` · ${race.num_horses}頭`}
                            {!race.in_db && <span className="ml-2 text-amber-500">⚠ DB未登録</span>}
                          </div>
                        </div>
                      </div>
                      <button
                        onClick={() => predictRace(race.race_id)}
                        disabled={result?.status === 'loading'}
                        className="px-3 py-1.5 text-xs border border-[#333] rounded-lg hover:border-[#555] disabled:opacity-50 transition-colors"
                      >
                        {result?.status === 'loading' ? '予測中...' : result?.status === 'done' ? '再予測' : '予測'}
                      </button>
                    </div>

                    {/* 予測結果 */}
                    {result?.status === 'error' && (
                      <div className="px-5 py-3 text-xs text-[#f87171] bg-[#1a0e0e]">
                        エラー: {result.error}
                      </div>
                    )}

                    {result?.status === 'loading' && (
                      <div className="px-5 py-4 text-sm text-[#555] animate-pulse">予測中...</div>
                    )}

                    {result?.status === 'done' && result.horses.length > 0 && (
                      <div className="px-5 py-3">

                        <div className="grid grid-cols-2 sm:grid-cols-4 gap-2">
                          {result.horses.map(h => {
                            const isSel = sel.has(h.horse_no)
                            return (
                              <button
                                key={h.horse_no}
                                onClick={() => toggleHorse(race.race_id, h.horse_no)}
                                className={`flex items-center gap-2 px-3 py-2 rounded-lg border text-left transition-colors ${
                                  isSel
                                    ? 'border-[#2a6acc] bg-[#0f2040] text-white'
                                    : 'border-[#1e1e1e] bg-[#0a0a0a] text-[#888] hover:border-[#333]'
                                }`}
                              >
                                <span className={`w-6 h-6 rounded-full flex items-center justify-center text-xs font-bold shrink-0 ${
                                  isSel ? 'bg-[#2a6acc] text-white' : 'bg-[#1e1e1e] text-[#666]'
                                }`}>
                                  {h.horse_no}
                                </span>
                                <div className="min-w-0">
                                  <div className="text-xs font-medium truncate">{h.horse_name}</div>
                                  <div className="text-[10px] text-[#555]">
                                    {(h.win_probability * 100).toFixed(1)}%
                                    {h.odds != null && ` · ${h.odds.toFixed(1)}倍`}
                                  </div>
                                </div>
                              </button>
                            )
                          })}
                        </div>
                        <div className="mt-2 text-xs text-[#444]">
                          選択: {sel.size}頭
                          {sel.size > 0 && (() => {
                            const selHorses = result.horses.filter(h => sel.has(h.horse_no))
                            const p = selHorses.reduce((s, h) => s + h.win_probability, 0)
                            return ` · このレースの的中確率: ${(p * 100).toFixed(1)}%`
                          })()}
                        </div>
                      </div>
                    )}
                  </div>
                )
              })}
            </div>

            {/* 組み合わせサマリー */}
            {allDone && (
              <div className="border border-[#1e3a1e] bg-[#0a140a] rounded-xl p-5">
                <h3 className="text-sm font-semibold text-[#86efac] mb-4">組み合わせサマリー</h3>
                <div className="grid grid-cols-2 sm:grid-cols-4 gap-4 mb-4">
                  <div className="text-center">
                    <div className="text-2xl font-bold text-white">{totalCombos.toLocaleString()}</div>
                    <div className="text-xs text-[#555] mt-0.5">組み合わせ数</div>
                  </div>
                  <div className="text-center">
                    <div className="text-2xl font-bold text-white">¥{totalCost.toLocaleString()}</div>
                    <div className="text-xs text-[#555] mt-0.5">購入金額（@¥100）</div>
                  </div>
                  <div className="text-center">
                    <div className="text-2xl font-bold text-[#fcd34d]">
                      {winProb > 0 ? (winProb * 100).toFixed(3) : '—'}%
                    </div>
                    <div className="text-xs text-[#555] mt-0.5">的中確率</div>
                  </div>
                  <div className="text-center">
                    <div className="text-2xl font-bold text-[#7dd3fc]">
                      {winProb > 0 ? Math.round(1 / winProb).toLocaleString() : '—'}
                    </div>
                    <div className="text-xs text-[#555] mt-0.5">期待オッズ</div>
                  </div>
                </div>
                {/* レース別選択サマリー */}
                <div className="space-y-1">
                  {races.map((race, idx) => {
                    const r = results[race.race_id]
                    const sel = selections[race.race_id] ?? new Set()
                    const selHorses = r?.horses?.filter(h => sel.has(h.horse_no)) ?? []
                    return (
                      <div key={race.race_id} className="flex items-center gap-2 text-xs text-[#666]">
                        <span className="text-[#7dd3fc] font-medium w-4">{idx + 1}</span>
                        <span className="text-[#888]">{race.venue} {race.race_no}R</span>
                        <span className="text-[#444]">→</span>
                        {selHorses.length > 0 ? (
                          selHorses.map(h => (
                            <span key={h.horse_no} className="px-1.5 py-0.5 bg-[#0f2040] border border-[#2a6acc] rounded text-[#7dd3fc]">
                              {h.horse_no}番 {h.horse_name}
                            </span>
                          ))
                        ) : (
                          <span className="text-amber-500">未選択</span>
                        )}
                      </div>
                    )
                  })}
                </div>
                {totalCombos === 0 && (
                  <p className="text-xs text-amber-400 mt-3">全レースで 1 頭以上選択してください。</p>
                )}
              </div>
            )}
          </>
        )}
      </main>
    </div>
  )
}
