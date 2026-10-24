import type { ReferenceBets } from '@/lib/race-analysis-types'

const cardinalities: Record<string, number> = { '単勝': 1, 'ワイド': 2, '馬連': 2, '馬単': 2, '三連複': 3, '三連単': 3 }

export function ReferenceBetsPanel({ reference }: { reference?: ReferenceBets | null }) {
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
    <section aria-label="参考買い目" className="rounded border border-[#263848] bg-[#0b141d] p-4 space-y-2">
      <div className="text-sm font-semibold text-[#7dd3fc]">参考買い目</div>
      {rows(tickets.slice(0, 2))}
      {tickets.length > 2 && <details><summary className="cursor-pointer text-xs text-[#888]">ほかの券種</summary><div className="mt-2 space-y-2">{rows(tickets.slice(2))}</div></details>}
      <p className="text-[11px] text-[#888]">予測順位のみ · 金額なし · 購入推奨ではありません</p>
    </section>
  )
}
