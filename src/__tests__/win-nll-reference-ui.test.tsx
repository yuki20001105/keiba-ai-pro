import { cleanup, render, screen, within } from '@testing-library/react'
import { afterEach, describe, expect, it } from 'vitest'
import { ModelEvaluationSummary } from '@/components/ModelEvaluationSummary'
import { PredictionInputNotice } from '@/components/PredictionInputNotice'
import { formatModelOptionLabel } from '@/lib/model-display'
import { predictionInputVerified, sharedFeatureProfileCount } from '@/lib/prediction-input-contract'
import type { PredictionInputDiagnostics } from '@/lib/race-analysis-types'

afterEach(cleanup)

const PIPELINE = 'local-win-nll-reference-v1'
const diagnostics: PredictionInputDiagnostics = {
  schema: 'prediction-input-contract-v1', feature_pipeline: PIPELINE,
  reference_only: true, status: 'unverified', semantic_parity_verified: false,
  expected_count: 50, actual_count: 50, missing_columns: [], restored_columns: [],
  blocked_columns: [], all_missing_columns: [], source_hash_matches: true,
  history_available: true, live_capture_hash_bound: false,
  issue_codes: ['win_nll_reference_only', 'prestart_market_transfer_unverified'],
}

describe('activated race-NLL candidate reference UI', () => {
  it('labels the probability error without conflating it with physical-speed RMSE or betting ROI', () => {
    render(<ModelEvaluationSummary featurePipeline={PIPELINE} target="win"
      evaluation={{ rmse_unit: 'm/s', evaluation_label: '既知2026期間・事後検証',
        primary: { rank_correlation: .5521, rmse: .2328, win_roi: 118 },
        details: { evaluation_date_from: '20260101', evaluation_date_to: '20260913', evaluation_race_count: 2000 },
        time_slices: [{ period: '2026', race_count: 2000, rank_correlation: .5521, rmse: .2328, win_roi: 118 }],
      }}
      probabilityValidation={{ status: 'unverified', calibrated: true, validated: false,
        eligible_for_ev: false, market_edge_verified: false, profitability_verified: false,
        metrics: { nll: 1.9234, brier: .0563 } }} />)
    const primary = within(screen.getByTestId('model-primary-metrics'))
    expect(primary.getByText('0.552')).toBeInTheDocument()
    expect(primary.getByText('0.233')).toBeInTheDocument()
    expect(primary.getByText('(RMSE 勝敗0/1)')).toBeInTheDocument()
    expect(primary.queryByText('回収率')).not.toBeInTheDocument()
    expect(screen.queryByText(/m\/s|R²|118.0%/)).not.toBeInTheDocument()
    expect(screen.getByTestId('model-feature-profile')).toHaveTextContent('46特徴量＋市場4項目（50項目）')
    expect(screen.getByTestId('win-nll-reference-state')).toHaveTextContent('購入判断未承認')
    expect(screen.getByTestId('model-calibration-state')).toHaveTextContent('校正済み・未検証')
    expect(screen.getByText('1.923')).toBeInTheDocument()
    expect(screen.getByText('0.056')).toBeInTheDocument()
    expect(screen.getByText('2026/01/01〜2026/09/13')).toBeInTheDocument()
    expect(screen.getByRole('columnheader', { name: 'RMSE (勝敗0/1)' })).toBeInTheDocument()
    expect(screen.getByText(/過去の最終オッズによる評価/)).toBeInTheDocument()
  })

  it('never changes reference-mode verification labels into purchase validation from broad metadata', () => {
    render(<ModelEvaluationSummary featurePipeline={PIPELINE}
      probabilityValidation={{ status: 'validated', calibrated: true, validated: true,
        eligible_for_ev: true, market_edge_verified: true, profitability_verified: true }} />)
    expect(screen.getByTestId('verification-probability')).toHaveTextContent('校正済み・未検証')
    expect(screen.getByTestId('verification-market')).toHaveTextContent('未確認')
    expect(screen.getByTestId('verification-strategy')).toHaveTextContent('未確認')
    expect(within(screen.getByTestId('model-probability-validation')).getByText('未達')).toBeInTheDocument()
    expect(screen.queryByText('充足')).not.toBeInTheDocument()
  })

  it('explains reference restrictions without calling 50 complete features a missing-input error', () => {
    const original = JSON.stringify(diagnostics)
    render(<PredictionInputNotice diagnostics={diagnostics} />)
    expect(screen.getByRole('status')).toHaveTextContent('参考運用：発走前オッズ・未知期間の検証待ち')
    expect(screen.getByText('特徴量 50/50 · 未計算 0件')).toBeInTheDocument()
    expect(screen.getByRole('status')).not.toHaveTextContent('入力に不足あり')
    expect(screen.getByRole('status')).toHaveTextContent('購入判断未承認')
    expect(screen.getByRole('status')).toHaveTextContent('買い目の根拠には使用しません')
    expect(JSON.stringify(diagnostics)).toBe(original)
  })

  it('still shows actual missing inputs instead of declaring all reference features complete', () => {
    render(<PredictionInputNotice diagnostics={{ ...diagnostics,
      status: 'degraded', actual_count: 49, missing_columns: ['market_log_probability'] }} />)
    expect(screen.getByRole('status')).toHaveTextContent('入力に不足あり · 参考予測')
    expect(screen.getByText('特徴量 49/50 · 未計算 1件')).toBeInTheDocument()
    expect(screen.getByRole('status')).not.toHaveTextContent('参考運用：発走前オッズ・未知期間の検証待ち')
  })

  it('never whitelists the reference profile or authorizes marked reference evidence', () => {
    expect(sharedFeatureProfileCount(PIPELINE)).toBeNull()
    expect(predictionInputVerified(diagnostics)).toBe(false)
    const otherwiseValid: PredictionInputDiagnostics = { ...diagnostics, status: 'aligned',
      semantic_parity_verified: true, live_capture_hash_bound: true, issue_codes: [] }
    expect(predictionInputVerified(otherwiseValid)).toBe(false)
    const legacyShape = { ...otherwiseValid, feature_pipeline: undefined }
    expect(predictionInputVerified(legacyShape)).toBe(false)
  })

  it('uses the named reference pipeline in prediction selectors without relabeling it validated', () => {
    const model = { model_id: 'model_win_reference_nll46_2026_20261012',
      feature_pipeline: PIPELINE, target: 'win', is_active: true, is_local_candidate: true,
      display_name: '全履歴・勝率NLL（参考運用）' }
    expect(formatModelOptionLabel(model)).toBe('全履歴・勝率NLL（参考運用）')
    expect(formatModelOptionLabel({ ...model, display_name: null })).toBe('全履歴・勝率NLL（参考運用）')
  })
})
