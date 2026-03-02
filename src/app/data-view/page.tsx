'use client'
import { useState } from 'react'
import { supabase } from '@/lib/supabase'
import Logo from '@/components/Logo'

type RawData = {
  race_id: string
  race_info_columns: string[]
  horse_columns: string[]
  race_info: Record<string, unknown>
  horses: Record<string, unknown>[]
}

type FeatureData = {
  race_id: string
  feature_count: number
  horse_count: number
  feature_columns: string[]
  records: Record<string, unknown>[]
}

function formatValue(v: unknown): string {
  if (v === null || v === undefined) return '—'
  if (typeof v === 'number') return isNaN(v) ? '—' : v.toLocaleString(undefined, { maximumFractionDigits: 4 })
  if (typeof v === 'boolean') return v ? '✓' : '✗'
  if (Array.isArray(v)) return JSON.stringify(v)
  if (typeof v === 'object') return JSON.stringify(v)
  return String(v)
}

function cellColor(v: unknown): string {
  if (v === null || v === undefined) return 'text-[#444]'
  if (typeof v === 'number') return 'text-[#7dd3fc]'
  if (typeof v === 'boolean') return v ? 'text-[#86efac]' : 'text-[#f87171]'
  return 'text-white'
}

export default function DataViewPage() {
  const [raceId, setRaceId] = useState('')
  const [tab, setTab] = useState<'raw' | 'features'>('raw')
  const [rawData, setRawData] = useState<RawData | null>(null)
  const [featData, setFeatData] = useState<FeatureData | null>(null)
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState('')

  const fetchData = async () => {
    if (!raceId.trim()) return
    setLoading(true)
    setError('')
    setRawData(null)
    setFeatData(null)

    try {
      const { data: { session } } = await supabase.auth.getSession()
      const authHeaders: HeadersInit = session?.access_token
        ? { Authorization: `Bearer ${session.access_token}` }
        : {}

      const [rawRes, featRes] = await Promise.all([
        fetch(`/api/debug/race/${raceId.trim()}`, { headers: authHeaders }),
        fetch(`/api/debug/race/${raceId.trim()}/features`, { headers: authHeaders }),
      ])

      if (!rawRes.ok) {
        const e = await rawRes.json()
        throw new Error(e.detail || `HTTP ${rawRes.status}`)
      }
      if (!featRes.ok) {
        const e = await featRes.json()
        throw new Error(e.detail || `HTTP ${featRes.status}`)
      }

      const [raw, feat] = await Promise.all([rawRes.json(), featRes.json()])
      setRawData(raw)
      setFeatData(feat)
    } catch (e: unknown) {
      setError(e instanceof Error ? e.message : 'エラーが発生しました')
    } finally {
      setLoading(false)
    }
  }

  return (
    <div className="min-h-screen bg-[#0a0a0a] text-white">
      <header className="border-b border-[#1e1e1e] px-6 py-4 flex items-center gap-4">
        <Logo href="/home" />
        <h1 className="text-lg font-bold text-[#888]">データ確認ビュー</h1>
      </header>

      <div className="px-6 py-4 max-w-[1600px] mx-auto">
        {/* Search row */}
        <div className="flex gap-3 mb-6">
          <input
            type="text"
            value={raceId}
            onChange={e => setRaceId(e.target.value)}
            onKeyDown={e => e.key === 'Enter' && fetchData()}
            placeholder="レースID (例: 202610011212)"
            className="flex-1 max-w-xs bg-[#111] border border-[#333] rounded px-3 py-2 text-sm text-white placeholder-[#555] focus:outline-none focus:border-[#4ade80]"
          />
          <button
            onClick={fetchData}
            disabled={loading || !raceId.trim()}
            className="bg-[#4ade80] text-black font-bold px-5 py-2 rounded text-sm disabled:opacity-40"
          >
            {loading ? '取得中…' : '取得'}
          </button>
        </div>

        {error && (
          <div className="mb-4 p-3 bg-[#1a0000] border border-[#f87171] rounded text-[#f87171] text-sm">{error}</div>
        )}

        {/* Tabs */}
        {(rawData || featData) && (
          <>
            <div className="flex gap-0 mb-4 border-b border-[#222]">
              {(['raw', 'features'] as const).map(t => (
                <button
                  key={t}
                  onClick={() => setTab(t)}
                  className={`px-5 py-2 text-sm font-semibold border-b-2 transition-colors ${
                    tab === t
                      ? 'border-[#4ade80] text-[#4ade80]'
                      : 'border-transparent text-[#666] hover:text-[#aaa]'
                  }`}
                >
                  {t === 'raw' ? `🗄️ 生データ (スクレイプ)` : `🔬 特徴量 (エンジニアリング後) ${featData ? `— ${featData.feature_count}列` : ''}`}
                </button>
              ))}
            </div>

            {/* ─── RAW TAB ─── */}
            {tab === 'raw' && rawData && (
              <div className="space-y-6">
                {/* race_info */}
                <section>
                  <h2 className="text-sm font-bold text-[#888] mb-2 uppercase tracking-widest">
                    race_info — {rawData.race_info_columns.length} カラム
                  </h2>
                  <div className="overflow-x-auto">
                    <table className="text-xs border-collapse w-auto">
                      <thead>
                        <tr>
                          {rawData.race_info_columns.map(col => (
                            <th key={col} className="px-3 py-1 bg-[#111] border border-[#222] text-[#888] font-mono whitespace-nowrap text-left">
                              {col}
                            </th>
                          ))}
                        </tr>
                      </thead>
                      <tbody>
                        <tr>
                          {rawData.race_info_columns.map(col => (
                            <td key={col} className={`px-3 py-1 border border-[#1a1a1a] font-mono whitespace-nowrap ${cellColor(rawData.race_info[col])}`}>
                              {formatValue(rawData.race_info[col])}
                            </td>
                          ))}
                        </tr>
                      </tbody>
                    </table>
                  </div>
                </section>

                {/* horses */}
                <section>
                  <h2 className="text-sm font-bold text-[#888] mb-2 uppercase tracking-widest">
                    horses — {rawData.horses.length} 頭 × {rawData.horse_columns.length} カラム
                  </h2>
                  <div className="overflow-x-auto max-h-[60vh] overflow-y-auto">
                    <table className="text-xs border-collapse w-auto">
                      <thead className="sticky top-0 z-10">
                        <tr>
                          {rawData.horse_columns.map(col => (
                            <th key={col} className="px-3 py-1 bg-[#111] border border-[#222] text-[#888] font-mono whitespace-nowrap text-left">
                              {col}
                            </th>
                          ))}
                        </tr>
                      </thead>
                      <tbody>
                        {rawData.horses.map((horse, i) => (
                          <tr key={i} className="hover:bg-[#111]">
                            {rawData.horse_columns.map(col => (
                              <td key={col} className={`px-3 py-1 border border-[#1a1a1a] font-mono whitespace-nowrap ${cellColor(horse[col])}`}>
                                {formatValue(horse[col])}
                              </td>
                            ))}
                          </tr>
                        ))}
                      </tbody>
                    </table>
                  </div>
                </section>
              </div>
            )}

            {/* ─── FEATURES TAB ─── */}
            {tab === 'features' && featData && (
              <section>
                <h2 className="text-sm font-bold text-[#888] mb-2 uppercase tracking-widest">
                  特徴量 — {featData.horse_count} 頭 × {featData.feature_count} 特徴量
                </h2>
                <div className="overflow-x-auto max-h-[72vh] overflow-y-auto">
                  <table className="text-xs border-collapse w-auto">
                    <thead className="sticky top-0 z-10">
                      <tr>
                        {featData.feature_columns.map(col => (
                          <th key={col} className="px-3 py-1 bg-[#111] border border-[#222] text-[#888] font-mono whitespace-nowrap text-left">
                            {col}
                          </th>
                        ))}
                      </tr>
                    </thead>
                    <tbody>
                      {featData.records.map((row, i) => (
                        <tr key={i} className="hover:bg-[#111]">
                          {featData.feature_columns.map(col => (
                            <td key={col} className={`px-3 py-1 border border-[#1a1a1a] font-mono whitespace-nowrap ${cellColor(row[col])}`}>
                              {formatValue(row[col])}
                            </td>
                          ))}
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
              </section>
            )}
          </>
        )}
      </div>
    </div>
  )
}
