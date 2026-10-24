import { cleanup, render, screen } from '@testing-library/react'
import { afterEach, describe, expect, it } from 'vitest'
import { ReferenceBetsPanel } from '@/components/ReferenceBetsPanel'
import type { ReferenceBets } from '@/lib/race-analysis-types'

afterEach(cleanup)

const reference: ReferenceBets = {
  schema: 'rank-reference-bets-v1', basis: 'ranking_only', purchase_eligible: false,
  monetary_amounts_assigned: false, tickets: [
    { bet_type: '単勝', combination: '5', horse_numbers: [5] },
    { bet_type: 'ワイド', combination: '2-5', horse_numbers: [2, 5] },
    { bet_type: '馬単', combination: '5-2', horse_numbers: [5, 2] },
  ],
}

describe('rank-only reference examples', () => {
  it('shows references without a monetary or purchase action', () => {
    render(<ReferenceBetsPanel reference={reference} />)
    expect(screen.getByRole('region', { name: '参考買い目' })).toBeTruthy()
    expect(screen.getByText('予測順位のみ · 金額なし · 購入推奨ではありません')).toBeTruthy()
    expect(screen.queryByRole('button')).toBeNull()
    expect(screen.queryByText(/¥|勝率|期待値/)).toBeNull()
  })
  it('does not turn malformed or purported executable offers into references', () => {
    const { rerender, container } = render(<ReferenceBetsPanel reference={undefined} />)
    expect(container.textContent).toBe('')
    rerender(<ReferenceBetsPanel reference={{ ...reference, purchase_eligible: true } as unknown as ReferenceBets} />)
    expect(container.textContent).toBe('')
    rerender(<ReferenceBetsPanel reference={{ ...reference, tickets: [{ bet_type: '単勝', combination: '3', horse_numbers: [5] }] }} />)
    expect(container.textContent).toBe('')
  })
})
