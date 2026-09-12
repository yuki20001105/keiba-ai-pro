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
})
