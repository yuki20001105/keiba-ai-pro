import type { PerformanceMode } from '@/lib/performance-records'

export type PaperOrigin = 'all' | 'historical' | 'pre_race' | 'historical_validation' | 'combined'
export type ModelRoiTotals = {
  total_cost: number
  total_return: number
  profit: number
  roi: number | null
  race_count: number
}
export type ModelRoiPoint = ModelRoiTotals & {
  date: string
  race_id: string
  post_time: string | null
}
export type ModelRoiSeries = ModelRoiTotals & {
  model_id: string
  display_name?: string
  color?: string
  cutoff_date?: string
  training_end_date?: string
  points: ModelRoiPoint[]
  status_counts?: Record<'settled' | 'pending' | 'payout_pending' | 'review' | 'skipped' | 'other', number>
  cohorts?: Record<'evaluation' | 'historical' | 'pre_race', ModelRoiTotals & { record_count: number; pending_races: number }>
}
export type ModelPerformance = {
  schema: 'model-cumulative-roi-v1' | 'model-cumulative-roi-v2'
  mode: PerformanceMode
  date_from: string | null
  date_to: string | null
  includes_full_filtered_set: true
  models: ModelRoiSeries[]
  origin?: PaperOrigin
  years?: string[]
}
export type ModelMonthlyPerformance = {
  schema: 'model-monthly-roi-v1'
  year: number
  models: { model_id: string; display_name: string; color: string; months: (ModelRoiTotals & { month: number })[] }[]
}
export type ModelRoiChartRow = {
  event: string
  date: string
  post_time: string | null
  values: Map<string, ModelRoiPoint>
}

const isObject = (value: unknown): value is Record<string, unknown> => value !== null && typeof value === 'object' && !Array.isArray(value)
const isCount = (value: unknown): value is number => typeof value === 'number' && Number.isSafeInteger(value) && value >= 0
const hasSimulationScope = (value: Record<string, unknown>, origin: PaperOrigin) => value.origin === origin
  && (origin === 'combined'
    ? value.source === 'combined-reference-simulation' && value.evaluation_scope === 'non-training-combined'
    : origin === 'historical_validation'
    ? value.source === 'historical-validation-simulation' && value.evaluation_scope === 'non-training-retrospective'
    : value.source === 'unseen-simulation')
const hasTotals = (value: Record<string, unknown>, allowEmpty = false) => isCount(value.total_cost)
  && isCount(value.total_return) && isCount(value.race_count)
  && typeof value.profit === 'number' && value.profit === value.total_return - value.total_cost
  && (value.total_cost > 0
    ? value.race_count > 0 && typeof value.roi === 'number' && Number.isFinite(value.roi)
      && Math.abs(value.roi - value.total_return / value.total_cost * 100) <= .051
    : allowEmpty && value.race_count === 0 && value.total_return === 0 && value.roi === null)

export function readModelPerformance(value: unknown, mode: PerformanceMode, dateFrom: string, dateTo: string, origin?: PaperOrigin): ModelPerformance | null {
  if (!isObject(value) || !['model-cumulative-roi-v1', 'model-cumulative-roi-v2'].includes(String(value.schema)) || value.mode !== mode
    || value.date_from !== (dateFrom || null) || value.date_to !== (dateTo || null)
    || value.includes_full_filtered_set !== true || !Array.isArray(value.models)) return null
  const daily = value.schema === 'model-cumulative-roi-v2'
  if (origin !== undefined && (!daily || !hasSimulationScope(value, origin))) return null
  if (daily && (value.point_granularity !== 'day' || !Array.isArray(value.years)
    || !value.years.every(year => typeof year === 'string' && /^\d{4}$/.test(year)))) return null
  const ids = new Set<string>()
  for (const model of value.models) {
    if (!isObject(model) || typeof model.model_id !== 'string' || ids.has(model.model_id)
      || !hasTotals(model, daily) || !Array.isArray(model.points)
      || (!daily && model.points.length !== model.race_count)) return null
    if (daily && (typeof model.color !== 'string' || !/^#[a-f\d]{6}$/i.test(model.color)
      || typeof model.display_name !== 'string' || !model.display_name
      || typeof model.cutoff_date !== 'string' || !/^\d{4}-\d{2}-\d{2}$/.test(model.cutoff_date))) return null
    ids.add(model.model_id)
    if (!model.points.every(point => isObject(point) && hasTotals(point)
      && typeof point.date === 'string' && /^\d{4}-\d{2}-\d{2}$/.test(point.date)
      && typeof point.race_id === 'string' && (point.post_time === null || typeof point.post_time === 'string'))) return null
    if (daily) {
      const last = model.points.at(-1)
      if (model.race_count === 0 ? model.points.length !== 0
        : !last || last.total_cost !== model.total_cost || last.total_return !== model.total_return || last.race_count !== model.race_count) return null
    }
    const statuses = model.status_counts
    const cohorts = model.cohorts
    if (statuses !== undefined && (!isObject(statuses)
      || !['settled', 'pending', 'payout_pending', 'review', 'skipped', 'other'].every(key => isCount(statuses[key])))) return null
    if (cohorts !== undefined && (!isObject(cohorts) || !['evaluation', 'historical', 'pre_race'].every(key => {
      const cohort = cohorts[key]
      return isObject(cohort) && hasTotals(cohort, true) && isCount(cohort.record_count)
        && isCount(cohort.pending_races) && cohort.record_count >= Number(cohort.race_count) + cohort.pending_races
    }))) return null
  }
  return value as ModelPerformance
}

export function readMonthlyPerformance(value: unknown, year: number, dateFrom: string, dateTo: string, origin: PaperOrigin): ModelMonthlyPerformance | null {
  if (!isObject(value) || value.schema !== 'model-monthly-roi-v1' || value.year !== year
    || value.mode !== 'paper' || !hasSimulationScope(value, origin)
    || value.date_from !== (dateFrom || null) || value.date_to !== (dateTo || null) || !Array.isArray(value.models)) return null
  const ids = new Set<string>()
  for (const model of value.models) {
    if (!isObject(model) || typeof model.model_id !== 'string' || ids.has(model.model_id)
      || typeof model.color !== 'string' || !/^#[a-f\d]{6}$/i.test(model.color)
      || typeof model.display_name !== 'string' || !Array.isArray(model.months) || model.months.length !== 12
      || !model.months.every((month, index) => isObject(month) && month.month === index + 1 && hasTotals(month, true))) return null
    ids.add(model.model_id)
  }
  return value as ModelMonthlyPerformance
}

export function modelRoiChartRows(models: ModelRoiSeries[]): ModelRoiChartRow[] {
  const events = new Map<string, ModelRoiChartRow>()
  for (const model of models) {
    for (const point of model.points) {
      const postTime = point.post_time?.replace(/^(\d{1,2}):(\d{1,2})$/, (_, hour: string, minute: string) => `${hour.padStart(2, '0')}:${minute.padStart(2, '0')}`) ?? null
      const event = `${point.date}|${postTime || ''}|${point.race_id}`
      const row = events.get(event) ?? { event, date: point.date, post_time: postTime, values: new Map<string, ModelRoiPoint>() }
      row.values.set(model.model_id, point)
      events.set(event, row)
    }
  }
  const latest = new Map<string, ModelRoiPoint>()
  return [...events.values()].sort((a, b) => a.event.localeCompare(b.event)).map(row => {
    row.values.forEach((point, modelId) => latest.set(modelId, point))
    return { ...row, values: new Map(latest) }
  })
}
