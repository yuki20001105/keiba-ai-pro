import type { ScrapeRaceScope } from './scrape-race-scope'

export type ScrapeRetirementBinding = {
  jobId: string
  operationId: string
  startDate: string
  endDate: string
  forceRescrape: boolean
  raceScope?: ScrapeRaceScope
}

export type RetiredScrapeDraft = {
  startPeriod: string
  endPeriod: string
  raceScope: ScrapeRaceScope
}

export function retirementRequest(binding: ScrapeRetirementBinding) {
  return {
    operation_id: binding.operationId,
    start_date: binding.startDate,
    end_date: binding.endDate,
    force_rescrape: binding.forceRescrape,
    server_batch: true,
    // Omission is part of an older saved request's identity.
    ...(binding.raceScope === undefined ? {} : { race_scope: binding.raceScope }),
  }
}

export function retirementCanonicalPayload(binding: ScrapeRetirementBinding) {
  return {
    start_date: binding.startDate,
    end_date: binding.endDate,
    force_rescrape: binding.forceRescrape,
    dry_run: false,
    server_batch: true,
    // The backend's existing canonical all-scope payload omits this key.
    ...(binding.raceScope === 'jra' ? { race_scope: 'jra' } : {}),
  }
}

function record(value: unknown): value is Record<string, unknown> {
  return typeof value === 'object' && value !== null && !Array.isArray(value)
}

export function isRetirementReceipt(value: unknown, binding: ScrapeRetirementBinding): boolean {
  if (!record(value) || value.job_id !== binding.jobId || value.operation_id !== binding.operationId
    || value.status !== 'cancelled' || value.retired !== true || value.replacement_allowed !== true
    || !record(value.request_payload)) return false
  const actual = value.request_payload
  const expected: Record<string, unknown> = retirementCanonicalPayload(binding)
  return Object.keys(actual).length === Object.keys(expected).length
    && Object.entries(expected).every(([key, field]) => actual[key] === field)
}

export function retiredScrapeDraft(binding: ScrapeRetirementBinding): RetiredScrapeDraft {
  return {
    startPeriod: `${binding.startDate.slice(0, 4)}-${binding.startDate.slice(4, 6)}`,
    endPeriod: `${binding.endDate.slice(0, 4)}-${binding.endDate.slice(4, 6)}`,
    raceScope: binding.raceScope ?? 'all',
  }
}
