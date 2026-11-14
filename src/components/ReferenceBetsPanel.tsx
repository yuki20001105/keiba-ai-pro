'use client'

import { useState } from 'react'
import type { RacePredictResult, ReferenceBets } from '@/lib/race-analysis-types'
import {
  aggregateReferenceTickets, calculateReferenceTicket, RANKING_TRIAL_NOTE,
  referenceSimulationProbabilities, referenceTicketProbability, TRIAL_STAKE,
} from '@/lib/reference-ticket-calculation'

const cardinalities: Record<string, number> = { '単勝': 1, 'ワイド': 2, '馬連': 2, '馬単': 2, '三連複': 3, '三連単': 3 }

type TrialProps = {
  tickets: ReferenceBets['tickets']
  result: RacePredictResult
  nowMs?: number
  expectedRaceId?: string
  aggregate?: boolean
}

export function ReferenceTicketTrial(props: TrialProps) {
  const key = JSON.stringify([props.result.race_info?.race_id, props.result.model_id, props.result.performance_snapshot_id,
    props.result.predictions, props.result.reference_simulation, props.tickets, props.expectedRaceId])
  return <TicketTrialEditor key={key} {...props} />
}

function TicketTrialEditor({ tickets, result, nowMs, expectedRaceId, aggregate = false }: TrialProps) {
  const [quotes, setQuotes] = useState<Record<string, string>>({})
  const probabilities = referenceSimulationProbabilities(result, nowMs, expectedRaceId)
  const calculations = tickets.map(ticket => {
    const key = `${ticket.bet_type}:${ticket.combination}`
    const matchingRace = expectedRaceId === undefined || expectedRaceId === result.race_info?.race_id
    const saved = matchingRace && ticket.bet_type === '単勝'
      ? result.predictions?.find(p => p.horse_number === ticket.horse_numbers[0])?.odds : null
    const savedOdds = typeof saved === 'number' && Number.isFinite(saved) && saved >= 1 ? saved : null
    const edited = Object.hasOwn(quotes, key)
    const text = edited ? quotes[key] : savedOdds === null ? '' : String(savedOdds)
    const odds = text.trim() === '' ? null : Number(text)
    const probability = referenceTicketProbability(probabilities, ticket.bet_type, ticket.horse_numbers)
    return { ticket, key, text, edited, savedOdds, probability, ...calculateReferenceTicket(probability, odds) }
  })
  const summary = aggregateReferenceTickets(calculations)
  const table = (rows: typeof calculations) => (
    <div className={aggregate ? 'max-h-80 overflow-auto' : 'overflow-x-auto'}>
      <table aria-label={aggregate ? '三連単の参考買い目' : '券種別の参考買い目'} className="w-full text-xs">
        <thead><tr className="border-b border-[#333] text-[#aaa]">
          {['買い目', 'オッズ', '期待倍率', '的中率', '払戻'].map(label => (
            <th key={label} className="px-2 py-2 text-left font-normal whitespace-nowrap">{label}</th>
          ))}
        </tr></thead>
        <tbody>{rows.map(row => (
          <tr key={row.key} className="border-b border-[#222]">
            <td className="px-2 py-3 whitespace-nowrap"><span className="block sm:inline">{row.ticket.bet_type}</span><span className="sm:ml-2 font-mono text-[#7dd3fc]">{['馬単', '三連単'].includes(row.ticket.bet_type) ? row.ticket.horse_numbers.join('→') : row.ticket.combination}</span></td>
            <td className="px-2 py-3">
              <input type="number" min="1" step="0.1" inputMode="decimal"
                aria-label={`${row.ticket.bet_type} ${row.ticket.combination}の仮オッズ`}
                placeholder="未取得" value={row.text}
                onChange={event => setQuotes(previous => ({ ...previous, [row.key]: event.target.value }))}
                className="w-20 rounded border border-[#444] bg-[#111] px-2 py-1.5 text-white"
              />
              <p className="mt-1 text-[10px] text-[#888]">{row.edited ? '仮入力' : row.savedOdds !== null ? '保存値' : '入力'}</p>
            </td>
            <td className="px-2 py-3 tabular-nums font-medium text-[#bbb]">{row.expectedMultiplier === null ? '—' : row.expectedMultiplier.toFixed(2)}</td>
            <td className="px-2 py-3 tabular-nums text-[#aaa]">{row.probability === null ? '—' : `${(row.probability * 100).toFixed(1)}%`}</td>
            <td className="px-2 py-3 tabular-nums">{row.payout === null ? '—' : `¥${row.payout.toLocaleString('ja-JP')}`}</td>
          </tr>
        ))}</tbody>
      </table>
    </div>
  )
  return (
    <div className="space-y-3 text-xs">
      <p className="text-[#aaa]">各¥{TRIAL_STAKE} · 単勝は保存オッズ</p>
      {tickets.some(ticket => ticket.bet_type !== '単勝') && <p className="text-[#888]">組合せ確率は未検証</p>}
      {table(aggregate ? calculations : calculations.slice(0, 2))}
      {!aggregate && calculations.length > 2 && <details className="text-[#aaa]">
        <summary className="cursor-pointer">ほかの券種も試算</summary>{table(calculations.slice(2))}
      </details>}
      {aggregate && <div role="group" aria-label="三連単の試算合計" className="rounded bg-[#1a1a1a] p-3 space-y-1">
        <p>{tickets.length}点 · 試算額 ¥{summary.totalCost.toLocaleString('ja-JP')} · オッズ入力 {summary.pricedPoints}/{tickets.length}点</p>
        <p>全点の仮期待倍率：{summary.expectedMultiplier === null ? '全点のオッズ・確率が揃うと算出' : summary.expectedMultiplier.toFixed(2)}</p>
        {summary.expectedReturn !== null && <p>確率で重み付けした仮払戻：¥{summary.expectedReturn.toLocaleString('ja-JP', { maximumFractionDigits: 0 })}</p>}
      </div>}
      {!probabilities && <p className="text-[#aaa]">確率未確認 · 払戻のみ表示</p>}
      <details className="text-[#888]"><summary className="cursor-pointer">計算の注意</summary>
        <p className="mt-2">{RANKING_TRIAL_NOTE}</p>
        <p className="mt-1">仮払戻＝100円×オッズ。仮期待倍率＝仮の的中率×オッズ（1.00が損益分岐の目安）。発走前の取得証拠・今購入できる価格は保証しません。</p>
        {!aggregate && <p className="mt-1">券種ごとの別々の参考案で、まとめ買いの推奨ではありません。</p>}
      </details>
    </div>
  )
}

export function ReferenceBetsPanel({ reference, result, nowMs, expectedRaceId }: {
  reference?: ReferenceBets | null; result?: RacePredictResult; nowMs?: number; expectedRaceId?: string
}) {
  if (reference?.schema !== 'rank-reference-bets-v1' || reference.basis !== 'ranking_only'
      || reference.purchase_eligible !== false || reference.monetary_amounts_assigned !== false
      || !Array.isArray(reference.tickets) || reference.tickets.length === 0) return null
  if (reference.tickets.length > 6 || new Set(reference.tickets.map(ticket => ticket?.bet_type)).size !== reference.tickets.length) return null
  const tickets = reference.tickets.filter(ticket => ticket && typeof ticket.bet_type === 'string'
    && typeof ticket.combination === 'string' && Array.isArray(ticket.horse_numbers)
    && cardinalities[ticket.bet_type] === ticket.horse_numbers.length
    && ticket.horse_numbers.every(n => Number.isInteger(n) && n >= 1 && n <= 18)
    && new Set(ticket.horse_numbers).size === ticket.horse_numbers.length
    && Object.keys(ticket).every(key => ['bet_type', 'combination', 'horse_numbers'].includes(key))
    && ticket.combination === ticket.horse_numbers.join('-'))
  if (tickets.length !== reference.tickets.length) return null
  const rows = (list: typeof tickets) => list.map(ticket => (
    <div key={ticket.bet_type} className="flex justify-between gap-4 text-sm">
      <span className="text-[#888]">{ticket.bet_type}</span><span className="font-mono text-[#7dd3fc]">{ticket.combination}</span>
    </div>
  ))
  return (
    <details className="rounded border border-[#263848] bg-[#0b141d]">
      <summary className="flex cursor-pointer items-center justify-between gap-2 p-3 text-sm font-semibold text-[#7dd3fc]">
        <span>参考試算</span><span className="rounded bg-[#17232d] px-1.5 py-0.5 text-[10px] text-[#9ca3af]">購入不可</span>
      </summary>
      <section aria-label="参考買い目" className="border-t border-[#263848] p-3 space-y-2">
      {result ? <ReferenceTicketTrial tickets={tickets} result={result} nowMs={nowMs} expectedRaceId={expectedRaceId} /> : <>
        {rows(tickets.slice(0, 2))}
        {tickets.length > 2 && <details><summary className="cursor-pointer text-xs text-[#888]">ほかの券種</summary><div className="mt-2 space-y-2">{rows(tickets.slice(2))}</div></details>}
        <p className="text-[11px] text-[#888]">順位のみ · 金額なし</p>
      </>}
      </section>
    </details>
  )
}
