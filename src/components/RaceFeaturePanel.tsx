'use client'
import { useState } from 'react'
import type { FeatureData, Prediction } from '@/lib/race-analysis-types'

function fmt(v: unknown, digits = 3): string {
  if (v === null || v === undefined) return '—'
  if (typeof v === 'number') return isNaN(v) ? '—' : v.toLocaleString(undefined, { maximumFractionDigits: digits })
  if (typeof v === 'boolean') return v ? '✓' : '✗'
  return String(v)
}

function zScore(val: unknown, col: string, records: Record<string, unknown>[]): number | null {
  if (typeof val !== 'number' || isNaN(val)) return null
  const vals = records.map(r => r[col]).filter(v => typeof v === 'number' && !isNaN(v as number)) as number[]
  if (vals.length < 2) return null
  const mean = vals.reduce((a, b) => a + b, 0) / vals.length
  const std = Math.sqrt(vals.reduce((a, b) => a + (b - mean) ** 2, 0) / vals.length)
  if (std === 0) return null
  return (val - mean) / std
}

function featCellColor(z: number | null): string {
  if (z === null) return 'text-[#444]'
  if (z > 1.5) return 'text-[#86efac]'
  if (z > 0.5) return 'text-[#a3e635]'
  if (z < -1.5) return 'text-[#f87171]'
  if (z < -0.5) return 'text-[#fb923c]'
  return 'text-[#888]'
}

const META_COLS = ['horse_number', 'horse_no', 'horse_name', 'jockey_name']

type Props = { featData: FeatureData; predictions: Prediction[] }

export function RaceFeaturePanel({ featData, predictions }: Props) {
  const [colFilter, setColFilter] = useState('')
  const [hiddenGroups, setHiddenGroups] = useState<Set<string>>(new Set())

  const getPrefix = (col: string) => { const i = col.indexOf('_'); return i > 0 ? col.slice(0, i) : 'misc' }
  const toggleGroup = (g: string) => setHiddenGroups(prev => {
    const s = new Set(prev); s.has(g) ? s.delete(g) : s.add(g); return s
  })

  const filteredCols = colFilter
    ? featData.feature_columns.filter(c => c.toLowerCase().includes(colFilter.toLowerCase()))
    : featData.feature_columns

  const nonMeta = filteredCols.filter(c => !META_COLS.includes(c))
  const metaPresent = META_COLS.filter(c => featData.feature_columns.includes(c))
  const groupMap: Record<string, string[]> = {}
  for (const c of nonMeta) { const p = getPrefix(c); (groupMap[p] ??= []).push(c) }
  const groups = Object.entries(groupMap)
  const visibleNonMeta = nonMeta.filter(c => !hiddenGroups.has(getPrefix(c)))
  const displayCols = [...metaPresent, ...visibleNonMeta]

  const sortedRecords = [...featData.records].sort((a, b) => {
    const aNo = Number(a.horse_number ?? a.horse_no ?? 0)
    const bNo = Number(b.horse_number ?? b.horse_no ?? 0)
    const aRank = predictions.find(p => p.horse_number === aNo)?.predicted_rank ?? 99
    const bRank = predictions.find(p => p.horse_number === bNo)?.predicted_rank ?? 99
    return aRank - bRank
  })

  return (
    <div className="flex-1 overflow-auto p-4">
      {/* 列名フィルタ */}
      <div className="mb-3 flex items-center gap-3">
        <input
          type="text"
          placeholder="列名で絞り込み..."
          value={colFilter}
          onChange={e => setColFilter(e.target.value)}
          className="px-3 py-1.5 bg-[#111] border border-[#1e1e1e] rounded text-xs text-white placeholder-[#444] focus:outline-none focus:border-[#333] w-44"
        />
        {colFilter && (
          <span className="text-xs text-[#555]">{filteredCols.length}列を表示 / {featData.feature_columns.length}列中</span>
        )}
      </div>

      {/* グループ表示切替チップ */}
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

      {/* テーブル */}
      <div className="overflow-x-auto">
        <table className="text-xs border-collapse">
          <thead className="sticky top-0 z-10">
            <tr>
              <th className="px-3 py-1 bg-[#0a0a0a] border border-[#333] text-[#444] font-mono text-center text-[10px]">—</th>
              <th colSpan={metaPresent.length} className="px-3 py-1 bg-[#0a0a0a] border border-[#333] text-[#555] font-mono text-center text-[10px] uppercase tracking-wider">meta</th>
              {groups.filter(([p]) => !hiddenGroups.has(p)).map(([prefix, cols]) => (
                <th
                  key={prefix}
                  colSpan={cols.filter(c => !hiddenGroups.has(getPrefix(c))).length}
                  className="px-3 py-1 bg-[#0a0a0a] border border-[#333] text-[#555] font-mono text-center text-[10px] uppercase tracking-wider"
                >
                  {prefix}
                </th>
              ))}
            </tr>
            <tr>
              <th className="px-3 py-2 bg-[#0f0f0f] border border-[#222] text-[#888] font-mono whitespace-nowrap text-left w-8">予測順</th>
              {displayCols.map(col => (
                <th key={col} className={`px-3 py-2 bg-[#0f0f0f] border border-[#222] whitespace-nowrap text-left font-mono ${metaPresent.includes(col) ? 'text-[#aaa]' : 'text-[#666]'}`}>
                  {col}
                </th>
              ))}
            </tr>
          </thead>
          <tbody>
            {sortedRecords.map((row, i) => {
              const horseNo = Number(row.horse_number ?? row.horse_no ?? i + 1)
              const pred = predictions.find(p => p.horse_number === horseNo)
              const rank = pred?.predicted_rank ?? (i + 1)
              const isTop = rank === 1
              const isTop3 = rank <= 3
              return (
                <tr key={i} className={`${isTop ? 'bg-[#0d1a0d]' : isTop3 ? 'bg-[#0f130f]' : i % 2 === 0 ? 'bg-[#0a0a0a]' : 'bg-[#0c0c0c]'} hover:bg-[#141414] transition-colors`}>
                  <td className={`px-3 py-1.5 border border-[#1a1a1a] text-center font-bold ${isTop ? 'text-[#fbbf24]' : isTop3 ? 'text-[#86efac]' : 'text-[#555]'}`}>
                    {rank}
                  </td>
                  {displayCols.map(col => {
                    const v = row[col]
                    const z = metaPresent.includes(col) ? null : zScore(v, col, featData.records)
                    return (
                      <td key={col} className={`px-3 py-1.5 border border-[#1a1a1a] font-mono whitespace-nowrap ${metaPresent.includes(col) ? 'text-white' : featCellColor(z)}`}>
                        {fmt(v, 4)}
                      </td>
                    )
                  })}
                </tr>
              )
            })}
          </tbody>
        </table>
      </div>

      {/* 色凡例 */}
      <div className="mt-3 flex items-center gap-4 text-[10px] text-[#555]">
        <span>色凡例（z-スコア）:</span>
        {[
          { color: 'text-[#86efac]', label: '≥+1.5σ（高）' },
          { color: 'text-[#a3e635]', label: '+0.5σ〜' },
          { color: 'text-[#888]', label: '±0.5σ（平均）' },
          { color: 'text-[#fb923c]', label: '−0.5σ〜' },
          { color: 'text-[#f87171]', label: '≤−1.5σ（低）' },
        ].map(e => (
          <span key={e.label} className={e.color}>{e.label}</span>
        ))}
      </div>
    </div>
  )
}
