import type { FeatureContribution, Prediction, RacePredictResult } from '@/lib/race-analysis-types'
import { predictionFeatureLabel, predictionFeatureValue } from '@/lib/prediction-feature-display'

export type NarrativeSource = 'openai' | 'gemini' | 'fallback'

export type NarrativeResponse = {
  explanation: string
  source: NarrativeSource
}

export type RaceNarrativeInput = {
  race_context: {
    race_name: string
    venue: string
    track_type: string
    distance: number | null
    starters: number | null
    weather: string | null
    field_condition: string | null
    race_class: string | null
  }
  selected_horse: {
    horse_number: number
    horse_name: string
    predicted_rank: number
    speed_score: number | null
    features: Array<{
      feature: string
      label: string
      direction: 'positive' | 'negative'
      value: string
      contribution: number
      impact_pct: number
    }>
  }
  field_comparison: Array<{
    horse_number: number
    horse_name: string
    predicted_rank: number
    speed_score: number | null
  }>
  pace_evidence: {
    field_front_rate: number | null
    style_coverage: number | null
    runners: Array<{
      horse_number: number
      horse_name: string
      predicted_rank: number
      recent_front_rate: number
      observed_runs: number | null
    }>
  }
}

function featureByName(prediction: Prediction, name: string): FeatureContribution | undefined {
  return prediction.explanation?.features.find(feature => feature.feature === name)
}

function numericFeature(prediction: Prediction, name: string): number | null {
  const value = featureByName(prediction, name)?.value
  return typeof value === 'number' && Number.isFinite(value) ? value : null
}

export function buildRaceNarrativeInput(result: RacePredictResult, prediction: Prediction): RaceNarrativeInput {
  const ranked = [...result.predictions].sort((left, right) => left.predicted_rank - right.predicted_rank)
  const raceClass = ranked.map(horse => featureByName(horse, 'safe_race_class')?.value)
    .find((value): value is string => typeof value === 'string' && value !== 'クラス名を確認できません') ?? null
  const runners = ranked.flatMap(horse => {
    const frontRate = numericFeature(horse, 'ctx_front_rate5')
    if (frontRate === null || frontRate < 0 || frontRate > 1) return []
    const count = numericFeature(horse, 'ctx_style_count5')
    return [{ horse_number: horse.horse_number, horse_name: horse.horse_name,
      predicted_rank: horse.predicted_rank, recent_front_rate: frontRate,
      observed_runs: count === null ? null : Math.max(0, Math.round(count)) }]
  }).slice(0, 8)
  const firstAvailable = (name: string) => ranked
    .map(horse => numericFeature(horse, name))
    .find((value): value is number => value !== null && value >= 0 && value <= 1) ?? null
  return {
    race_context: {
      race_name: result.race_info.race_name || '', venue: result.race_info.venue || '',
      track_type: result.race_info.track_type || '',
      distance: Number.isFinite(result.race_info.distance) ? result.race_info.distance : null,
      starters: Number.isFinite(result.race_info.num_horses) ? result.race_info.num_horses : null,
      weather: result.race_info.weather || null, field_condition: result.race_info.field_condition || null,
      race_class: raceClass,
    },
    selected_horse: {
      horse_number: prediction.horse_number, horse_name: prediction.horse_name,
      predicted_rank: prediction.predicted_rank,
      speed_score: Number.isFinite(prediction.p_raw) ? prediction.p_raw : null,
      features: (prediction.explanation?.features ?? []).slice(0, 6).map(feature => ({
        feature: feature.feature, label: predictionFeatureLabel(feature), direction: feature.direction,
        value: predictionFeatureValue(feature), contribution: feature.contribution, impact_pct: feature.impact_pct,
      })),
    },
    field_comparison: ranked.slice(0, 6).map(horse => ({
      horse_number: horse.horse_number, horse_name: horse.horse_name,
      predicted_rank: horse.predicted_rank, speed_score: Number.isFinite(horse.p_raw) ? horse.p_raw : null,
    })),
    pace_evidence: {
      field_front_rate: firstAvailable('ctx_field_front_pressure'),
      style_coverage: firstAvailable('ctx_field_style_coverage'),
      runners,
    },
  }
}

function formatNarrativeFeatures(
  features: RaceNarrativeInput['selected_horse']['features'],
  direction: 'positive' | 'negative',
  limit: number,
): string {
  return features
    .filter(feature => feature.direction === direction)
    .slice(0, limit)
    .map(feature => {
      const value = feature.value && feature.value !== '未取得' ? `（今回値 ${feature.value}）` : ''
      const sign = feature.contribution > 0 ? '+' : ''
      return `${feature.label}${value}：スコア寄与 ${sign}${feature.contribution.toFixed(Math.abs(feature.contribution) < 0.001 ? 5 : 3)}、寄与割合 ${feature.impact_pct.toFixed(0)}%`
    })
    .join('、')
}

/** Deterministic explanation used when no text-generation provider is configured. */
export function buildRaceNarrativeFallback(input: RaceNarrativeInput): string {
  const { race_context: race, selected_horse: selected, field_comparison: field, pace_evidence: pace } = input
  const raceLabel = race.race_class || race.race_name || null
  const conditions = [race.venue, race.track_type, race.distance ? `${race.distance}m` : null,
    raceLabel, race.field_condition ? `馬場${race.field_condition}` : null,
    race.weather ? `天候${race.weather}` : null, race.starters ? `${race.starters}頭` : null].filter(Boolean)
  const unknownConditions = [!race.field_condition ? '馬場状態' : null, !race.weather ? '天候' : null].filter(Boolean)
  const comparison = field.filter(horse => horse.horse_number !== selected.horse_number).slice(0, 2)
    .map(horse => `${horse.predicted_rank}位 ${horse.horse_name}${horse.speed_score === null ? '' : `（速度スコア ${horse.speed_score.toFixed(2)}）`}`).join('・')
  const positive = formatNarrativeFeatures(selected.features, 'positive', 2)
  const negative = formatNarrativeFeatures(selected.features, 'negative', 1)
  const reasons = [positive && `上げた要素：${positive}`, negative && `下げた要素：${negative}`].filter(Boolean).join('。')

  const selectedStyle = pace.runners.find(runner => runner.horse_number === selected.horse_number)
  const coverage = pace.style_coverage === null ? '' : `過去の通過順を確認できた馬は出走馬の${(pace.style_coverage * 100).toFixed(0)}%です。`
  let paceText: string
  if (pace.field_front_rate !== null) {
    const fieldRate = pace.field_front_rate
    const selectedText = selectedStyle
      ? `${selected.horse_name}は${(selectedStyle.recent_front_rate * 100).toFixed(0)}%で、出走馬平均より${selectedStyle.recent_front_rate > fieldRate ? '高く' : selectedStyle.recent_front_rate < fieldRate ? '低く' : '同程度'}、過去の位置取りは${selectedStyle.recent_front_rate > fieldRate ? '前寄り' : selectedStyle.recent_front_rate < fieldRate ? '後ろ寄り' : '平均的'}でした。`
      : ''
    paceText = `${coverage}過去走データのある馬では、前半に前方3分の1を通過した割合が平均${(fieldRate * 100).toFixed(0)}%です。${selectedText}これは過去傾向で、今回の隊列やペースを確定するものではありません。`
  } else if (selectedStyle) {
    paceText = `${selected.horse_name}は直近の通過順がある${selectedStyle.observed_runs ?? '複数'}走中、前半に前方3分の1を通過した割合が${(selectedStyle.recent_front_rate * 100).toFixed(0)}%です。他馬との全体比較ができず、今回の隊列やペースは判断できません。`
  } else {
    paceText = '今回の予測には通過順・脚質の根拠が含まれず、具体的な隊列やペースは判断できません。'
  }

  return [
    `【レース条件】${conditions.length ? conditions.join('・') : '取得できた条件情報はありません'}。${unknownConditions.length ? `${unknownConditions.join('・')}は未確認です。` : ''}`,
    `【AIの比較】${selected.horse_name}は${selected.predicted_rank}位${selected.speed_score === null ? '' : `（速度スコア ${selected.speed_score.toFixed(2)}）`}${comparison ? `。上位比較：${comparison}` : '。'} 順位は速度スコアの比較で、勝率ではありません。`,
    reasons ? `【評価材料】${reasons}。特徴量の寄与はモデル内の評価材料で、因果関係や好走を保証しません。` : '【評価材料】寄与データが不足しています。',
    `【展開材料】${paceText}`,
  ].join('\n')
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
