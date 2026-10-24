import type { FeatureContribution, Prediction } from '@/lib/race-analysis-types'

export type NarrativeSource = 'openai' | 'gemini' | 'fallback'

export type NarrativeResponse = {
  explanation: string
  source: NarrativeSource
}

function featureNames(features: FeatureContribution[], direction: FeatureContribution['direction'], limit: number) {
  return features
    .filter(feature => feature.direction === direction)
    .slice(0, limit)
    .map(feature => `「${feature.label}」`)
}

export function buildLocalPredictionExplanation(prediction: Prediction): string {
  const features = prediction.explanation?.features ?? []
  const positive = featureNames(features, 'positive', 2)
  const negative = featureNames(features, 'negative', 1)
  const parts = [`AIは${prediction.horse_name}を${prediction.predicted_rank}位と予測しました。`]

  if (positive.length > 0) parts.push(`${positive.join('・')}が速度スコアを上げています。`)
  if (negative.length > 0) parts.push(`${negative.join('・')}は速度スコアを下げています。`)
  if (positive.length === 0 && negative.length === 0) {
    parts.push('この予測には説明データがありません。')
  }
  return parts.join('')
}

export function narrativeCacheKey(raceId: string, modelId: string | null | undefined, horseNumber: number) {
  return `prediction-narrative:${raceId}:${modelId || 'default'}:${horseNumber}`
}
