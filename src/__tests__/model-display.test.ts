import { describe, expect, it } from 'vitest'
import { formatModelCreatedAt, formatModelOptionLabel } from '@/lib/model-display'

const NEW_MODEL = {
  model_id: 'model_speed_deviation_lightgbm_20180106_20260706_20260912_2159_02101032',
  target: 'speed_deviation',
  created_at: '20260912_2159',
  training_date_from: '20180106',
  training_date_to: '20260706',
  auc: 0.759527,
}

describe('model display names', () => {
  it('shows a compact creation time from the bundle metadata', () => {
    expect(formatModelCreatedAt(NEW_MODEL)).toBe('2026/09/12 21:59')
  })

  it('falls back to the model ID creation segment', () => {
    expect(formatModelCreatedAt({ ...NEW_MODEL, created_at: 'unknown' })).toBe('2026/09/12 21:59')
  })

  it('builds a short but identifiable prediction option', () => {
    expect(formatModelOptionLabel(NEW_MODEL)).toBe(
      '速度偏差｜2026/09/12 21:59｜学習 2018/01–2026/07｜相関 0.760',
    )
  })

  it('labels the context profile as a comparison candidate, not an approved strategy', () => {
    expect(formatModelOptionLabel({ ...NEW_MODEL, feature_pipeline: 'local-db-context-v1' })).toBe(
      '条件・展開80（比較候補）｜2026/09/12 21:59｜学習 2018/01–2026/07',
    )
  })

  it('warns when the model feature source and probability validation are not ready', () => {
    expect(formatModelOptionLabel({
      ...NEW_MODEL,
      prediction_readiness: {
        feature_compatibility: 'mismatch',
        win_probability_validated: false,
        eligible_bet_types: [],
        model_betting_ready: false,
      },
    })).toContain('特徴量互換性未確認・参考予測')
  })

  it('shows validated bet types without implying that live odds are ready', () => {
    const label = formatModelOptionLabel({
      ...NEW_MODEL,
      prediction_readiness: {
        feature_compatibility: 'verified',
        win_probability_validated: true,
        eligible_bet_types: ['単勝'],
        model_betting_ready: true,
      },
    })
    expect(label).toContain('勝率検証済・単勝')
    expect(label).toContain('収益性未検証')
    expect(label).toContain('発走前オッズ確認が別途必要')
  })
})
