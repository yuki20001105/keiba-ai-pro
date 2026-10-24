import { cleanup, render, screen, within } from '@testing-library/react'
import { afterEach, describe, expect, it } from 'vitest'
import { modelVerificationStatus } from '@/lib/model-verification-status'
import { ModelEvaluationSummary } from '@/components/ModelEvaluationSummary'

afterEach(cleanup)

describe('separate model verification states', () => {
  it('does not infer market or strategy evidence from calibrated probabilities', () => {
    const states = modelVerificationStatus({ status: 'validated', calibrated: true, validated: true,
      market_edge_verified: false, profitability_verified: false })
    expect(states.map(state => state.value)).toEqual(['検証済み', '未確認', '未確認'])
  })

  it('treats absent/false market and profit flags as unknown, not failure', () => {
    expect(modelVerificationStatus().every(state => state.value === '未確認')).toBe(true)
    expect(modelVerificationStatus({ status: 'unverified', calibrated: true, validated: true })[0].value)
      .toBe('校正済み・未検証')
  })

  it('accepts only separately explicit report flags and does not infer strategy from market', () => {
    const evidence = { status: 'validated', calibrated: true, validated: true, market_edge_verified: true }
    expect(modelVerificationStatus(evidence).map(state => state.value)).toEqual(['検証済み', '検証済み', '未確認'])
    expect(modelVerificationStatus({ ...evidence, profitability_verified: true }).every(state => state.verified)).toBe(true)
    expect(modelVerificationStatus({ profitability_verified: true }).map(state => state.value))
      .toEqual(['未確認', '未確認', '検証済み'])
    expect(modelVerificationStatus({ market_edge_verified: true }).map(state => state.value))
      .toEqual(['未確認', '検証済み', '未確認'])
  })

  it('does not elevate a low NLL, EV eligibility or 118% ROI into betting authority', () => {
    render(<ModelEvaluationSummary featurePipeline="local-db-shared-optimized-v1"
      evaluation={{ primary: { win_roi: 118, rank_correlation: .55, rmse: .23 } }}
      probabilityValidation={{ status: 'validated', calibrated: true, validated: true,
        eligible_for_ev: true, race_count: 5, metrics: { nll: 1.9, brier: .79 } }} />)
    expect(within(screen.getByTestId('verification-probability')).getByText('検証済み')).toBeInTheDocument()
    expect(within(screen.getByTestId('verification-market')).getByText('未確認')).toBeInTheDocument()
    expect(within(screen.getByTestId('verification-strategy')).getByText('未確認')).toBeInTheDocument()
    expect(screen.getByText(/EV用の確率条件は購入の許可ではありません/)).toBeInTheDocument()
    expect(screen.queryByText(/購入推奨|利益が出る|不合格/)).not.toBeInTheDocument()
  })
})
