import { describe, expect, it } from 'vitest'
import { recoveryCandidate, recoveryHistory } from '@/lib/scrape-job-recovery'
import type { PersistedUncertaintyLock } from '@/lib/scrape-uncertainty-approval'

const lock: PersistedUncertaintyLock = {
  version: 1, failureKind: 'client_stop', occurredAt: '2026-09-13T12:48:11.000Z',
  request: { startPeriod: '2016-01', endPeriod: '2026-09', forceRescrape: false },
}
const job = {
  job_id: '446e4cc1-f3c5-4874-90b1-5c2eb81ac739', status: 'completed',
  created_at: '2026-09-13T12:26:44Z', result: { races_collected: 62 },
  request_payload: { start_date: '20160101', end_date: '20160131', force_rescrape: false, dry_run: false },
}

describe('legacy jobless lock recovery evidence', () => {
  it('can identify the completed child month without claiming the entire range completed', () => {
    expect(recoveryCandidate(job, lock)).toMatchObject({ jobId: job.job_id, startDate: '20160101', endDate: '20160131' })
  })
  it.each(['running', 'queued', 'cancelling', 'not_found'])('rejects nonterminal %s', status => {
    expect(recoveryCandidate({ ...job, status }, lock)).toBeNull()
  })
  it('rejects jobs created after the uncertainty, unrelated dates and dry runs', () => {
    expect(recoveryCandidate({ ...job, created_at: '2026-09-13T13:00:00Z' }, lock)).toBeNull()
    for (const change of [{ start_date: '20151231' }, { end_date: '20261001' }, { dry_run: true }, { force_rescrape: true }, { start_date: '20160231' }]) {
      expect(recoveryCandidate({ ...job, request_payload: { ...job.request_payload, ...change } }, lock)).toBeNull()
    }
  })
  it.each([null, {}, { races_collected: '62' }, { races_collected: -1 }])('requires a valid completion result (%j)', result => {
    expect(recoveryCandidate({ ...job, result }, lock)).toBeNull()
  })
  it('requires a complete owner-scoped history with no active jobs', () => {
    expect(recoveryHistory({ jobs: [job], count: 1 })).toEqual([job])
    expect(() => recoveryHistory({ jobs: [job], count: 0 })).toThrow()
    expect(() => recoveryHistory({ jobs: [{ ...job, status: 'running' }], count: 1 })).toThrow('実行中')
    expect(() => recoveryHistory({ jobs: [{ ...job, status: 'unknown' }], count: 1 })).toThrow()
  })
})
