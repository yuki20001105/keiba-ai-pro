import { cleanup, render, screen } from '@testing-library/react'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { BettingRecommendationPanel } from '@/components/BettingRecommendationPanel'
import { BETTING_POLICY_VERSION } from '@/lib/betting-policy'
import type { PredictionInputDiagnostics, Recommendation } from '@/lib/race-analysis-types'

vi.mock('@/lib/auth-fetch', () => ({ authFetch: vi.fn() }))

afterEach(cleanup)
const NOW = Date.parse('2026-10-11T01:00:00Z')
const input: PredictionInputDiagnostics = {
  schema: 'prediction-input-contract-v1', feature_pipeline: 'local-db-shared-v1', status: 'unverified',
  semantic_parity_verified: true, expected_count: 47, actual_count: 47,
  missing_columns: [], restored_columns: [], blocked_columns: [], all_missing_columns: [],
  source_hash_matches: true, history_available: true, live_capture_hash_bound: false,
  issue_codes: ['pre_race_db_capture_unverified'],
}
const recommendation: Recommendation = {
  policy_version: BETTING_POLICY_VERSION, action: '見送り', decision: 'skip',
  reason: 'モデルの入力互換性が未確認 / 発走済みのレース',
  reason_codes: ['input_contract_unverified', 'race_closed'],
  input_verified: false, probability_basis: 'calibrated', tickets: [],
  purchase_count: 0, unit_price: 0, total_cost: 0, expected_return: 0,
  evaluated_at: new Date(NOW).toISOString(), valid_until: new Date(NOW + 300000).toISOString(),
}

describe('shared capture reason display only', () => {
  it('replaces only the input phrase, preserving other skip reasons and the original receipt', () => {
    const before = JSON.stringify(recommendation)
    render(<BettingRecommendationPanel recommendation={recommendation} diagnostics={input} nowMs={NOW} snapshotId="read-only-display" />)
    expect(screen.getByText('発走前の取得証拠が未確認 / 発走済みのレース')).toBeInTheDocument()
    expect(screen.getByText('見送り')).toBeInTheDocument()
    expect(screen.getByText('合計 ¥0')).toBeInTheDocument()
    expect(screen.queryByRole('button', { name: '購入済みとして記録' })).not.toBeInTheDocument()
    expect(JSON.stringify(recommendation)).toBe(before)
  })
  it('does not authorize a contradictory buy proposal while making the displayed reason accurate', () => {
    render(<BettingRecommendationPanel recommendation={{ ...recommendation, decision: 'buy', input_verified: true,
      tickets: [{ bet_type: '単勝', combination: '1', horse_numbers: [1], stake: 100, odds: 5,
        probability: 0.3, expected_value: 1.5, conservative_expected_value: 1.3 }] }}
      diagnostics={input} nowMs={NOW} snapshotId="read-only-display" />)
    expect(screen.getByText('発走前の取得証拠が未確認のため買い目には使用しません。')).toBeInTheDocument()
    expect(screen.getByText('参考予測 · 見送り')).toBeInTheDocument()
    expect(screen.getByText('合計 ¥0')).toBeInTheDocument()
    expect(screen.queryByText('単勝')).not.toBeInTheDocument()
    expect(screen.queryByRole('button', { name: '購入済みとして記録' })).not.toBeInTheDocument()
  })
  it.each([
    [{ ...input, feature_pipeline: undefined, scope: 'local-safe-candidate-input-v1' }, recommendation],
    [{ ...input, issue_codes: ['unknown'] }, recommendation],
    [input, { ...recommendation, reason_codes: ['race_closed'] }],
  ] as [PredictionInputDiagnostics, Recommendation][])(
    'preserves M02 or other reasons when either required condition is absent', (diagnostics, rec) => {
      render(<BettingRecommendationPanel recommendation={rec} diagnostics={diagnostics} nowMs={NOW} />)
      expect(screen.getByText(rec.reason)).toBeInTheDocument()
      expect(screen.queryByText(/発走前の取得証拠が未確認/)).not.toBeInTheDocument()
    },
  )
  it('preserves the expiry notice instead of hiding it behind capture wording', () => {
    render(<BettingRecommendationPanel recommendation={{ ...recommendation, decision: 'buy' }} diagnostics={input} nowMs={NOW + 300000} />)
    expect(screen.getByText('オッズまたは発走時刻の有効期限を過ぎました。予測を再実行してください。')).toBeInTheDocument()
    expect(screen.queryByText(/発走前の取得証拠が未確認/)).not.toBeInTheDocument()
  })
})
