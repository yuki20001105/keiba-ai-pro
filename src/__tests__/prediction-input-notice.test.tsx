import { cleanup, render, screen } from '@testing-library/react'
import { afterEach, describe, expect, it } from 'vitest'
import { PredictionInputNotice } from '@/components/PredictionInputNotice'
import type { PredictionInputDiagnostics } from '@/lib/race-analysis-types'
import { predictionInputVerified, sharedFeatureProfileCount, sharedInputFormulaMatchedCaptureUnverified } from '@/lib/prediction-input-contract'

afterEach(cleanup)
const base: PredictionInputDiagnostics = {
  schema: 'prediction-input-contract-v1', status: 'aligned', semantic_parity_verified: true,
  expected_count: 3, actual_count: 3, missing_columns: [], restored_columns: [],
  blocked_columns: [], all_missing_columns: [], source_hash_matches: true,
  history_available: true, issue_codes: [],
}
const shared: PredictionInputDiagnostics = {
  ...base, feature_pipeline: 'local-db-shared-v1', status: 'unverified',
  expected_count: 47, actual_count: 47, live_capture_hash_bound: false,
  issue_codes: ['pre_race_db_capture_unverified'],
}
describe('served input notice', () => {
  it.each(['aligned', 'unverified'] as const)('keeps the complete context candidate separate from approved inputs: %s', status => {
    const diagnostic = { ...shared, feature_pipeline: 'local-db-context-v1', expected_count: 80,
      actual_count: 80, reference_only: true, status, live_capture_hash_bound: status === 'aligned',
      issue_codes: status === 'aligned' ? [] : ['pre_race_db_capture_unverified'] }
    expect(sharedFeatureProfileCount(diagnostic.feature_pipeline)).toBe(80)
    expect(predictionInputVerified(diagnostic)).toBe(false)
    render(<PredictionInputNotice diagnostics={diagnostic} />)
    expect(screen.getByRole('status')).toHaveTextContent('条件・展開の比較候補 · 購入判断未承認')
    expect(screen.getByRole('status')).toHaveTextContent('特徴量 80/80 · 未計算 0件')
    expect(screen.getByRole('status')).toHaveTextContent('校正値を購入判断の根拠には使用しません')
  })
  it.each([
    ['local-db-shared-v1', 47], ['local-db-shared-optimized-v1', 46],
  ] as const)('preserves capture-only reference status for the fixed %s profile', (pipeline, count) => {
    const diagnostics = { ...shared, feature_pipeline: pipeline, expected_count: count, actual_count: count }
    expect(sharedFeatureProfileCount(pipeline)).toBe(count)
    expect(sharedInputFormulaMatchedCaptureUnverified(diagnostics)).toBe(true)
    expect(predictionInputVerified(diagnostics)).toBe(false)
    render(<PredictionInputNotice diagnostics={diagnostics} />)
    expect(screen.getByRole('status')).toHaveTextContent('入力式は一致 · 取得時点未確認 · 参考予測')
    expect(screen.getByText(`特徴量 ${count}/${count} · 未計算 0件`)).toBeInTheDocument()
    expect(screen.getByText(/買い目の根拠には使用しません/)).toBeInTheDocument()
  })

  it.each([
    ['local-db-shared-v1', 46], ['local-db-shared-optimized-v1', 47],
    ['local-db-shared-optimized-v1', 3], ['unknown-profile', 46],
  ] as const)('rejects unknown / mismatched profile evidence despite equal counts: %s / %s', (pipeline, count) => {
    const diagnostics = { ...shared, feature_pipeline: pipeline, expected_count: count, actual_count: count,
      scope: 'local-safe-candidate-input-v1' }
    expect(sharedInputFormulaMatchedCaptureUnverified(diagnostics)).toBe(false)
    expect(predictionInputVerified({ ...diagnostics, status: 'aligned', live_capture_hash_bound: true, issue_codes: [] })).toBe(false)
    render(<PredictionInputNotice diagnostics={diagnostics} />)
    expect(screen.getByRole('status')).not.toHaveTextContent('入力式は一致')
    expect(screen.getByRole('status')).toHaveTextContent('参考予測')
  })

  it.each([
    ['local-db-shared-v1', 47], ['local-db-shared-optimized-v1', 46],
  ] as const)('requires a bound live capture for aligned shared evidence: %s', (pipeline, count) => {
    const diagnostics = { ...base, feature_pipeline: pipeline, expected_count: count, actual_count: count,
      live_capture_hash_bound: true }
    expect(predictionInputVerified(diagnostics)).toBe(true)
    expect(predictionInputVerified({ ...diagnostics, live_capture_hash_bound: false })).toBe(false)
    const { live_capture_hash_bound: _capture, ...missingCapture } = diagnostics
    expect(predictionInputVerified(missingCapture)).toBe(false)
    render(<PredictionInputNotice diagnostics={diagnostics} />)
    expect(screen.queryByRole('status')).not.toBeInTheDocument()
  })

  it.each(['pre_race_db_capture_unverified', 'pre_race_db_capture_failed'])(
    'labels matching shared features separately from capture proof without granting EV eligibility: %s', reason => {
      const diagnostics = { ...shared, issue_codes: [reason] }
      const before = JSON.stringify(diagnostics)
      render(<PredictionInputNotice diagnostics={diagnostics} />)
      expect(screen.getByRole('status')).toHaveTextContent('入力式は一致 · 取得時点未確認 · 参考予測')
      expect(screen.getByText('特徴量 47/47 · 未計算 0件')).toBeInTheDocument()
      expect(screen.getByText(/発走前の取得証拠が未確認/)).toBeInTheDocument()
      expect(screen.queryByText(/勝率校正は未確認/)).not.toBeInTheDocument()
      expect(sharedInputFormulaMatchedCaptureUnverified(diagnostics)).toBe(true)
      expect(predictionInputVerified(diagnostics)).toBe(false)
      expect(JSON.stringify(diagnostics)).toBe(before)
    },
  )
  it.each([
    { feature_pipeline: undefined }, { feature_pipeline: 'legacy' }, { status: 'degraded' as const },
    { semantic_parity_verified: false }, { source_hash_matches: false }, { actual_count: 46 },
    { expected_count: 0, actual_count: 0 }, { missing_columns: ['old'] }, { blocked_columns: ['finish_time'] },
    { history_available: false }, { live_capture_hash_bound: true }, { issue_codes: [] },
    { issue_codes: ['pre_race_db_capture_unverified', 'target_not_after_training_evaluation_period'] },
    { issue_codes: ['unknown'] },
    { scope: 'local-safe-candidate-input-v1', issue_codes: ['unknown'] },
  ])('does not claim shared formula/capture-only parity for contradictory evidence: %o', change => {
    const diagnostics = { ...shared, ...change }
    expect(sharedInputFormulaMatchedCaptureUnverified(diagnostics)).toBe(false)
    expect(predictionInputVerified(diagnostics)).toBe(false)
    render(<PredictionInputNotice diagnostics={diagnostics} />)
    expect(screen.getByRole('status')).not.toHaveTextContent('入力式は一致')
    expect(screen.getByRole('status')).toHaveTextContent('参考予測')
  })
  it('distinguishes candidate formula parity from capture/calibration proof', () => {
    render(<PredictionInputNotice diagnostics={{ ...base, scope: 'local-safe-candidate-input-v1',
      status: 'unverified', live_capture_hash_bound: false, issue_codes: ['card_capture_provenance_unverified'] }} />)
    expect(screen.getByRole('status')).toHaveTextContent('入力式は一致 · 取得時点未確認 · 参考予測')
    expect(screen.getByText(/発走前の取得証拠と勝率校正は未確認/)).toBeInTheDocument()
  })
  it('does not clutter separately verified inputs or claim validated accuracy', () => {
    render(<PredictionInputNotice diagnostics={base} />)
    expect(screen.queryByRole('status')).not.toBeInTheDocument()
  })
  it('labels old responses as unverified without another prediction', () => {
    render(<PredictionInputNotice />)
    expect(screen.getByRole('status')).toHaveTextContent('入力互換性未確認 · 参考予測')
    expect(screen.getByText('入力の詳細').parentElement).not.toHaveAttribute('open')
  })
  it('keeps missing columns and source changes in folded detail', () => {
    render(<PredictionInputNotice diagnostics={{ ...base, status: 'degraded', missing_columns: ['legacy'],
      blocked_columns: ['finish_time'], source_hash_matches: false, history_available: false }} />)
    expect(screen.getByRole('status')).toHaveTextContent('入力に不足あり · 参考予測')
    expect(screen.getByText('未計算: legacy')).toBeInTheDocument()
    expect(screen.getByText('過去成績を読み込めませんでした。')).toBeInTheDocument()
    expect(screen.getByText('入力の詳細').parentElement).not.toHaveAttribute('open')
  })
  it('does not crash or hide the warning for malformed cached diagnostics', () => {
    render(<PredictionInputNotice diagnostics={{ schema: base.schema, status: 'aligned' } as PredictionInputDiagnostics} />)
    expect(screen.getByRole('status')).toHaveTextContent('入力互換性未確認 · 参考予測')
  })
  it.each([
    { semantic_parity_verified: false }, { actual_count: 4 }, { missing_columns: ['old'] },
    { blocked_columns: ['finish_time'] }, { source_hash_matches: false },
    { history_available: false }, { issue_codes: ['unknown'] },
  ])('does not hide contradictory or semantic-unverified aligned inputs: %o', change => {
    render(<PredictionInputNotice diagnostics={{ ...base, ...change }} />)
    expect(screen.getByRole('status')).toHaveTextContent('参考予測')
  })
})
