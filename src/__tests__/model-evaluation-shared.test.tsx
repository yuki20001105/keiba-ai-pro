import { cleanup, render, screen, within } from '@testing-library/react'
import { afterEach, describe, expect, it } from 'vitest'
import { ModelEvaluationSummary, type ProbabilityValidation } from '@/components/ModelEvaluationSummary'

afterEach(cleanup)

const evaluation = {
  rmse_unit: 'm/s',
  primary: { rank_correlation: 0.4321, rmse: 0.2345, top_pick_win_rate: 0.3, win_roi: 110 },
  details: { evaluation_date_from: '20250105', evaluation_date_to: '20251228', evaluation_race_count: 3009 },
  time_slices: [{ period: '2025', race_count: 3009, rank_correlation: 0.4321, rmse: 0.2345, win_roi: 110 }],
}

describe('shared DB model evaluation', () => {
  it.each(['local-db-shared-v1', 'local-db-shared-optimized-v1', 'local-db-context-v1'])(
    'puts only rank correlation and physical RMSE in the primary cards, with ROI separate: %s', (pipeline) => {
    render(<ModelEvaluationSummary featurePipeline={pipeline} evaluation={evaluation} />)
    const primary = within(screen.getByTestId('model-primary-metrics'))
    expect(primary.getByText('順位相関')).toBeInTheDocument()
    expect(primary.getByText('0.432')).toBeInTheDocument()
    expect(primary.getByText('予測誤差')).toBeInTheDocument()
    expect(primary.getByText('(RMSE m/s)')).toBeInTheDocument()
    expect(primary.queryByText('回収率')).not.toBeInTheDocument()
    expect(primary.queryByText('1位推奨の勝率')).not.toBeInTheDocument()
    expect(screen.queryByRole('columnheader', { name: '回収率' })).not.toBeInTheDocument()
    expect(screen.queryByText('110.0%')).not.toBeInTheDocument()
    expect(screen.getByTestId('model-calibration-state')).toHaveTextContent('勝率校正：未校正')
    expect(screen.getByTestId('model-calibration-state')).toHaveTextContent('買い目・回収率は別評価')
    expect(screen.getByText('2025/01/05〜2025/12/28')).toBeInTheDocument()
  })

  it.each([
    ['local-db-shared-v1', '基準47項目'],
    ['local-db-shared-optimized-v1', '候補46項目'],
  ])('shows the exact shared profile without treating a candidate as verified: %s', (pipeline, label) => {
    render(<ModelEvaluationSummary featurePipeline={pipeline} evaluation={{ primary: {
      rank_correlation: 0.42471, rmse: 0.24558,
    } }} />)
    expect(screen.getByTestId('model-feature-profile')).toHaveTextContent(label)
    expect(screen.getByText('(RMSE m/s)')).toBeInTheDocument()
    expect(screen.getByTestId('model-calibration-state')).toHaveTextContent('未校正')
    expect(within(screen.getByTestId('model-probability-validation')).getByText('未達')).toBeInTheDocument()
    if (pipeline === 'local-db-shared-optimized-v1') {
      expect(screen.getByText('直近5走の複勝圏率を除外した候補です。安定した優位性は未確認です。')).toBeInTheDocument()
    } else {
      expect(screen.queryByText(/安定した優位性/)).not.toBeInTheDocument()
    }
  })

  it('preserves the five cards and ROI for legacy and fixed research models', () => {
    render(<ModelEvaluationSummary evaluation={evaluation} />)
    const primary = within(screen.getByTestId('model-primary-metrics'))
    expect(primary.getByText('回収率')).toBeInTheDocument()
    expect(primary.getByText('1位推奨の勝率')).toBeInTheDocument()
    expect(primary.getByText('人気1位との差')).toBeInTheDocument()
    expect(screen.queryByTestId('model-calibration-state')).not.toBeInTheDocument()
  })

  it.each([
    [{ status: 'validated', validated: true, eligible_for_ev: true }, '未校正', '未達'],
    [{ status: 'unverified', calibrated: true, validated: true, eligible_for_ev: true }, '校正済み・未検証', '未達'],
    [{ status: 'validated', calibrated: true, validated: false, eligible_for_ev: true }, '校正済み・未検証', '未達'],
    [{ status: 'validated', calibrated: true, validated: true, eligible_for_ev: false }, '検証済み', '未達'],
    [{ status: 'validated', calibrated: true, validated: true, eligible_for_ev: true }, '検証済み', '充足'],
  ] as [ProbabilityValidation, string, string][])(
    'does not mistake calibration or a partial flag for EV validation: %o', (validation, label, evState) => {
      render(<ModelEvaluationSummary featurePipeline="local-db-shared-v1" probabilityValidation={validation} />)
      expect(screen.getByTestId('model-calibration-state')).toHaveTextContent(`勝率校正：${label}`)
      expect(within(screen.getByTestId('model-probability-validation')).getByText(evState)).toBeInTheDocument()
      expect(screen.getByText('買い目の判断には、発走前の入力・オッズも別途確認します。')).toBeInTheDocument()
    },
  )

  it('reads embedded probability evidence and shows bounded compute metadata in details', () => {
    render(<ModelEvaluationSummary featurePipeline="local-db-shared-v1"
      evaluation={{ ...evaluation, probability_validation: {
        status: 'unverified', calibrated: true, validated: false,
        metrics: { nll: 2.1034, brier: 0.0901, selection_ece: 0.0241 },
      } }}
      trainingTimeSeconds={119.7} trainingParallelWorkers={2} nativeThreads={4} />)
    expect(screen.getByTestId('model-calibration-state')).toHaveTextContent('校正済み・未検証')
    expect(screen.getByText('2.103')).toBeInTheDocument()
    expect(screen.getByText('0.090')).toBeInTheDocument()
    expect(screen.getByText('0.024')).toBeInTheDocument()
    expect(screen.getByTestId('model-training-runtime')).toHaveTextContent('2分0秒')
    expect(screen.getByTestId('model-training-runtime')).toHaveTextContent('2候補')
    expect(screen.getByTestId('model-training-runtime')).toHaveTextContent('4スレッド/候補')
  })

  it('does not display invalid numbers as valid metrics or compute budgets', () => {
    render(<ModelEvaluationSummary featurePipeline="local-db-shared-v1"
      evaluation={{ primary: { rank_correlation: NaN, rmse: Infinity } }}
      probabilityValidation={{ metrics: { nll: NaN, brier: Infinity } }}
      trainingTimeSeconds={-1} trainingParallelWorkers={0} nativeThreads={1.5} />)
    expect(within(screen.getByTestId('model-primary-metrics')).getAllByText('未計測')).toHaveLength(2)
    expect(screen.queryByTestId('model-training-runtime')).not.toBeInTheDocument()
    expect(screen.queryByText(/NaN|Infinity/)).not.toBeInTheDocument()
  })

  it('keeps an opt-in context candidate reference-only even if embedded flags claim live eligibility', () => {
    render(<ModelEvaluationSummary featurePipeline="local-db-context-v1" evaluation={evaluation}
      probabilityValidation={{ calibrated: true, status: 'validated', validated: true,
        eligible_for_ev: true, market_edge_verified: true, profitability_verified: true }} />)
    expect(screen.getByTestId('model-feature-profile')).toHaveTextContent('条件・展開80項目')
    expect(screen.getByTestId('context-model-reference-state')).toHaveTextContent('購入判断未承認')
    expect(screen.getByTestId('model-calibration-state')).toHaveTextContent('校正済み・未検証')
    expect(within(screen.getByTestId('model-primary-metrics')).queryByText('回収率')).not.toBeInTheDocument()
    expect(within(screen.getByTestId('model-probability-validation')).getByText('未達')).toBeInTheDocument()
  })
})
