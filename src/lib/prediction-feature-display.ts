import type { FeatureContribution } from '@/lib/race-analysis-types'

const labels: Record<string, string> = {
  safe_distance: '今回の距離', safe_num_horses: '出走頭数', safe_horse_number: '馬番', safe_bracket_number: '枠番',
  safe_age: '年齢', safe_burden_weight: '斤量', safe_horse_weight: '馬体重', safe_weight_change: '馬体重の増減',
  safe_month: '開催月', safe_race_number: 'レース番号', safe_surface: 'コース（芝・ダート）', safe_venue: '競馬場',
  safe_sex: '性別', safe_field_condition: '馬場状態', safe_weather: '天候', safe_race_class: 'レースのクラス（競走条件）',
  safe_prior_starts: '過去の出走数', safe_days_since_last: '前走からの日数',
  safe_prev_finish: '前走の着順', safe_prev_time_seconds: '前走の走破タイム', safe_prev_distance: '前走の距離', safe_prev_speed_mps: '前走の平均速度',
  safe_prev2_finish: '2走前の着順', safe_prev2_time_seconds: '2走前の走破タイム', safe_prev2_distance: '2走前の距離', safe_prev2_speed_mps: '2走前の平均速度',
  safe_distance_change: '前走からの距離変更',
  ctx_condition_starts: '同じ馬場状態での過去出走数', ctx_condition_rank_mean: '同じ馬場状態での平均着順',
  ctx_condition_speed_delta_mean: '同じ馬場状態での平均速度差', ctx_course_starts: '同じ競馬場・コースの過去出走数',
  ctx_course_rank_mean: '同じ競馬場での平均着順', ctx_course_speed_delta_mean: '同じ競馬場での平均速度差',
  ctx_surface_changed: '前走からの芝・ダート変更', ctx_distance_change_ratio: '前走からの距離変更率',
  ctx_weight_history_count5: '直近5走の馬体重記録数', ctx_weight_delta_last: '前走時からの馬体重差',
  ctx_weight_delta_mean5: '直近5走の平均馬体重との差', ctx_weight_std5: '直近5走の馬体重のばらつき',
  ctx_weight_change_pct: '今回の馬体重増減率', ctx_weight_trend5: '直近5走の馬体重傾向',
  ctx_sire_surface_starts: '父の産駒・同コースの記録数', ctx_sire_surface_rank_mean: '父の産駒・同コースの平均着順',
  ctx_sire_condition_starts: '父の産駒・同馬場状態の記録数', ctx_sire_condition_rank_mean: '父の産駒・同馬場状態の平均着順',
  ctx_damsire_surface_starts: '母父の産駒・同コースの記録数', ctx_damsire_surface_rank_mean: '母父の産駒・同コースの平均着順',
  ctx_pedigree_verified_prior: '過去データで確認できた血統情報', ctx_gate_fraction: '出走頭数に対する馬番の位置',
  ctx_gate_prior_count: '同条件・同じ馬番帯の記録数', ctx_gate_rank_mean: '同条件・同じ馬番帯の平均着順',
  ctx_gate_rank_delta: '同条件全体と比べた馬番帯の成績差', ctx_style_count5: '直近5走の通過順位記録数',
  ctx_early_position_mean5: '直近5走の前半通過順位', ctx_early_position_std5: '直近5走の前半通過順位のばらつき',
  ctx_front_rate5: '直近5走の前方通過割合', ctx_late_gain_mean5: '直近5走の後半順位改善幅',
  ctx_field_front_pressure: '出走馬全体の前方傾向', ctx_other_front_pressure: '他馬の前方傾向',
  ctx_field_style_coverage: '出走馬の通過順位データ割合', ctx_style_pressure_interaction: '自馬と他馬の前方傾向の組み合わせ',
}

const windowLabels: Record<string, string> = {
  count: '記録数', observed_coverage: '記録割合', avg_finish: '平均着順', avg_speed_mps: '平均速度',
  avg_last3f_time: '平均上がり3F', win_rate: '勝率', top3_rate: '3着以内率',
}
for (const window of [3, 5, 10]) {
  for (const [suffix, label] of Object.entries(windowLabels)) labels[`safe_past${window}_${suffix}`] = `直近${window}走の${label}`
}
for (const entity of ['jockey', 'trainer', 'sire', 'damsire']) {
  const prefix = entity === 'jockey' ? '騎手' : entity === 'trainer' ? '調教師' : entity === 'sire' ? '父' : '母父'
  labels[`safe_${entity}_starts`] = `${prefix}の記録数`
  labels[`safe_${entity}_win_rate`] = `${prefix}の勝率`
  labels[`safe_${entity}_top3_rate`] = `${prefix}の3着以内率`
}
for (const group of ['horse_surface', 'horse_distance_band', 'horse_course']) {
  const context = group === 'horse_surface' ? '同じコース' : group === 'horse_distance_band' ? '同じ距離帯' : '同じ競馬場・コース'
  labels[`safe_${group}_starts`] = `${context}での過去出走数`
  labels[`safe_${group}_win_rate`] = `${context}での勝率`
  labels[`safe_${group}_top3_rate`] = `${context}での3着以内率`
  labels[`safe_${group}_avg_speed_mps`] = `${context}での平均速度`
}

const japanese = /[ぁ-んァ-ヶ一-龠]/

export function predictionFeatureLabel(feature: FeatureContribution): string {
  if (labels[feature.feature]) return labels[feature.feature]
  if (japanese.test(feature.label)) return feature.label
  if (japanese.test(feature.description)) return feature.description.split(/[（(。]/, 1)[0].trim() || 'その他の特徴量'
  return 'その他の特徴量'
}

export function predictionFeatureValue(feature: FeatureContribution): string {
  const value = feature.value
  if (feature.feature === 'safe_race_class' && typeof value === 'number') return 'クラス名を確認できません'
  if (value === null || value === undefined || value === '') return '未取得'
  if (typeof value === 'boolean') return value ? 'はい' : 'いいえ'
  if (typeof value !== 'number' || !Number.isFinite(value)) return String(value)
  const number = value.toLocaleString('ja-JP', { maximumFractionDigits: 3 })
  if (/(?:win_rate|top3_rate|observed_coverage|_fraction|_ratio|_pct)$/.test(feature.feature) && Math.abs(value) <= 1) {
    return `${(value * 100).toFixed(1)}%`
  }
  if (/(?:avg_)?finish$/.test(feature.feature) || /_rank_(?:mean|delta)$/.test(feature.feature)) return `${number}着`
  if (/(?:speed_mps|speed_delta_mean)$/.test(feature.feature)) return `${number}m/s`
  if (/(?:time_seconds|last3f_time)$/.test(feature.feature)) return `${number}秒`
  if (/(?:distance)$/.test(feature.feature)) return `${number}m`
  if (/(?:weight|burden_weight)/.test(feature.feature)) return `${number}kg`
  if (/(?:starts|_count\d*|_count)$/.test(feature.feature)) return `${number}走`
  if (/days_since/.test(feature.feature)) return `${number}日`
  return number
}

export function predictionScoreContribution(value: number): string {
  const magnitude = Math.abs(value)
  const precision = magnitude > 0 && magnitude < 0.001 ? 5 : 3
  return `${value > 0 ? '+' : ''}${value.toFixed(precision)}`
}
