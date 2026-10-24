import { authFetch } from '@/lib/auth-fetch'
import { selectedTickets } from '@/lib/betting-policy'
import type { Recommendation, RecommendedTicket } from '@/lib/race-analysis-types'

export type PerformanceMode = 'paper' | 'actual'
export type PerformanceStatus = 'pending' | 'settled' | 'payout_pending' | 'review' | 'skipped'
export type PerformanceTicket = Pick<RecommendedTicket, 'bet_type' | 'combination' | 'horse_numbers' | 'stake'> & {
  actual_return?: number | null
  is_hit?: boolean | null
  status?: string
}
export type PerformanceRecord = {
  id: string
  race_id: string
  race_info: { date?: string; race_date?: string; race_name?: string; venue?: string; post_time?: string; race_no?: number }
  model_id?: string
  policy_version?: string
  decision: string
  reason: string
  mode: PerformanceMode
  tickets: PerformanceTicket[]
  total_cost: number
  actual_return: number | null
  profit: number | null
  status: PerformanceStatus
  created_at: string
  settled_at?: string | null
  settlement_reason?: string | null
}
export type PerformanceSummary = {
  settled_cost: number
  settled_return: number
  profit: number
  roi: number | null
  settled_races: number
  hit_races: number
  pending_races: number
  pending_cost: number
  skipped_races: number
}

export const EMPTY_PERFORMANCE_SUMMARY: PerformanceSummary = {
  settled_cost: 0, settled_return: 0, profit: 0, roi: null,
  settled_races: 0, hit_races: 0, pending_races: 0, pending_cost: 0, skipped_races: 0,
}

export type SelectionCalibrationMetrics = {
  ticket_count: number
  race_count: number
  date_count: number
  mean_probability: number | null
  hit_rate: number | null
  bias_predicted_minus_actual: number | null
  mean_probability_ci95: [number, number] | null
  hit_rate_ci95: [number, number] | null
  bias_ci95: [number, number] | null
  bootstrap_valid_samples: number
  interval_status: 'no_observations' | 'insufficient_distinct_dates' | 'descriptive_interval'
}

export type SelectionCalibrationBand = SelectionCalibrationMetrics & {
  band: string
  label: string
  lower: number
  upper: number | null
}

export type SelectionCalibrationGroup = SelectionCalibrationMetrics & {
  model_id: string
  policy_version: string
  bet_type: string
  odds_bands: SelectionCalibrationBand[]
}

export type SelectionDiagnostics = {
  schema: 'recorded-selection-calibration-v1'
  label: string
  observational: true
  unknown_holdout: false
  purchase_authority: false
  scope: { mode: PerformanceMode; date_from: string | null; date_to: string | null; record_count: number; includes_full_filtered_set: true }
  status_counts: Record<PerformanceStatus | 'other', number>
  sample: { eligible_tickets: number; eligible_races: number; eligible_dates: number; excluded_records: number; excluded_tickets: number }
  excluded_counts: Record<string, number>
  excluded_ticket_counts: Record<string, number>
  groups: SelectionCalibrationGroup[]
  bootstrap: {
    method: 'paired_whole_race_date_percentile'
    confidence_level: 0.95
    repetitions: number
    seed: number
    interval_scope: 'conditional_on_verified_settled_recorded_selection'
    simultaneous_intervals: false
    multiple_comparisons_adjusted: false
  }
}

const isCount = (value: unknown): value is number => typeof value === 'number' && Number.isInteger(value) && value >= 0
const isObject = (value: unknown): value is Record<string, unknown> => value !== null && typeof value === 'object' && !Array.isArray(value)
const isNullableNumber = (value: unknown, min: number, max: number) => value === null
  || typeof value === 'number' && Number.isFinite(value) && value >= min && value <= max
const isInterval = (value: unknown, min: number, max: number) => value === null || Array.isArray(value) && value.length === 2
  && value.every(item => typeof item === 'number' && Number.isFinite(item) && item >= min && item <= max) && value[0] <= value[1]

function hasCounts(value: unknown, keys: string[]) {
  return isObject(value) && keys.every(key => isCount(value[key]))
}

function diagnosticDate(value: unknown): string | null {
  if (value === null) return ''
  if (typeof value !== 'string') return null
  const digits = value.replace(/[-/]/g, '')
  return /^\d{8}$/.test(digits) ? digits : null
}

function validCalibrationMetrics(value: unknown): boolean {
  if (!isObject(value) || !['ticket_count', 'race_count', 'date_count', 'bootstrap_valid_samples'].every(key => isCount(value[key]))) return false
  if (!isNullableNumber(value.mean_probability, 0, 1) || !isNullableNumber(value.hit_rate, 0, 1)
    || !isNullableNumber(value.bias_predicted_minus_actual, -1, 1)
    || !isInterval(value.mean_probability_ci95, 0, 1) || !isInterval(value.hit_rate_ci95, 0, 1)
    || !isInterval(value.bias_ci95, -1, 1)
    || !['no_observations', 'insufficient_distinct_dates', 'descriptive_interval'].includes(String(value.interval_status))) return false
  return value.ticket_count !== 0 || [value.mean_probability, value.hit_rate, value.bias_predicted_minus_actual,
    value.mean_probability_ci95, value.hit_rate_ci95, value.bias_ci95].every(item => item === null)
}

/** Validate observational display scope only; never reinterpret it as model or purchase validation. */
export function readSelectionDiagnostics(value: unknown, mode: PerformanceMode, dateFrom = '', dateTo = ''): SelectionDiagnostics | null {
  if (!isObject(value) || value.schema !== 'recorded-selection-calibration-v1' || value.observational !== true
    || value.unknown_holdout !== false || value.purchase_authority !== false || typeof value.label !== 'string'
    || !isObject(value.scope) || value.scope.mode !== mode || value.scope.includes_full_filtered_set !== true
    || !isCount(value.scope.record_count)
    || diagnosticDate(value.scope.date_from) !== dateFrom.replace(/[-/]/g, '') || diagnosticDate(value.scope.date_to) !== dateTo.replace(/[-/]/g, '')
    || !hasCounts(value.status_counts, ['settled', 'pending', 'payout_pending', 'review', 'skipped', 'other'])
    || !hasCounts(value.sample, ['eligible_tickets', 'eligible_races', 'eligible_dates', 'excluded_records', 'excluded_tickets'])
    || !isObject(value.excluded_counts) || !Object.values(value.excluded_counts).every(isCount)
    || !isObject(value.excluded_ticket_counts) || !Object.values(value.excluded_ticket_counts).every(isCount) || !Array.isArray(value.groups)
    || !isObject(value.bootstrap) || value.bootstrap.method !== 'paired_whole_race_date_percentile'
    || value.bootstrap.confidence_level !== .95 || !isCount(value.bootstrap.repetitions) || value.bootstrap.repetitions < 2
    || !isCount(value.bootstrap.seed) || value.bootstrap.interval_scope !== 'conditional_on_verified_settled_recorded_selection'
    || value.bootstrap.simultaneous_intervals !== false || value.bootstrap.multiple_comparisons_adjusted !== false) return null
  const bounds = [1, 3, 5, 10, 20, 30]
  const groupKeys = new Set<string>()
  for (const group of value.groups) {
    if (!validCalibrationMetrics(group) || !isObject(group) || !['model_id', 'policy_version', 'bet_type'].every(key => typeof group[key] === 'string')
      || !Array.isArray(group.odds_bands) || group.odds_bands.length !== bounds.length) return null
    const key = JSON.stringify([group.model_id, group.policy_version, group.bet_type])
    if (groupKeys.has(key)) return null
    groupKeys.add(key)
    for (const [index, band] of group.odds_bands.entries()) {
      if (!validCalibrationMetrics(band) || !isObject(band) || typeof band.band !== 'string' || typeof band.label !== 'string'
        || band.lower !== bounds[index] || band.upper !== (bounds[index + 1] ?? null)) return null
    }
  }
  return value as SelectionDiagnostics
}

export function performanceRoiLabel(value: number | null | undefined): string {
  return typeof value === 'number' && Number.isFinite(value) ? `${value.toFixed(1)}%` : '未算出'
}

/** Persist the allocated selection, never a newly reconstructed bet or a real purchase. */
export async function savePerformanceRecord(
  snapshotId: string | null | undefined,
  recommendation: Recommendation | null | undefined,
  mode: PerformanceMode = 'paper',
  nowMs = Date.now(),
): Promise<{ record: PerformanceRecord; existing: boolean; selection_matches: boolean }> {
  if (!snapshotId) throw new Error('成績に記録できません。予測を再実行してください。')
  const tickets = selectedTickets(recommendation, nowMs).map(({ bet_type, combination, horse_numbers, stake }) => ({
    bet_type, combination, horse_numbers: [...horse_numbers], stake,
  }))
  if (mode === 'actual' && tickets.length === 0) throw new Error('有効な買い目がありません。')
  const res = await authFetch('/api/performance/records', {
    method: 'POST', headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ snapshot_id: snapshotId, tickets, mode, ...(mode === 'actual' ? { confirmed: true } : {}) }),
    signal: AbortSignal.timeout(15_000),
  })
  const data = await res.json().catch(() => ({}))
  if (!res.ok) throw new Error(typeof data.detail === 'string' ? data.detail : '成績の記録に失敗しました。')
  if (!data.record?.id) throw new Error('成績の保存を確認できませんでした。')
  const existing = Boolean(data.existing)
  const ticketKey = (items: PerformanceTicket[]) => JSON.stringify(items.map(ticket => ({
    bet_type: ticket.bet_type, combination: ticket.combination, horse_numbers: ticket.horse_numbers, stake: ticket.stake,
  })).sort((a, b) => `${a.bet_type}:${a.combination}`.localeCompare(`${b.bet_type}:${b.combination}`)))
  const selectionMatches = !existing || (Array.isArray(data.record.tickets) && ticketKey(data.record.tickets) === ticketKey(tickets))
  if (mode === 'actual' && !selectionMatches) throw new Error('このレースには別の買い目が記録されています。成績確認で既存の記録を確認してください。')
  return { record: data.record, existing, selection_matches: selectionMatches }
}

export function performanceDateLabel(value?: string | null): string {
  const digits = (value ?? '').replace(/[-/]/g, '').slice(0, 8)
  return /^\d{8}$/.test(digits) ? `${digits.slice(0, 4)}/${digits.slice(4, 6)}/${digits.slice(6, 8)}` : '日付未確認'
}

export function performanceStatusLabel(record: Pick<PerformanceRecord, 'status' | 'actual_return'>): string {
  switch (record.status) {
    case 'settled': return record.actual_return == null ? '要確認' : record.actual_return > 0 ? '的中' : '外れ'
    case 'skipped': return '見送り'
    case 'payout_pending': return '払戻待ち'
    case 'review': return '要確認'
    default: return '結果待ち'
  }
}
