export type DryRunSummary = {
  total_target_count: number
  unique_url_count: number
  estimated_request_count: number
  cache_hit_count: number
  cache_miss_count: number
  resume_hit_count: number
  skipped_count: number
  db_existing_skip_count: number
  db_existing_race_count: number
  db_existing_horse_count: number
  db_existing_result_count: number
  db_existing_pedigree_count: number
  new_fetch_required_count: number
  already_covered_count: number
  estimated_runtime_sec: number
}

export type DryRunResult = {
  dry_run: DryRunSummary
  rate_limit_policy?: {
    min_interval_sec?: number
    scope?: string
    note?: string
  }
  retry_backoff_policy?: {
    max_retries?: number
    retry_statuses?: number[]
    backoff?: { type?: string; base_sec?: number; jitter_sec?: number }
    retry_after?: string
  }
  circuit_breaker_policy?: {
    failure_threshold?: number
    cooldown_sec?: number
    scope?: string
  }
}

export type MonthDateRange = {
  label: string
  startDateStr: string
  endDateStr: string
}

const SUM_FIELDS: Array<keyof DryRunSummary> = [
  'total_target_count',
  'unique_url_count',
  'estimated_request_count',
  'cache_hit_count',
  'cache_miss_count',
  'resume_hit_count',
  'skipped_count',
  'db_existing_skip_count',
  'new_fetch_required_count',
  'already_covered_count',
  'estimated_runtime_sec',
]

const SNAPSHOT_FIELDS: Array<keyof DryRunSummary> = [
  'db_existing_race_count',
  'db_existing_horse_count',
  'db_existing_result_count',
  'db_existing_pedigree_count',
]

export function enumerateMonthDateRanges(startPeriod: string, endPeriod: string): MonthDateRange[] {
  const [startYear, startMonth] = startPeriod.split('-').map(Number)
  const [endYear, endMonth] = endPeriod.split('-').map(Number)
  const ranges: MonthDateRange[] = []
  const pad = (value: number) => String(value).padStart(2, '0')

  let year = startYear
  let month = startMonth
  while (year < endYear || (year === endYear && month <= endMonth)) {
    const lastDay = new Date(year, month, 0).getDate()
    ranges.push({
      label: `${year}-${pad(month)}`,
      startDateStr: `${year}${pad(month)}01`,
      endDateStr: `${year}${pad(month)}${pad(lastDay)}`,
    })
    month += 1
    if (month > 12) {
      month = 1
      year += 1
    }
  }

  return ranges
}

export function aggregateDryRunResults(results: DryRunResult[]): DryRunResult {
  if (results.length === 0) {
    throw new Error('集計対象のDry-run結果がありません')
  }

  const summary = {} as DryRunSummary
  for (const field of SUM_FIELDS) {
    summary[field] = results.reduce((total, result) => total + result.dry_run[field], 0)
  }
  for (const field of SNAPSHOT_FIELDS) {
    summary[field] = Math.max(...results.map(result => result.dry_run[field]))
  }

  const first = results[0]
  return {
    dry_run: summary,
    rate_limit_policy: first.rate_limit_policy,
    retry_backoff_policy: first.retry_backoff_policy,
    circuit_breaker_policy: first.circuit_breaker_policy,
  }
}
