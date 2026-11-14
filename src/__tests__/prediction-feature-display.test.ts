import { describe, expect, it } from 'vitest'
import { predictionFeatureLabel, predictionFeatureValue, predictionScoreContribution } from '@/lib/prediction-feature-display'
import type { FeatureContribution } from '@/lib/race-analysis-types'

function feature(featureName: string, value: FeatureContribution['value'], label = featureName): FeatureContribution {
  return { feature: featureName, label, description: '', value, contribution: 0, impact_pct: 0, direction: 'positive' }
}

describe('prediction feature display', () => {
  it.each([
    ['safe_prev_finish', 'safe prev finish', '前走の着順'],
    ['safe_past10_avg_finish', 'safe past10 avg finish', '直近10走の平均着順'],
    ['safe_field_condition', 'safe field condition', '馬場状態'],
    ['safe_race_class', 'safe race class', 'レースのクラス（競走条件）'],
  ])('translates %s even if the server label is an internal key', (key, rawLabel, expected) => {
    expect(predictionFeatureLabel(feature(key, 3, rawLabel))).toBe(expected)
  })

  it('formats observed values in racing units and signed score contributions', () => {
    expect(predictionFeatureValue(feature('safe_prev_finish', 3))).toBe('3着')
    expect(predictionFeatureValue(feature('safe_jockey_win_rate', 0.15))).toBe('15.0%')
    expect(predictionFeatureValue(feature('safe_prev_speed_mps', 16.25))).toBe('16.25m/s')
    expect(predictionScoreContribution(0.1254)).toBe('+0.125')
    expect(predictionScoreContribution(-0.17)).toBe('-0.170')
  })

  it('never presents an undecoded race-class category number as a class name', () => {
    expect(predictionFeatureValue(feature('safe_race_class', 10))).toBe('クラス名を確認できません')
    expect(predictionFeatureValue(feature('safe_race_class', '3勝クラス'))).toBe('3勝クラス')
  })
})
