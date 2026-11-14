'use client'
import { useState, useEffect } from 'react'
import { Logo } from '@/components/Logo'
import Link from 'next/link'
import { authFetch } from '@/lib/auth-fetch'
import { useAuth } from '@/contexts/AuthContext'
import { PremiumRequiredNotice } from '@/components/PremiumRequiredNotice'

type RaceItem = { race_id: string; race_name: string; venue: string; race_no: number; distance: number; track_type: string; num_horses: number }
type RawData = { race_id: string; race_info_columns: string[]; horse_columns: string[]; race_info: Record<string, unknown>; horses: Record<string, unknown>[] }
type FeatureData = { race_id: string; feature_count: number; horse_count: number; feature_columns: string[]; records: Record<string, unknown>[] }

// 馬テーブルで先頭に固定する優先列
const PRIORITY_HORSE_COLS = ['horse_number', 'horse_no', 'bracket_number', 'horse_name', 'jockey_name', 'trainer_name', 'age', 'sex', 'sex_age', 'weight_kg', 'jockey_weight', 'burden_weight', 'horse_weight', 'weight_diff', 'weight_change', 'odds', 'popularity', 'finish_order', 'finish_position', 'last_3f_time', 'last_3f', 'finish_time']
const CORE_HORSE_COLS = new Set(PRIORITY_HORSE_COLS)
const HORSE_COLUMN_LABELS: Record<string, string> = {
  horse_number: '馬番', horse_no: '馬番', bracket_number: '枠番', horse_name: '馬名',
  jockey_name: '騎手', trainer_name: '調教師', age: '年齢', sex: '性別', sex_age: '性齢',
  weight_kg: '斤量', jockey_weight: '斤量', burden_weight: '斤量', horse_weight: '馬体重',
  weight_diff: '馬体重増減', weight_change: '馬体重増減', horse_weight_change: '馬体重増減', odds: 'オッズ',
  popularity: '人気', finish_order: '着順', finish_position: '着順', finish: '着順', last_3f_time: '上がり3F', last_3f: '上がり3F',
  finish_time: '走破タイム', time_seconds: '走破タイム', distance: '距離', surface: 'コース', track_type: 'コース', venue: '競馬場',
  field_condition: '馬場状態', weather: '天候', race_class: 'クラス', race_id: 'レースID',
  horse_id: '馬ID', jockey_id: '騎手ID', trainer_id: '調教師ID', race_date: '開催日',
}

function todayStr() {
  const d = new Date()
  return `${d.getFullYear()}-${String(d.getMonth()+1).padStart(2,'0')}-${String(d.getDate()).padStart(2,'0')}`
}

function formatValue(v: unknown): string {
  if (v === null || v === undefined) return '—'
  if (typeof v === 'number') return isNaN(v) ? '—' : v.toLocaleString(undefined, { maximumFractionDigits: 3 })
  if (typeof v === 'boolean') return v ? '✓' : '✗'
  if (Array.isArray(v)) return JSON.stringify(v)
  if (typeof v === 'object') return JSON.stringify(v)
  return String(v)
}

function cellColor(v: unknown): string {
  if (v === null || v === undefined) return 'text-[#333]'
  if (typeof v === 'number') return 'text-[#7dd3fc]'
  if (typeof v === 'boolean') return v ? 'text-[#86efac]' : 'text-[#f87171]'
  return 'text-white'
}

function sortedCols(cols: string[]): string[] {
  const priority = PRIORITY_HORSE_COLS.filter(c => cols.includes(c))
  const rest = cols.filter(c => !PRIORITY_HORSE_COLS.includes(c))
  return [...priority, ...rest]
}

export default function DataViewPage() {
  const { isPremium, loading: authLoading } = useAuth()
  const [date, setDate] = useState(todayStr())
  const [races, setRaces] = useState<RaceItem[]>([])
  const [racesLoading, setRacesLoading] = useState(false)
  const [selectedRaceId, setSelectedRaceId] = useState('')
  const [tab, setTab] = useState<'raw' | 'features'>('raw')
  const [rawData, setRawData] = useState<RawData | null>(null)
  const [featData, setFeatData] = useState<FeatureData | null>(null)
  const [dataLoading, setDataLoading] = useState(false)
  const [error, setError] = useState('')
  const [colFilter, setColFilter] = useState('')
  const [rawColFilter, setRawColFilter] = useState('')
  const [showAllRawCols, setShowAllRawCols] = useState(true)
  const [hiddenGroups, setHiddenGroups] = useState<Set<string>>(new Set())

  // 日付変更時にレース一覧を自動取得
  useEffect(() => { loadRaces() }, [date])

  const loadRaces = async () => {
    setRacesLoading(true)
    setRaces([])
    setSelectedRaceId('')
    setRawData(null)
    setFeatData(null)
    setError('')
    try {
      const d = date.replace(/-/g, '')
      const res = await authFetch(`/api/races/by-date?date=${d}`)
      if (res.ok) {
        const data = await res.json()
        setRaces(data.races || [])
      }
    } catch {}
    finally { setRacesLoading(false) }
  }

  const loadRaceData = async (raceId: string) => {
    if (!isPremium) {
      setError('この機能は Premium または Admin のみ利用できます。')
      return
    }

    setSelectedRaceId(raceId)
    setDataLoading(true)
    setError('')
    setRawData(null)
    setFeatData(null)
    try {
      const [rawRes, featRes] = await Promise.all([
        authFetch(`/api/debug/race/${raceId}`),
        authFetch(`/api/debug/race/${raceId}/features`),
      ])
      if (!rawRes.ok) {
        const e = await rawRes.json().catch(() => ({}))
        const authMsg = rawRes.status === 401 || rawRes.status === 403
          ? '権限不足: Premium または Admin が必要です。'
          : null
        throw new Error(authMsg || e.detail || `HTTP ${rawRes.status}`)
      }
      const [raw, feat] = await Promise.all([rawRes.json(), featRes.ok ? featRes.json() : null])
      setRawData(raw)
      setFeatData(feat)
    } catch (e: unknown) {
      setError(e instanceof Error ? e.message : 'エラーが発生しました')
    } finally { setDataLoading(false) }
  }

  const raceInfo = rawData?.race_info
  const trackIcon = (t: string) => t?.includes('ダ') || t?.toLowerCase() === 'dirt' ? '🟤' : '🟢'
  const visibleRawCols = rawData
    ? sortedCols(rawData.horse_columns).filter(col => {
        if (!showAllRawCols && !CORE_HORSE_COLS.has(col)) return false
        const query = rawColFilter.trim().toLowerCase()
        return !query || col.toLowerCase().includes(query) || (HORSE_COLUMN_LABELS[col] ?? '').includes(query)
      })
    : []

  return (
    <div className="min-h-screen bg-[#0a0a0a] text-white flex flex-col">
      <header className="border-b border-[#1e1e1e] px-6 py-4 flex items-center justify-between shrink-0">
        <div className="flex items-center gap-4">
          <Logo href="/home" />
          <span className="text-sm text-[#888]">データ確認ビュー</span>
          <span className="text-[10px] px-2 py-0.5 rounded border border-yellow-600/40 bg-yellow-500/20 text-yellow-400">
            Premium 専用
          </span>
          <Link href="/data-view/all" className="rounded border border-[#303030] px-3 py-1.5 text-xs text-[#aaa] hover:text-white">
            保存済み全データ・CSV
          </Link>
        </div>
        <Link href="/home" className="flex items-center gap-1 text-xs text-[#555] hover:text-white transition-colors">
          <svg className="w-3 h-3" fill="none" viewBox="0 0 24 24" stroke="currentColor">
            <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M15 19l-7-7 7-7" />
          </svg>
          ホーム
        </Link>
      </header>

      <div className="flex flex-1 overflow-hidden">
        {/* ── 左パネル: 日付 + レース一覧 ── */}
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
              <div className="p-4 text-xs text-[#555]">レースが見つかりません</div>
            )}
            {races.map(r => (
              <button
                key={r.race_id}
                onClick={() => loadRaceData(r.race_id)}
                    disabled={!isPremium}
                className={`w-full text-left px-4 py-3 border-b border-[#111] transition-colors ${
                  selectedRaceId === r.race_id
                    ? 'bg-[#1a1a1a] border-l-2 border-l-white'
                    : 'hover:bg-[#111] border-l-2 border-l-transparent'
                    } disabled:opacity-50 disabled:cursor-not-allowed`}
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

        {/* ── 右パネル: データ表示 ── */}
        <main className="flex-1 overflow-hidden flex flex-col">
          {!authLoading && !isPremium && !selectedRaceId && (
            <div className="p-4">
              <PremiumRequiredNotice
                title="デバッグデータ表示は Premium 専用です"
                message="レース一覧の閲覧は可能ですが、レース詳細（生データ/特徴量）は Premium または Admin 権限が必要です。"
              />
            </div>
          )}

          {!selectedRaceId && !dataLoading && (
            <div className="flex-1 flex items-center justify-center text-[#333] text-sm">
              ← 左のレースを選択してください
            </div>
          )}

          {dataLoading && (
            <div className="flex-1 flex items-center justify-center gap-2 text-[#555] text-sm">
              <div className="w-4 h-4 border-2 border-[#555] border-t-transparent rounded-full animate-spin" />
              データ取得中...
            </div>
          )}

          {error && (
            <div className="m-4 p-3 bg-[#1a0000] border border-[#f87171] rounded text-[#f87171] text-sm">{error}</div>
          )}

          {rawData && !dataLoading && (
            <>
              {/* レース情報カード */}
              <div className="px-6 pt-5 pb-4 border-b border-[#1e1e1e] shrink-0">
                <div className="flex items-start justify-between gap-4">
                  <div>
                    <div className="flex items-center gap-2 mb-1">
                      <span className="text-xs text-[#666]">{formatValue(raceInfo?.venue)}</span>
                      <span className="text-xs text-[#666]">·</span>
                      <span className="text-xs text-[#666]">{formatValue(raceInfo?.date)}</span>
                    </div>
                    <h2 className="text-lg font-bold leading-tight">
                      {formatValue(raceInfo?.race_name) !== '—' ? formatValue(raceInfo?.race_name) : `${formatValue(raceInfo?.race_no)}レース`}
                    </h2>
                  </div>
                  <div className="flex gap-3 shrink-0">
                    {[
                      { label: 'コース', value: `${trackIcon(String(raceInfo?.track_type ?? ''))} ${formatValue(raceInfo?.track_type)}` },
                      { label: '距離', value: raceInfo?.distance ? `${raceInfo.distance}m` : '—' },
                      { label: '頭数', value: rawData.horses.length > 0 ? `${rawData.horses.length}頭` : '—' },
                    ].map(s => (
                      <div key={s.label} className="bg-[#111] border border-[#1e1e1e] rounded px-3 py-2 text-center min-w-[60px]">
                        <div className="text-[10px] text-[#555] mb-0.5">{s.label}</div>
                        <div className="text-sm font-medium">{s.value}</div>
                      </div>
                    ))}
                  </div>
                </div>
                <div className="text-[10px] text-[#555] mt-2 font-mono">ID: {rawData.race_id}</div>
              </div>

              {/* タブ */}
              <div className="flex gap-0 border-b border-[#1e1e1e] shrink-0 px-6">
                {(['raw', 'features'] as const).map(t => (
                  <button
                    key={t}
                    onClick={() => setTab(t)}
                    className={`px-4 py-2.5 text-xs font-semibold border-b-2 transition-colors ${
                      tab === t ? 'border-white text-white' : 'border-transparent text-[#555] hover:text-[#888]'
                    }`}
                  >
                    {t === 'raw'
                      ? `生データ（${rawData.horses.length}頭 × ${rawData.horse_columns.length}列）`
                      : `特徴量${featData ? `（${featData.feature_count}列）` : ''}`}
                  </button>
                ))}
                {tab === 'features' && (
                  <div className="ml-auto flex items-center pr-0 py-1.5">
                    <input
                      type="text"
                      placeholder="列名で絞り込み..."
                      value={colFilter}
                      onChange={e => setColFilter(e.target.value)}
                      className="px-3 py-1 bg-[#111] border border-[#1e1e1e] rounded text-xs text-white placeholder-[#444] focus:outline-none focus:border-[#333] w-40"
                    />
                  </div>
                )}
                {tab === 'raw' && (
                  <div className="ml-auto flex items-center gap-2 py-1.5">
                    <div className="flex rounded border border-[#292929] p-0.5">
                      <button
                        type="button"
                        onClick={() => setShowAllRawCols(false)}
                        className={`rounded px-2 py-1 text-[11px] ${!showAllRawCols ? 'bg-[#292929] text-white' : 'text-[#777] hover:text-white'}`}
                      >主な項目</button>
                      <button
                        type="button"
                        onClick={() => setShowAllRawCols(true)}
                        className={`rounded px-2 py-1 text-[11px] ${showAllRawCols ? 'bg-[#292929] text-white' : 'text-[#777] hover:text-white'}`}
                      >全項目</button>
                    </div>
                    <input
                      type="text"
                      placeholder="項目名で検索..."
                      aria-label="生データの項目名で検索"
                      value={rawColFilter}
                      onChange={e => setRawColFilter(e.target.value)}
                      className="w-36 rounded border border-[#292929] bg-[#111] px-2.5 py-1.5 text-xs text-white placeholder-[#555] focus:outline-none focus:border-[#555]"
                    />
                  </div>
                )}
              </div>

              {/* ── 生データタブ ── */}
              {tab === 'raw' && (
                <div className="flex-1 overflow-auto p-4">
                  <div className="mb-2 flex items-center justify-between text-[11px] text-[#777]">
                    <span>レース情報（{rawData.race_info_columns.length}項目）</span>
                    <span>取得した項目と値をすべて表示</span>
                  </div>
                  <div className="mb-6 overflow-x-auto">
                    <table className="w-full text-xs border-collapse">
                      <thead className="sticky top-0 z-10">
                        <tr>
                          <th className="w-40 px-3 py-2 bg-[#0f0f0f] border border-[#222] text-left text-[#aaa]">項目</th>
                          <th className="w-56 px-3 py-2 bg-[#0f0f0f] border border-[#222] text-left text-[#666] font-mono">元のカラム名</th>
                          <th className="px-3 py-2 bg-[#0f0f0f] border border-[#222] text-left text-[#aaa]">取得値</th>
                        </tr>
                      </thead>
                      <tbody>
                        {rawData.race_info_columns.map((col, i) => (
                          <tr key={col} className={i % 2 === 0 ? 'bg-[#0a0a0a]' : 'bg-[#0c0c0c]'}>
                            <td className="px-3 py-1.5 border border-[#1a1a1a] text-[#ddd] whitespace-nowrap">{HORSE_COLUMN_LABELS[col] ?? col.replaceAll('_', ' ')}</td>
                            <td className="px-3 py-1.5 border border-[#1a1a1a] text-[#777] font-mono whitespace-nowrap">{col}</td>
                            <td className={`px-3 py-1.5 border border-[#1a1a1a] font-mono ${cellColor(rawData.race_info[col])}`}>{formatValue(rawData.race_info[col])}</td>
                          </tr>
                        ))}
                      </tbody>
                    </table>
                  </div>
                  <div className="mb-2 flex items-center justify-between text-[11px] text-[#777]">
                    <span>各馬の取得データ（{rawData.horses.length}頭）</span>
                    <span>{visibleRawCols.length} / {rawData.horse_columns.length} 項目を表示</span>
                  </div>
                  <div className="overflow-x-auto">
                    <table className="text-xs border-collapse">
                      <thead className="sticky top-0 z-10">
                        <tr>
                          {visibleRawCols.map(col => (
                            <th key={col} className="px-3 py-2 bg-[#0f0f0f] border border-[#222] whitespace-nowrap text-left">
                              <span className="block text-[#ddd]">{HORSE_COLUMN_LABELS[col] ?? col.replaceAll('_', ' ')}</span>
                              <span className="mt-0.5 block font-mono text-[10px] font-normal text-[#666]">{col}</span>
                            </th>
                          ))}
                        </tr>
                      </thead>
                      <tbody>
                        {rawData.horses.map((horse, i) => (
                          <tr key={i} className={`${i % 2 === 0 ? 'bg-[#0a0a0a]' : 'bg-[#0c0c0c]'} hover:bg-[#141414] transition-colors`}>
                            {visibleRawCols.map(col => (
                              <td key={col} className={`px-3 py-1.5 border border-[#1a1a1a] font-mono whitespace-nowrap ${cellColor(horse[col])}`}>
                                {formatValue(horse[col])}
                              </td>
                            ))}
                          </tr>
                        ))}
                      </tbody>
                    </table>
                    {visibleRawCols.length === 0 && <div className="p-6 text-center text-xs text-[#666]">一致する項目がありません</div>}
                  </div>
                </div>
              )}

              {/* ── 特徴量タブ ── */}
              {tab === 'features' && featData && (
                <div className="flex-1 overflow-auto p-4">
                  {(() => {
                    const getPrefix = (col: string) => { const i = col.indexOf('_'); return i > 0 ? col.slice(0, i) : 'misc' }
                    const toggleGroup = (g: string) => setHiddenGroups(prev => { const s = new Set(prev); s.has(g) ? s.delete(g) : s.add(g); return s })
                    const allFiltered = colFilter
                      ? featData.feature_columns.filter(c => c.toLowerCase().includes(colFilter.toLowerCase()))
                      : featData.feature_columns
                    // Build groups preserving column order
                    const groupMap: Record<string, string[]> = {}
                    for (const c of allFiltered) { const p = getPrefix(c); (groupMap[p] ??= []).push(c) }
                    const groups = Object.entries(groupMap)
                    const visibleCols = allFiltered.filter(c => !hiddenGroups.has(getPrefix(c)))
                    return (
                      <>
                        {/* Group toggle chips */}
                        <div className="flex flex-wrap gap-1.5 mb-3">
                          {groups.map(([prefix, cols]) => {
                            const hidden = hiddenGroups.has(prefix)
                            return (
                              <button
                                key={prefix}
                                onClick={() => toggleGroup(prefix)}
                                className={`px-2 py-0.5 rounded text-xs font-mono transition-colors ${
                                  hidden ? 'bg-[#111] border border-[#222] text-[#444]' : 'bg-[#1e1e1e] border border-[#333] text-[#aaa]'
                                }`}
                              >
                                {prefix} <span className="text-[#555]">({cols.length})</span>
                              </button>
                            )
                          })}
                        </div>
                        {colFilter && (
                          <div className="mb-2 text-xs text-[#555]">{visibleCols.length} / {featData.feature_columns.length} 列を表示</div>
                        )}
                        <div className="overflow-x-auto">
                          <table className="text-xs border-collapse">
                            <thead className="sticky top-0 z-10">
                              {/* Group header row */}
                              <tr>
                                {groups.filter(([p]) => !hiddenGroups.has(p)).map(([prefix, cols]) => (
                                  <th
                                    key={prefix}
                                    colSpan={cols.filter(c => !hiddenGroups.has(getPrefix(c))).length}
                                    className="px-3 py-1 bg-[#0a0a0a] border border-[#333] text-[#555] font-mono text-center text-[10px] tracking-wider uppercase"
                                  >
                                    {prefix}
                                  </th>
                                ))}
                              </tr>
                              <tr>
                                {visibleCols.map(col => (
                                  <th key={col} className="px-3 py-2 bg-[#0f0f0f] border border-[#222] text-[#666] font-mono whitespace-nowrap text-left">
                                    {col}
                                  </th>
                                ))}
                              </tr>
                            </thead>
                            <tbody>
                              {featData.records.map((row, i) => (
                                <tr key={i} className={`${i % 2 === 0 ? 'bg-[#0a0a0a]' : 'bg-[#0c0c0c]'} hover:bg-[#141414] transition-colors`}>
                                  {visibleCols.map(col => (
                                    <td key={col} className={`px-3 py-1.5 border border-[#1a1a1a] font-mono whitespace-nowrap ${cellColor(row[col])}`}>
                                      {formatValue(row[col])}
                                    </td>
                                  ))}
                                </tr>
                              ))}
                            </tbody>
                          </table>
                        </div>
                      </>
                    )
                  })()}
                </div>
              )}

              {tab === 'features' && !featData && (
                <div className="flex-1 flex items-center justify-center text-[#555] text-sm">特徴量データがありません</div>
              )}
            </>
          )}
        </main>
      </div>
    </div>
  )
}
