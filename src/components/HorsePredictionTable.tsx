'use client'

import { useState } from 'react'
import type { RacePredictResult } from '@/lib/race-analysis-types'
import { predictionInputVerified } from '@/lib/prediction-input-contract'
import { predictionExpectationDisplay } from '@/lib/prediction-expectation-display'
import { HORSE_MARKS, type CandidateSelection, type HorseMark } from '@/lib/horse-candidates'
import { useHorseCandidateState } from '@/components/HorseCandidatePanel'

type SortKey = 'rank' | 'mark' | 'horse_number' | 'horse_name' | 'jockey_name' | 'probability' | 'expected_value' | 'odds'
type SortState = { key: SortKey; direction: 'ascending' | 'descending' }
type ExtendedPrediction = RacePredictResult['predictions'][number] & { p_place3?: number | null; p_ensemble?: number | null }
type Props = {
  predictions: RacePredictResult['predictions']
  recommendation?: RacePredictResult['recommendation']
  input_diagnostics?: RacePredictResult['input_diagnostics']
  result?: RacePredictResult
  nowMs?: number
}

const COLUMNS: { key: SortKey; label: (calibrated: boolean, expectationLabel: string) => string }[] = [
  { key: 'rank', label: () => '順位' },
  { key: 'mark', label: () => '印' },
  { key: 'horse_number', label: () => '馬番' },
  { key: 'horse_name', label: () => '馬名' },
  { key: 'jockey_name', label: () => '騎手' },
  { key: 'probability', label: calibrated => calibrated ? '勝率' : '参考比率' },
  { key: 'expected_value', label: (_calibrated, expectationLabel) => expectationLabel },
  { key: 'odds', label: () => 'オッズ' },
]

function markFor(horseNumber: number, selection: CandidateSelection | null) {
  return selection?.selected.includes(horseNumber) ? selection.marks[horseNumber] : undefined
}

function sortValue(horse: ExtendedPrediction, key: SortKey, index: number, mark?: string, referenceIndex = false): number | string | null {
  switch (key) {
    case 'rank': return horse.predicted_rank ?? index + 1
    case 'mark': return mark ? HORSE_MARKS.findIndex(item => item.mark === mark) : null
    case 'horse_number': return horse.horse_number
    case 'horse_name': return horse.horse_name
    case 'jockey_name': return horse.jockey_name || null
    case 'probability': return Number.isFinite(horse.p_norm) ? horse.p_norm : Number.isFinite(horse.win_probability) ? horse.win_probability : null
    case 'expected_value': return referenceIndex
      ? Number.isFinite(horse.p_norm) && horse.p_norm >= 0 && horse.p_norm <= 1 && typeof horse.odds === 'number' && Number.isFinite(horse.odds) && horse.odds >= 1 ? horse.p_norm * horse.odds : null
      : Number.isFinite(horse.expected_value) ? horse.expected_value : null
    case 'odds': return Number.isFinite(horse.odds) ? horse.odds : null
  }
}

export function HorsePredictionTable({ predictions, recommendation, input_diagnostics, result, nowMs = Date.now() }: Props) {
  const [sort, setSort] = useState<SortState>({ key: 'rank', direction: 'ascending' })
  const calibrated = recommendation?.probability_basis === 'calibrated'
    && recommendation.input_verified === true && predictionInputVerified(input_diagnostics)
  const expectation = predictionExpectationDisplay(result, recommendation, nowMs)
  const quote = result?.odds_refresh
  const actualQuote = quote?.status === 'middle' || quote?.status === 'result'
  const referenceIndex = expectation.mode === 'unavailable' && actualQuote
    && result?.input_diagnostics?.current_outcomes_consumed === false
  const expectationLabel = referenceIndex ? '参考指数' : expectation.label
  const quoteTime = quote?.observed_at ? Date.parse(quote.observed_at) : Number.NaN
  const quoteTimestamp = Number.isFinite(quoteTime)
    ? new Intl.DateTimeFormat('ja-JP', { timeZone: 'Asia/Tokyo', month: '2-digit', day: '2-digit',
      hour: '2-digit', minute: '2-digit', second: '2-digit', hour12: false }).format(quoteTime) : '時刻不明'
  const maxRatio = Math.max(...predictions.map(horse => horse.p_norm ?? horse.win_probability ?? 0), 0.001)
  const candidateState = useHorseCandidateState()
  const ranked = predictions.map((horse, index) => ({ horse: horse as ExtendedPrediction, index }))
  ranked.sort((left, right) => {
    const leftMark = markFor(left.horse.horse_number, candidateState?.state.selection ?? null)
    const rightMark = markFor(right.horse.horse_number, candidateState?.state.selection ?? null)
    const a = sortValue(left.horse, sort.key, left.index, leftMark, referenceIndex)
    const b = sortValue(right.horse, sort.key, right.index, rightMark, referenceIndex)
    if (a == null && b == null) return left.index - right.index
    if (a == null) return 1
    if (b == null) return -1
    const compared = typeof a === 'string' && typeof b === 'string' ? a.localeCompare(b, 'ja') : Number(a) - Number(b)
    return compared === 0 ? left.index - right.index : compared * (sort.direction === 'ascending' ? 1 : -1)
  })

  function sortBy(key: SortKey) {
    setSort(current => current.key === key
      ? { key, direction: current.direction === 'ascending' ? 'descending' : 'ascending' }
      : { key, direction: 'ascending' })
  }

  return (
    <div className="min-w-0 overflow-x-auto">
      <table aria-label="予測一覧" className="w-full min-w-[680px] text-xs">
        <thead>
          <tr className="border-y border-[#1e1e1e] text-left text-xs text-[#888]">
            {COLUMNS.map(column => {
              const label = column.label(calibrated, expectationLabel)
              const active = sort.key === column.key
              return (
                <th key={column.key} aria-sort={active ? sort.direction : 'none'} className="whitespace-nowrap px-3 py-2 font-normal">
                  <button type="button" onClick={() => sortBy(column.key)} className="inline-flex items-center gap-1 hover:text-white" aria-label={`${label}で${active && sort.direction === 'ascending' ? '降順' : '昇順'}に並べ替え`}>
                    {label}<span aria-hidden="true" className="text-[10px]">{active ? sort.direction === 'ascending' ? '↑' : '↓' : '↕'}</span>
                  </button>
                </th>
              )
            })}
          </tr>
        </thead>
        <tbody>
          {ranked.map(({ horse, index }) => {
            const ratio = horse.p_norm ?? horse.win_probability
            const validRatio = typeof ratio === 'number' && Number.isFinite(ratio) && ratio >= 0 && ratio <= 1
            const mark = markFor(horse.horse_number, candidateState?.state.selection ?? null)
            const ev = referenceIndex
              ? validRatio && typeof horse.odds === 'number' && Number.isFinite(horse.odds) && horse.odds >= 1 ? ratio * horse.odds : undefined
              : expectation.values.get(horse.horse_number)
            const evColor = ev == null ? 'text-[#777]' : referenceIndex || expectation.mode === 'reference' ? 'text-[#aaa]' : ev >= 1.2 ? 'text-[#4ade80]' : ev >= 1 ? 'text-[#facc15]' : 'text-[#aaa]'
            return (
              <tr key={horse.horse_number} className="border-b border-[#1a1a1a] last:border-0 hover:bg-[#141414]">
                <td className="whitespace-nowrap px-2 py-1.5 text-[#aaa]">{horse.predicted_rank ?? index + 1}</td>
                <td className="whitespace-nowrap px-1 py-1 text-center">
                  <select aria-label={`${horse.horse_number}番の印を編集`} value={mark ?? ''}
                    onChange={event => candidateState?.dispatch({ type: 'mark', horseNumber: horse.horse_number, mark: event.target.value === '' ? null : event.target.value as HorseMark })}
                    disabled={!candidateState}
                    className={`w-12 cursor-pointer rounded border border-transparent bg-transparent p-1 text-center text-base font-bold focus:border-[#555] focus:outline-none disabled:cursor-default ${mark === '◎' ? 'text-[#fbbf24]' : mark === '○' ? 'text-[#e5e7eb]' : mark === '▲' ? 'text-[#fb923c]' : mark === '☆' ? 'text-[#facc15]' : 'text-[#9ca3af]'}`}>
                    <option value="">—</option>
                    {HORSE_MARKS.map(({ mark: option }) => <option key={option} value={option}>{option}</option>)}
                  </select>
                </td>
                <td className="whitespace-nowrap px-2 py-1.5 font-medium">{horse.horse_number}</td>
                <td className="min-w-28 whitespace-nowrap px-2 py-1.5 font-medium">{horse.horse_name}</td>
                <td className="whitespace-nowrap px-2 py-1.5 text-[#999]">{horse.jockey_name || '—'}</td>
                <td className="whitespace-nowrap px-2 py-1.5 tabular-nums">
                  {validRatio ? <div className="flex min-w-20 items-center gap-1.5"><div className="h-1.5 w-10 overflow-hidden rounded-full bg-[#1e1e1e]"><div className="h-full rounded-full bg-[#4ade80]" style={{ width: `${Math.min(100, Math.round(ratio / maxRatio * 100))}%` }} /></div><span className="text-[#4ade80]">{(ratio * 100).toFixed(1)}%</span></div> : '—'}
                </td>
                <td className={`whitespace-nowrap px-2 py-1.5 tabular-nums font-medium ${evColor}`}>{ev == null ? '—' : ev.toFixed(2)}</td>
                <td className="whitespace-nowrap px-2 py-1.5 tabular-nums text-[#ddd]">{horse.odds == null ? '—' : horse.odds}</td>
              </tr>
            )
          })}
          {predictions.length === 0 && <tr><td colSpan={COLUMNS.length} className="px-3 py-5 text-center text-xs text-[#888]">出走馬データがありません。</td></tr>}
        </tbody>
      </table>
      {quote && <p role="status" className="px-3 py-2 text-xs text-[#888]">
        {actualQuote ? `${quote.status === 'result' ? '確定単勝オッズ（締切済み）' : '単勝オッズ取得'}：${quoteTimestamp}`
          : quote.status === 'historical' ? '過去レースの保存オッズ（更新なし）'
            : quote.status === 'yoso' || quote.status === 'not_published' ? '実オッズ未公開（予想オッズは使用しません）'
              : '実オッズ未取得（旧オッズは使用しません）'}
      </p>}
      {referenceIndex && <p className="px-3 pb-2 text-xs text-[#888]">参考指数＝参考比率×実オッズ。検証済み勝率ではないため、期待値・購入根拠ではありません。</p>}
      {candidateState?.state.notice && <p role="status" className="px-3 py-2 text-xs text-[#fbbf24]">{candidateState.state.notice}</p>}
    </div>
  )
}
