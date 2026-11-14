import { describe, expect, test } from 'vitest'
import { buildLocalPredictionExplanation, buildRaceNarrativeFallback, buildRaceNarrativeInput, narrativeCacheKey } from '@/lib/prediction-explanation'
import type { Prediction, RacePredictResult } from '@/lib/race-analysis-types'

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

  test('prepares race conditions, model comparisons, score reasons, and only recorded pace evidence', () => {
    const selected = structuredClone(prediction)
    selected.explanation!.features.push(
      { feature: 'safe_race_class', label: 'safe race class', description: '', value: '1勝クラス', contribution: 0.04, impact_pct: 8, direction: 'positive' },
      { feature: 'ctx_front_rate5', label: '過去の前方通過割合', description: '', value: 0.6, contribution: 0.03, impact_pct: 6, direction: 'positive' },
      { feature: 'ctx_style_count5', label: '過去通過順の記録数', description: '', value: 5, contribution: 0.01, impact_pct: 2, direction: 'positive' },
      { feature: 'ctx_field_front_pressure', label: '今回の出走馬の前方傾向', description: '', value: 0.45, contribution: 0.01, impact_pct: 2, direction: 'positive' },
      { feature: 'ctx_field_style_coverage', label: '過去通過順の確認率', description: '', value: 0.8, contribution: 0.01, impact_pct: 2, direction: 'positive' },
    )
    const second = { ...structuredClone(selected), horse_number: 2, horse_name: '比較馬', predicted_rank: 2, p_raw: 0.2 }
    const result: RacePredictResult = {
      success: true, model_id: 'model-1', race_info: { race_id: 'race-1', race_name: '条件戦', venue: '東京', date: '20260407', distance: 1600, track_type: '芝', num_horses: 12, weather: '晴', field_condition: '良' },
      predictions: [second, selected], recommendation: null, best_bet_type: null, pro_evaluation: null,
    }
    const input = buildRaceNarrativeInput(result, selected)
    expect(input.race_context).toMatchObject({ venue: '東京', track_type: '芝', distance: 1600, starters: 12, weather: '晴', field_condition: '良', race_class: '1勝クラス' })
    expect(input.selected_horse.features[0]).toMatchObject({ label: '騎手の勝率', value: '18.0%', direction: 'positive', contribution: 0.2 })
    expect(input.field_comparison.map(horse => horse.horse_name)).toEqual(['テストホース', '比較馬'])
    expect(input.pace_evidence).toMatchObject({ field_front_rate: 0.45, style_coverage: 0.8 })
    expect(input.pace_evidence.runners).toEqual([
      { horse_number: 6, horse_name: 'テストホース', predicted_rank: 1, recent_front_rate: 0.6, observed_runs: 5 },
      { horse_number: 2, horse_name: '比較馬', predicted_rank: 2, recent_front_rate: 0.6, observed_runs: 5 },
    ])
    const narrative = buildRaceNarrativeFallback(input)
    expect(narrative).toContain('【レース条件】東京・芝・1600m・1勝クラス・馬場良・天候晴・12頭')
    expect(narrative).toContain('【AIの比較】テストホースは1位')
    expect(narrative).toContain('騎手の勝率（今回値 18.0%）')
    expect(narrative).toContain('出走馬の80%')
    expect(narrative).toContain('テストホースは60%で、出走馬平均より高く')
    expect(narrative).toContain('今回の隊列やペースを確定するものではありません')
  })

  test('does not invent pace when historical passing-order evidence is missing', () => {
    const selected = structuredClone(prediction)
    selected.explanation!.features = selected.explanation!.features.filter(feature => !feature.feature.startsWith('ctx_'))
    const result: RacePredictResult = {
      success: true, model_id: 'model-1', race_info: { race_id: 'race-1', race_name: '条件戦', venue: '東京', date: '20260407', distance: 1600, track_type: '芝', num_horses: 12 },
      predictions: [selected], recommendation: null, best_bet_type: null, pro_evaluation: null,
    }
    const narrative = buildRaceNarrativeFallback(buildRaceNarrativeInput(result, selected))
    expect(narrative).toContain('馬場状態・天候は未確認です')
    expect(narrative).toContain('具体的な隊列やペースは判断できません')
    expect(narrative).not.toContain('先行争い')
  })
})
