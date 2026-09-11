import { describe, expect, it } from 'vitest'

import {
  aggregateDryRunResults,
  enumerateMonthDateRanges,
  type DryRunResult,
} from '@/lib/dry-run-batch'

function result(value: number, dbValue: number): DryRunResult {
  return {
    dry_run: {
      total_target_count: value,
      unique_url_count: value,
      estimated_request_count: value,
      cache_hit_count: value,
      cache_miss_count: value,
      resume_hit_count: value,
      skipped_count: value,
      db_existing_skip_count: value,
      db_existing_race_count: dbValue,
      db_existing_horse_count: dbValue,
      db_existing_result_count: dbValue,
      db_existing_pedigree_count: dbValue,
      new_fetch_required_count: value,
      already_covered_count: value,
      estimated_runtime_sec: value,
    },
    rate_limit_policy: { min_interval_sec: 1, scope: 'per-host' },
  }
}

describe('enumerateMonthDateRanges', () => {
  it('splits a long period into API-safe calendar months', () => {
    const ranges = enumerateMonthDateRanges('2016-01', '2026-08')

    expect(ranges).toHaveLength(128)
    expect(ranges[0]).toEqual({
      label: '2016-01',
      startDateStr: '20160101',
      endDateStr: '20160131',
    })
    expect(ranges.at(-1)).toEqual({
      label: '2026-08',
      startDateStr: '20260801',
      endDateStr: '20260831',
    })
    expect(ranges.find(range => range.label === '2020-02')?.endDateStr).toBe('20200229')
    expect(ranges.every(range => {
      const start = new Date(`${range.startDateStr.slice(0, 4)}-${range.startDateStr.slice(4, 6)}-${range.startDateStr.slice(6, 8)}T00:00:00Z`)
      const end = new Date(`${range.endDateStr.slice(0, 4)}-${range.endDateStr.slice(4, 6)}-${range.endDateStr.slice(6, 8)}T00:00:00Z`)
      return (end.getTime() - start.getTime()) / 86_400_000 <= 30
    })).toBe(true)
  })
})

describe('aggregateDryRunResults', () => {
  it('sums monthly estimates without multiplying database snapshots', () => {
    const aggregate = aggregateDryRunResults([result(2, 100), result(3, 105)])

    expect(aggregate.dry_run.total_target_count).toBe(5)
    expect(aggregate.dry_run.estimated_request_count).toBe(5)
    expect(aggregate.dry_run.estimated_runtime_sec).toBe(5)
    expect(aggregate.dry_run.db_existing_race_count).toBe(105)
    expect(aggregate.rate_limit_policy).toEqual({ min_interval_sec: 1, scope: 'per-host' })
  })
})
