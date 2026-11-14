import { cleanup, fireEvent, render, screen, within } from '@testing-library/react'
import { afterEach, describe, expect, it } from 'vitest'
import { ReferenceBetsPanel } from '@/components/ReferenceBetsPanel'
import type { ReferenceBets } from '@/lib/race-analysis-types'
import { referenceSimulationResult, REFERENCE_NOW } from './helpers/reference-expectation'

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
  it('calculates a hypothetical win quote despite missing capture and stored odds, without mutating the result', () => {
    const result = referenceSimulationResult()
    const reference: ReferenceBets = {
      schema: 'rank-reference-bets-v1', basis: 'ranking_only', purchase_eligible: false,
      monetary_amounts_assigned: false, tickets: [{ bet_type: '単勝', combination: '1', horse_numbers: [1] }],
    }
    const before = JSON.stringify(result)
    render(<ReferenceBetsPanel reference={reference} result={result} nowMs={REFERENCE_NOW} />)
    fireEvent.click(screen.getByText('参考試算'))
    const row = within(screen.getByRole('row', { name: /単勝/ }))
    fireEvent.change(row.getByRole('spinbutton', { name: '単勝 1の仮オッズ' }), { target: { value: '9' } })
    expect(row.getByText('¥900')).toBeInTheDocument()
    expect(row.getByText('1.17')).toBeInTheDocument()
    expect(screen.queryByRole('button', { name: /購入|記録/ })).not.toBeInTheDocument()
    expect(JSON.stringify(result)).toBe(before)
  })

  it('keeps invalid odds unpriced and clears typed prices on a different prediction', () => {
    const result = referenceSimulationResult()
    const single: ReferenceBets = { ...reference, tickets: [{ bet_type: '単勝', combination: '1', horse_numbers: [1] }] }
    const { rerender } = render(<ReferenceBetsPanel reference={single} result={result} nowMs={REFERENCE_NOW} />)
    fireEvent.click(screen.getByText('参考試算'))
    const input = screen.getByRole('spinbutton', { name: '単勝 1の仮オッズ' })
    fireEvent.change(input, { target: { value: '0' } })
    expect(screen.queryByText('¥0')).not.toBeInTheDocument()
    fireEvent.change(input, { target: { value: '9' } })
    expect(screen.getByText('¥900')).toBeInTheDocument()
    rerender(<ReferenceBetsPanel reference={single} result={{ ...result, model_id: 'changed-model' }} nowMs={REFERENCE_NOW} />)
    expect(screen.getByRole('spinbutton', { name: '単勝 1の仮オッズ' })).toHaveValue(null)
    expect(screen.queryByText('¥900')).not.toBeInTheDocument()
  })

  it('shows references without a monetary or purchase action', () => {
    render(<ReferenceBetsPanel reference={reference} />)
    const disclosure = screen.getByText('参考試算').closest('details')!
    expect(disclosure).not.toHaveAttribute('open')
    fireEvent.click(screen.getByText('参考試算'))
    expect(screen.getByRole('region', { name: '参考買い目' })).toBeTruthy()
    expect(screen.getByText('順位のみ · 金額なし')).toBeTruthy()
    expect(screen.getByText('購入不可')).toBeTruthy()
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
