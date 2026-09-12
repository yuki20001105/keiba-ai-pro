import { fireEvent, render, screen, waitFor } from '@testing-library/react'
import { beforeEach, describe, expect, test, vi } from 'vitest'
import { RaceExplanationPanel } from '@/components/RaceExplanationPanel'
import type { RacePredictResult } from '@/lib/race-analysis-types'
import { authFetch } from '@/lib/auth-fetch'

vi.mock('@/lib/auth-fetch', () => ({ authFetch: vi.fn() }))

const result: RacePredictResult = {
  success: true,
  model_id: 'model-1',
  explanation_method: 'tree_shap',
  race_info: {
    race_id: '202604070101',
    race_name: 'テストレース',
    venue: '東京',
    date: '20260407',
    distance: 1600,
    track_type: '芝',
    num_horses: 1,
  },
  predictions: [{
    horse_number: 1,
    horse_name: 'テスト馬A',
    jockey_name: '騎手A',
    trainer_name: '',
    sex: '牡',
    age: 4,
    horse_weight: 480,
    odds: 3.2,
    popularity: 1,
    win_probability: 0.4,
    p_raw: 0.35,
    p_norm: 0.4,
    expected_value: 1.28,
    predicted_rank: 1,
    explanation: {
      method: 'tree_shap',
      base_value: 0.1,
      features: [
        {
          feature: 'jockey_win_rate',
          label: '騎手の勝率',
          description: '騎手の勝率',
          value: 0.15,
          contribution: 0.2,
          impact_pct: 40,
          direction: 'positive',
        },
        {
          feature: 'horse_weight_change',
          label: '馬体重変化',
          description: '馬体重変化',
          value: -8,
          contribution: -0.1,
          impact_pct: 20,
          direction: 'negative',
        },
      ],
    },
  }],
  recommendation: null,
  best_bet_type: null,
  pro_evaluation: null,
}

describe('RaceExplanationPanel', () => {
  beforeEach(() => {
    localStorage.clear()
    vi.clearAllMocks()
  })

  test('shows contribution bars without calling the LLM endpoint', () => {
    render(<RaceExplanationPanel result={result} />)

    expect(screen.getByText('速度スコアを上げた要素')).toBeInTheDocument()
    expect(screen.getByText('速度スコアを下げた要素')).toBeInTheDocument()
    expect(screen.getByText('速度スコアへの寄与')).toBeInTheDocument()
    expect(screen.getAllByText('騎手の勝率').length).toBeGreaterThan(0)
    expect(authFetch).not.toHaveBeenCalled()
  })

  test('calls the narrative endpoint only after the user asks', async () => {
    vi.mocked(authFetch).mockResolvedValue(new Response(JSON.stringify({
      explanation: '騎手の勝率が評価を押し上げています。',
      source: 'openai',
    }), { status: 200, headers: { 'Content-Type': 'application/json' } }))

    render(<RaceExplanationPanel result={result} />)
    fireEvent.click(screen.getByRole('button', { name: '文章で解説' }))

    await waitFor(() => expect(authFetch).toHaveBeenCalledTimes(1))
    expect(await screen.findByText('LLM解説')).toBeInTheDocument()
    expect(screen.getByText('騎手の勝率が評価を押し上げています。')).toBeInTheDocument()
  })
})
