import { describe, expect, test } from 'vitest'
import { buildLocalPredictionExplanation, narrativeCacheKey } from '@/lib/prediction-explanation'
import type { Prediction } from '@/lib/race-analysis-types'

const prediction: Prediction = {
  horse_number: 6,
  horse_name: 'テストホース',
  jockey_name: 'テスト騎手',
  trainer_name: '',
  sex: '牡',
  age: 4,
  horse_weight: 480,
  odds: 3.2,
  popularity: 1,
  win_probability: 0.3,
  p_raw: 0.4,
  p_norm: 0.3,
  expected_value: 0.96,
  predicted_rank: 1,
  explanation: {
    method: 'tree_shap',
    base_value: 0.1,
    features: [
      {
        feature: 'jockey_win_rate',
        label: '騎手の勝率',
        description: '騎手の勝率',
        value: 0.18,
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
}

describe('prediction explanation', () => {
  test('builds a short explanation from actual contribution directions', () => {
    expect(buildLocalPredictionExplanation(prediction)).toBe(
      'AIはテストホースを1位と予測しました。「騎手の勝率」が速度スコアを上げています。「馬体重変化」は速度スコアを下げています。',
    )
  })

  test('scopes cached LLM text by race, model, and horse', () => {
    expect(narrativeCacheKey('202604070101', 'model-1', 6)).toBe(
      'prediction-narrative:202604070101:model-1:6',
    )
  })
})
