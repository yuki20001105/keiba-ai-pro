import { render, screen } from '@testing-library/react'
import { describe, expect, test } from 'vitest'
import { ModelEvaluationSummary } from '@/components/ModelEvaluationSummary'

describe('ModelEvaluationSummary', () => {
  test('shows only the five primary metrics before the details', () => {
    render(<ModelEvaluationSummary target="speed_deviation" evaluation={{
      primary: {
        rank_correlation: 0.7595,
        rmse: 0.6754,
        top_pick_win_rate: 0.355,
        top_pick_win_rate_delta: -0.055,
        win_roi: 91.2,
      },
      details: {
        mae: 0.5119,
        r2: 0.5731,
        evaluation_date_from: '2025-07-19',
        evaluation_date_to: '2026-07-06',
        evaluation_race_count: 2058,
      },
      time_slices: [{ period: '2026', race_count: 1000, top_pick_win_rate: 0.36, top_pick_win_rate_delta: -0.04, win_roi: 93 }],
    }} />)

    expect(screen.getByText('順位相関')).toBeInTheDocument()
    expect(screen.getByText('予測誤差')).toBeInTheDocument()
    expect(screen.getByText('1位推奨の勝率')).toBeInTheDocument()
    expect(screen.getByText('人気1位との差')).toBeInTheDocument()
    expect(screen.getAllByText('回収率')).toHaveLength(2)
    expect(screen.getByText('-5.5pt')).toBeInTheDocument()
    expect(screen.getByText('詳細')).toBeInTheDocument()
    expect(screen.getByText('2025-07-19〜2026-07-06')).toBeInTheDocument()
  })

  test('does not present missing return data as zero', () => {
    render(<ModelEvaluationSummary target="speed_deviation" legacyAuc={0.7} legacyLogloss={0.8} />)
    expect(screen.getAllByText('未計測').length).toBeGreaterThan(0)
  })
})
