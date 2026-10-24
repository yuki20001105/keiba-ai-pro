import { fireEvent, render, screen, waitFor } from '@testing-library/react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { ScrapeJobRecovery } from '@/components/ScrapeJobRecovery'
import type { PersistedUncertaintyLock } from '@/lib/scrape-uncertainty-approval'

const { fetchMock } = vi.hoisted(() => ({ fetchMock: vi.fn() }))
vi.mock('@/lib/auth-fetch', () => ({ authFetch: fetchMock }))
const lock: PersistedUncertaintyLock = {
  version: 1, failureKind: 'client_stop', occurredAt: '2026-09-13T12:48:11.000Z',
  request: { startPeriod: '2016-01', endPeriod: '2026-09', forceRescrape: false },
}
const job = {
  job_id: '446e4cc1-f3c5-4874-90b1-5c2eb81ac739', status: 'completed',
  created_at: '2026-09-13T12:26:44Z', result: { races_collected: 62 },
  request_payload: { start_date: '20160101', end_date: '20160131', force_rescrape: false, dry_run: false },
}
const history = () => Response.json({ jobs: [job], count: 1 })

describe('read-only legacy job recovery', () => {
  beforeEach(() => vi.clearAllMocks())
  afterEach(() => vi.restoreAllMocks())

  async function chooseJob() {
    fireEvent.click(screen.getByRole('button', { name: '前回の取得を探す' }))
    const select = await screen.findByRole('combobox')
    fireEvent.change(select, { target: { value: job.job_id } })
    fireEvent.click(screen.getByRole('checkbox'))
  }

  it('requires explicit selection and fresh server evidence; performs GET only', async () => {
    fetchMock.mockResolvedValueOnce(history()).mockResolvedValueOnce(history()).mockResolvedValueOnce(Response.json(job))
    const verified = vi.fn()
    render(<ScrapeJobRecovery lock={lock} onVerifiedJob={verified} />)
    expect(fetchMock).not.toHaveBeenCalled()
    await chooseJob()
    expect(verified).not.toHaveBeenCalled()
    fireEvent.click(screen.getByRole('button', { name: 'この取得と照合する' }))
    await waitFor(() => expect(verified).toHaveBeenCalledWith(job.job_id))
    expect(fetchMock).toHaveBeenCalledTimes(3)
    for (const [url, init] of fetchMock.mock.calls) {
      expect(String(url)).toMatch(/^\/api\/scrape\/(history\?|status\/)/)
      expect(init.method).toBeUndefined()
      expect(init.body).toBeUndefined()
      expect(init.cache).toBe('no-store')
    }
  })

  it('does not bind a job if another acquisition starts before confirmation', async () => {
    fetchMock.mockResolvedValueOnce(history()).mockResolvedValueOnce(Response.json({ jobs: [{ ...job, status: 'running' }], count: 1 }))
    const verified = vi.fn()
    render(<ScrapeJobRecovery lock={lock} onVerifiedJob={verified} />)
    await chooseJob()
    fireEvent.click(screen.getByRole('button', { name: 'この取得と照合する' }))
    expect(await screen.findByRole('status')).toHaveTextContent('別の取得が実行中')
    expect(verified).not.toHaveBeenCalled()
  })

  it('does not change the lock when the status no longer matches the selected job', async () => {
    fetchMock.mockResolvedValueOnce(history()).mockResolvedValueOnce(history()).mockResolvedValueOnce(Response.json({ ...job, status: 'running' }))
    const verified = vi.fn()
    render(<ScrapeJobRecovery lock={lock} onVerifiedJob={verified} />)
    await chooseJob()
    fireEvent.click(screen.getByRole('button', { name: 'この取得と照合する' }))
    expect(await screen.findByRole('status')).toHaveTextContent('記録が一致しません')
    expect(verified).not.toHaveBeenCalled()
  })
})
