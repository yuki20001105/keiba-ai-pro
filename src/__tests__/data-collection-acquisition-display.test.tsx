import { fireEvent, render, screen, waitFor } from '@testing-library/react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

const { fetchMock, batch } = vi.hoisted(() => ({ fetchMock: vi.fn(), batch: { value: {} as Record<string, unknown> } }))
vi.mock('@/lib/auth-fetch', () => ({ authFetch: fetchMock }))
vi.mock('@/contexts/AuthContext', () => ({ useAuth: () => ({ userId: 'owner-a' }) }))
vi.mock('@/components/Toast', () => ({ Toast: () => null }))
vi.mock('@/components/Logo', () => ({ Logo: () => null }))
vi.mock('@/hooks/useBatchScrape', async importOriginal => {
  const original = await importOriginal<typeof import('@/hooks/useBatchScrape')>()
  return { ...original, useBatchScrape: () => batch.value }
})

import DataCollectionPage from '@/app/data-collection/page'

const JOB = '11111111-1111-4111-8111-111111111111'
const json = (value: unknown) => Response.json(value)
const CAPABILITIES = { scrape_capabilities: {
  race_scopes: ['all', 'jra'], jra_month_reuse: true,
  safety_policy_version: 'normal-acquisition-safe-v1', minimum_provider_interval_seconds: 3,
  halt_http400: true, preserve_saved_values: true, max_concurrent_http_requests: 1,
} }
function runningJob(progress: Record<string, unknown> = {}, status = 'running') {
  return {
    job_id: JOB, status, created_at: '2026-10-12T07:00:00Z',
    request_payload: { start_date: '20160101', end_date: '20260930', force_rescrape: false, server_batch: true },
    progress: { race_task_concurrency: 3, max_concurrent_http_requests: 1,
      http_pacing_scope: 'provider-family', current_status: status, ...progress },
  }
}

describe('collection acquisition-only presentation', () => {
  beforeEach(() => {
    localStorage.clear()
    fetchMock.mockReset()
    batch.value = {
      loading: true, status: 'queued', error: null, failureKind: null, canRetry: false,
      canResubmit: false, isExecutionLocked: true, jobId: JOB,
      progress: { current: 0, total: 100, message: '再接続中', eta: '' }, result: null,
      start: vi.fn(), reconnect: vi.fn().mockResolvedValue(null),
      resubmit: vi.fn(), requestBatchStop: vi.fn(), clearExecutionLockAfterReconciliation: vi.fn(),
    }
  })
  afterEach(() => vi.restoreAllMocks())

  function serve(jobs: unknown[] = []) {
    fetchMock.mockImplementation(async (url: string, init: RequestInit = {}) => {
      expect(init.method).not.toBe('POST')
      if (url === '/api/scrape/health' || url === '/api/health') return json({ status: 'healthy', ...CAPABILITIES })
      if (url.startsWith('/api/data-stats')) return json({ total_races: 78949, total_horses: 949950 })
      if (url.startsWith('/api/scrape/history')) return json({ jobs, count: jobs.length })
      if (url === `/api/scrape/status/${JOB}` && jobs.length === 1) return json(jobs[0])
      throw new Error('unexpected request: ' + url)
    })
  }

  it('labels an unconfirmed queued request without pretending acquisition started or retrying it', async () => {
    serve()
    render(<DataCollectionPage />)
    await waitFor(() => expect(screen.getByTestId('batch-status-panel'))
      .toHaveTextContent('サーバー受付を確認中（取得開始は未確認）'))
    expect(screen.getByTestId('execute-button')).toHaveTextContent('受付確認中...')
    expect(screen.getByTestId('execute-button')).toBeDisabled()
    expect(screen.getByTestId('scrape-repair-mode')).toHaveTextContent('正常データは再利用し、不足・未確定結果を補完')
    expect(screen.getByTestId('scrape-race-scope')).toHaveValue('jra')
    expect(screen.getByTestId('scrape-race-scope')).toBeDisabled()
    expect(screen.queryByTestId('scrape-acquisition-policy')).toBeNull()
    expect(batch.value.start).not.toHaveBeenCalled()
    expect(batch.value.resubmit).not.toHaveBeenCalled()
  })

  it('uses only its own reserved period while server acceptance is still unconfirmed', async () => {
    batch.value.requestPeriod = { jobId: JOB, startPeriod: '2016-01', endPeriod: '2026-09' }
    batch.value.raceScope = 'all'
    serve()
    render(<DataCollectionPage />)
    await waitFor(() => expect(screen.getByTestId('batch-status-panel')).toHaveTextContent('取得開始は未確認'))
    expect(screen.getByTestId('start-period-input')).toHaveValue('2016-01')
    expect(screen.getByTestId('end-period-input')).toHaveValue('2026-09')
    expect(screen.getByTestId('scrape-race-scope')).toHaveValue('all')
    expect(screen.queryByTestId('active-scrape-job')).toBeNull()
    expect(batch.value.start).not.toHaveBeenCalled()
    expect(batch.value.resubmit).not.toHaveBeenCalled()
  })

  it('shows one recovery action despite a healthy API and preserves the original request in details', async () => {
    batch.value.status = 'error'
    batch.value.loading = false
    batch.value.failureKind = 'monitoring'
    batch.value.monitoringIssue = 'service_unavailable'
    batch.value.error = 'サーバーが状態確認に応答できません（HTTP 502）。'
    batch.value.requestPeriod = { jobId: JOB, startPeriod: '2016-01', endPeriod: '2026-09' }
    batch.value.raceScope = 'all'
    serve()
    render(<DataCollectionPage />)
    await waitFor(() => expect(screen.getByText('API 稼働中')).toBeInTheDocument())
    expect(screen.getByRole('heading', { name: '受付を確認できません' })).toBeInTheDocument()
    expect(screen.getByTestId('batch-recovery-details')).not.toHaveAttribute('open')
    expect(screen.getByTestId('batch-recovery-details')).toHaveTextContent('HTTP 502')
    expect(screen.getByTestId('batch-recovery-details')).toHaveTextContent('2016/01～2026/09')
    expect(screen.getByTestId('batch-recovery-details')).toHaveTextContent(JOB)
    expect(screen.getByTestId('batch-scrape-race-scope')).toHaveTextContent('中央・地方競馬')
    expect(screen.getByTestId('execute-button')).toBeDisabled()
    expect(screen.getByTestId('scrape-race-scope')).toHaveValue('all')
    expect(screen.queryByTestId('resubmit-batch-button')).toBeNull()
    fireEvent.click(screen.getByTestId('reconnect-batch-button'))
    expect(batch.value.reconnect).toHaveBeenCalledTimes(1)
    expect(batch.value.start).not.toHaveBeenCalled()
    expect(batch.value.resubmit).not.toHaveBeenCalled()
  })

  it('does not duplicate the last known active-job card after monitoring is lost', async () => {
    batch.value.status = 'error'
    batch.value.loading = false
    batch.value.failureKind = 'monitoring'
    batch.value.monitoringIssue = 'network'
    batch.value.raceScope = 'all'
    serve([runningJob()])
    render(<DataCollectionPage />)
    await waitFor(() => expect(screen.getByRole('heading', { name: '取得状況を確認できません' })).toBeInTheDocument())
    expect(screen.queryByTestId('active-scrape-job')).toBeNull()
    expect(screen.getAllByRole('button', { name: '状態を再確認' })).toHaveLength(1)
    expect(screen.queryByTestId('scrape-acceptance-unverified')).toBeNull()
    expect(screen.getByTestId('batch-recovery-notice')).toHaveTextContent('継続している可能性')
    expect(screen.getByTestId('execute-button')).toBeDisabled()
    expect(batch.value.start).not.toHaveBeenCalled()
  })

  it('labels restoration as checking a previous request without pretending acquisition is 0% complete', async () => {
    batch.value.observationPhase = 'monitoring'
    batch.value.requestPeriod = { jobId: JOB, startPeriod: '2016-01', endPeriod: '2026-09' }
    batch.value.raceScope = 'all'
    serve()
    render(<DataCollectionPage />)
    await waitFor(() => expect(screen.getByTestId('batch-status-panel'))
      .toHaveTextContent('前回の依頼を確認中・取得は開始していません'))
    expect(screen.getByTestId('execute-button')).toHaveTextContent('前回の依頼を確認中')
    expect(screen.queryByTestId('scrape-progress-counters')).toBeNull()
    expect(screen.queryByRole('progressbar', { name: '取得進捗' })).toBeNull()
    expect(screen.getByTestId('execute-button')).toBeDisabled()
    expect(batch.value.start).not.toHaveBeenCalled()
    expect(batch.value.resubmit).not.toHaveBeenCalled()
  })

  it('returns to an editable central-only draft only after confirmed retirement without starting acquisition', async () => {
    batch.value.status = 'error'
    batch.value.loading = false
    batch.value.failureKind = 'monitoring'
    batch.value.monitoringIssue = 'not_found'
    batch.value.canRetire = true
    batch.value.requestPeriod = { jobId: JOB, startPeriod: '2016-01', endPeriod: '2026-09' }
    batch.value.raceScope = 'all'
    batch.value.retire = vi.fn(async (confirmRetirement: () => boolean) => {
      if (!confirmRetirement()) return null
      Object.assign(batch.value, { status: 'idle', jobId: null, isExecutionLocked: false,
        canRetire: false, raceScope: null, requestPeriod: null, failureKind: null })
      return { startPeriod: '2016-01', endPeriod: '2026-09', raceScope: 'all' }
    })
    const confirmSpy = vi.spyOn(window, 'confirm').mockReturnValue(true)
    serve()
    render(<DataCollectionPage />)
    await waitFor(() => expect(screen.getByTestId('retire-batch-button')).toBeEnabled())
    expect(screen.getByTestId('scrape-race-scope')).toHaveValue('all')
    fireEvent.click(screen.getByTestId('retire-batch-button'))
    await waitFor(() => expect(screen.getByTestId('scrape-request-selection-ready')).toBeInTheDocument())
    await waitFor(() => expect(screen.getByTestId('execute-button')).toBeEnabled())
    expect(screen.getByTestId('start-period-input')).toHaveValue('2016-01')
    expect(screen.getByTestId('end-period-input')).toHaveValue('2026-09')
    expect(screen.getByTestId('scrape-race-scope')).toHaveValue('jra')
    expect(screen.getByTestId('scrape-race-scope')).toBeEnabled()
    expect(screen.getByTestId('scrape-request-selection-ready')).toHaveTextContent('まだ取得は開始していません')
    expect(confirmSpy).toHaveBeenCalledWith(expect.stringContaining('保存済みデータは削除しません'))
    expect(batch.value.start).not.toHaveBeenCalled()
    expect(batch.value.resubmit).not.toHaveBeenCalled()
    expect(fetchMock.mock.calls.some(([, init]) => init?.method === 'POST')).toBe(false)
  })

  it('keeps the original request and scope locked when retirement confirmation is dismissed', async () => {
    batch.value.status = 'error'
    batch.value.loading = false
    batch.value.failureKind = 'monitoring'
    batch.value.monitoringIssue = 'not_found'
    batch.value.canRetire = true
    batch.value.requestPeriod = { jobId: JOB, startPeriod: '2016-01', endPeriod: '2026-09' }
    batch.value.raceScope = 'all'
    batch.value.retire = vi.fn(async (confirmRetirement: () => boolean) => {
      expect(confirmRetirement()).toBe(false)
      return null
    })
    vi.spyOn(window, 'confirm').mockReturnValue(false)
    serve()
    render(<DataCollectionPage />)
    await waitFor(() => expect(screen.getByTestId('retire-batch-button')).toBeEnabled())
    fireEvent.click(screen.getByTestId('retire-batch-button'))
    await waitFor(() => expect(batch.value.retire).toHaveBeenCalledTimes(1))
    expect(screen.getByTestId('execute-button')).toBeDisabled()
    expect(screen.getByTestId('scrape-race-scope')).toHaveValue('all')
    expect(screen.queryByTestId('scrape-request-selection-ready')).toBeNull()
    expect(batch.value.start).not.toHaveBeenCalled()
    expect(batch.value.resubmit).not.toHaveBeenCalled()
  })

  it('restores a sealed retirement draft once and does not overwrite the newly selected scope or period', async () => {
    Object.assign(batch.value, { status: 'idle', loading: false, jobId: null, isExecutionLocked: false,
      retiredRequestDraft: { startPeriod: '2016-01', endPeriod: '2026-09', raceScope: 'all' } })
    serve()
    render(<DataCollectionPage />)
    await waitFor(() => expect(screen.getByTestId('scrape-request-selection-ready')).toBeInTheDocument())
    await waitFor(() => expect(screen.getByTestId('scrape-race-scope')).toBeEnabled())
    expect(screen.getByTestId('start-period-input')).toHaveValue('2016-01')
    expect(screen.getByTestId('scrape-race-scope')).toHaveValue('jra')
    fireEvent.change(screen.getByTestId('start-period-input'), { target: { value: '2018-01' } })
    fireEvent.change(screen.getByTestId('scrape-race-scope'), { target: { value: 'all' } })
    expect(screen.getByTestId('start-period-input')).toHaveValue('2018-01')
    expect(screen.getByTestId('scrape-race-scope')).toHaveValue('all')
    expect(batch.value.start).not.toHaveBeenCalled()
    expect(batch.value.resubmit).not.toHaveBeenCalled()
    expect(fetchMock.mock.calls.some(([, init]) => init?.method === 'POST')).toBe(false)
  })

  it('distinguishes server-accepted queueing from actual acquisition', async () => {
    serve([runningJob({}, 'queued')])
    render(<DataCollectionPage />)
    await waitFor(() => expect(screen.getByTestId('active-scrape-job')).toHaveTextContent('サーバー受付済み・開始待ち'))
    expect(screen.getByTestId('batch-status-panel')).toHaveTextContent('サーバー受付済み・開始待ち')
    expect(screen.getByTestId('execute-button')).toHaveTextContent('開始待ち...')
    expect(screen.queryByTestId('scrape-acquisition-policy')).toBeNull()
    expect(screen.getByTestId('active-scrape-race-scope')).toHaveTextContent('中央・地方競馬')
  })

  it('shows verified parallelism and measured speed for the same running server job', async () => {
    batch.value.status = 'running'
    serve([runningJob({ fetch_metrics_scope: 'current_worker_attempt',
      fetch_metrics: { network_requests: 20 }, attempt_elapsed_sec: 40,
      fetch_control: { interval: 1.5 } })])
    render(<DataCollectionPage />)
    const policy = await screen.findByTestId('scrape-acquisition-policy')
    expect(policy).toHaveTextContent('解析・保存 最大3並列 / 外部通信は共通制限')
    expect(policy).toHaveTextContent('20通信 · 30.0通信/分（現作業枠） · 1.5秒/回')
    expect(screen.getByTestId('execute-button')).toBeDisabled()
    expect(batch.value.start).not.toHaveBeenCalled()
  })

  it('does not claim a policy absent from an old running server payload', async () => {
    batch.value.status = 'running'
    serve([runningJob({ race_task_concurrency: undefined })])
    render(<DataCollectionPage />)
    await screen.findByTestId('active-scrape-job')
    expect(screen.queryByTestId('scrape-acquisition-policy')).toBeNull()
  })

  it('restores the original draft after an existing server job disappears rather than overwriting it', async () => {
    const jobs = [runningJob()]
    serve(jobs)
    render(<DataCollectionPage />)
    const draftStart = (screen.getByTestId('start-period-input') as HTMLInputElement).value
    const draftEnd = (screen.getByTestId('end-period-input') as HTMLInputElement).value
    await screen.findByTestId('active-scrape-job')
    expect(screen.getByTestId('start-period-input')).toHaveValue('2016-01')
    expect(screen.getByTestId('end-period-input')).toHaveValue('2026-09')
    expect(screen.getByTestId('scrape-race-scope')).toHaveValue('all')
    jobs.splice(0)
    batch.value.status = 'idle'
    batch.value.loading = false
    batch.value.isExecutionLocked = false
    fireEvent.click(screen.getByTestId('refresh-active-job-button'))
    await waitFor(() => expect(screen.queryByTestId('active-scrape-job')).toBeNull())
    expect(screen.getByTestId('start-period-input')).toHaveValue(draftStart)
    expect(screen.getByTestId('end-period-input')).toHaveValue(draftEnd)
    expect(screen.getByTestId('scrape-race-scope')).toHaveValue('jra')
    expect(batch.value.start).not.toHaveBeenCalled()
  })

  it('blocks all new starts on a healthy old server while leaving the target selectable', async () => {
    batch.value.loading = false
    batch.value.status = 'idle'
    batch.value.isExecutionLocked = false
    batch.value.jobId = null
    serve()
    const original = fetchMock.getMockImplementation()!
    fetchMock.mockImplementation((url: string, init: RequestInit = {}) => url === '/api/scrape/health'
      ? Promise.resolve(json({ status: 'healthy' })) : original(url, init))
    render(<DataCollectionPage />)
    expect(await screen.findByTestId('jra-scrape-server-pending')).toHaveTextContent('サーバーへの反映待ち')
    expect(screen.getByTestId('execute-button')).toBeDisabled()
    expect(screen.getByTestId('dry-run-button')).toBeDisabled()
    await waitFor(() => expect(screen.getByTestId('scrape-race-scope')).toBeEnabled())
    fireEvent.change(screen.getByTestId('scrape-race-scope'), { target: { value: 'all' } })
    expect(screen.getByTestId('scrape-safety-policy')).toHaveTextContent('安全設定はサーバーへの反映待ち')
    expect(screen.getByTestId('execute-button')).toBeDisabled()
    expect(screen.getByTestId('dry-run-button')).toBeDisabled()
    expect(batch.value.start).not.toHaveBeenCalled()
    expect(fetchMock.mock.calls.some(([, init]) => init?.method === 'POST')).toBe(false)
  })

  it('labels the verified minimum interval separately from measured running speed', async () => {
    batch.value.status = 'running'
    serve([runningJob({ fetch_metrics_scope: 'current_worker_attempt',
      fetch_metrics: { network_requests: 20 }, attempt_elapsed_sec: 40,
      fetch_control: { interval: 1.5 } })])
    render(<DataCollectionPage />)
    expect(await screen.findByTestId('scrape-acquisition-policy')).toHaveTextContent('1.5秒/回')
    expect(screen.getByTestId('scrape-safety-policy'))
      .toHaveTextContent('最低3秒・HTTP400で停止・保存済み値を保護')
    expect(batch.value.start).not.toHaveBeenCalled()
  })

  it.each(['jra', 'all'] as const)('rechecks the safety policy immediately before starting %s', async raceScope => {
    Object.assign(batch.value, { loading: false, status: 'idle', isExecutionLocked: false, jobId: null })
    vi.spyOn(window, 'confirm').mockReturnValue(true)
    serve()
    const original = fetchMock.getMockImplementation()!
    fetchMock.mockImplementation((url: string, init: RequestInit = {}) => url === '/api/health'
      ? Promise.resolve(json({ status: 'healthy', scrape_capabilities: {
        race_scopes: ['all', 'jra'], jra_month_reuse: true,
      } })) : original(url, init))
    render(<DataCollectionPage />)
    await waitFor(() => expect(screen.getByTestId('execute-button')).toBeEnabled())
    fireEvent.change(screen.getByTestId('scrape-race-scope'), { target: { value: raceScope } })
    fireEvent.click(screen.getByTestId('execute-button'))
    await waitFor(() => expect(screen.getByTestId('scrape-safety-policy')).toHaveTextContent('反映待ち'))
    expect(screen.getByTestId('execute-button')).toBeDisabled()
    expect(batch.value.start).not.toHaveBeenCalled()
    expect(batch.value.resubmit).not.toHaveBeenCalled()
    expect(fetchMock.mock.calls.some(([, init]) => init?.method === 'POST')).toBe(false)
  })

  it('preserves recovery and retirement on an old API without offering a re-send', async () => {
    Object.assign(batch.value, { status: 'error', loading: false, failureKind: 'monitoring',
      monitoringIssue: 'not_found', canRetire: true, canResubmit: true,
      retire: vi.fn().mockResolvedValue(null) })
    serve()
    const original = fetchMock.getMockImplementation()!
    fetchMock.mockImplementation((url: string, init: RequestInit = {}) => url === '/api/scrape/health'
      ? Promise.resolve(json({ status: 'healthy' })) : original(url, init))
    render(<DataCollectionPage />)
    await waitFor(() => expect(screen.getByTestId('scrape-safety-policy')).toHaveTextContent('反映待ち'))
    expect(screen.getByTestId('reconnect-batch-button')).toBeEnabled()
    expect(screen.getByTestId('retire-batch-button')).toBeEnabled()
    expect(screen.queryByTestId('resubmit-batch-button')).toBeNull()
    fireEvent.click(screen.getByTestId('reconnect-batch-button'))
    expect(batch.value.reconnect).toHaveBeenCalledTimes(1)
    expect(batch.value.resubmit).not.toHaveBeenCalled()
  })

  it('does not re-send if the running API loses the safety capability after page load', async () => {
    Object.assign(batch.value, { status: 'error', loading: false, failureKind: 'monitoring',
      monitoringIssue: 'not_found', canRetire: true, canResubmit: true,
      retire: vi.fn().mockResolvedValue(null) })
    serve()
    const original = fetchMock.getMockImplementation()!
    fetchMock.mockImplementation((url: string, init: RequestInit = {}) => url === '/api/health'
      ? Promise.resolve(json({ status: 'healthy', scrape_capabilities: {
        race_scopes: ['all', 'jra'], jra_month_reuse: true,
      } })) : original(url, init))
    render(<DataCollectionPage />)
    const resubmit = await screen.findByTestId('resubmit-batch-button')
    fireEvent.click(resubmit)
    await waitFor(() => expect(screen.getByTestId('scrape-safety-policy')).toHaveTextContent('反映待ち'))
    expect(batch.value.resubmit).not.toHaveBeenCalled()
    expect(screen.getByTestId('retire-batch-button')).toBeEnabled()
    expect(fetchMock.mock.calls.some(([, init]) => init?.method === 'POST')).toBe(false)
  })

  it('does not send an all-venue preflight when the fresh safety check fails', async () => {
    Object.assign(batch.value, { status: 'idle', loading: false, isExecutionLocked: false, jobId: null })
    serve()
    const original = fetchMock.getMockImplementation()!
    fetchMock.mockImplementation((url: string, init: RequestInit = {}) => url === '/api/health'
      ? Promise.resolve(json({ status: 'healthy', scrape_capabilities: {
        ...CAPABILITIES.scrape_capabilities, halt_http400: false,
      } })) : original(url, init))
    render(<DataCollectionPage />)
    await waitFor(() => expect(screen.getByTestId('dry-run-button')).toBeEnabled())
    fireEvent.change(screen.getByTestId('scrape-race-scope'), { target: { value: 'all' } })
    fireEvent.click(screen.getByTestId('dry-run-button'))
    await waitFor(() => expect(screen.getByTestId('scrape-safety-policy')).toHaveTextContent('反映待ち'))
    expect(screen.getByTestId('dry-run-button')).toBeDisabled()
    expect(fetchMock.mock.calls.some(([, init]) => init?.method === 'POST')).toBe(false)
    expect(batch.value.start).not.toHaveBeenCalled()
  })

  it('labels only an explicit jra server request as central-only', async () => {
    batch.value.status = 'running'
    const job = runningJob()
    serve([{ ...job, request_payload: { ...job.request_payload, race_scope: 'jra' } }])
    render(<DataCollectionPage />)
    expect(await screen.findByTestId('active-scrape-race-scope')).toHaveTextContent('中央競馬のみ')
  })

  it('submits the new form scope while leaving legacy and pending job identities alone', async () => {
    batch.value.loading = false
    batch.value.status = 'idle'
    batch.value.isExecutionLocked = false
    batch.value.jobId = null
    batch.value.start = vi.fn().mockResolvedValue({ races_collected: 0, elapsed_time: 0 })
    vi.spyOn(window, 'confirm').mockReturnValue(true)
    serve()
    render(<DataCollectionPage />)
    await waitFor(() => expect(screen.getByTestId('execute-button')).toBeEnabled())
    expect(screen.getByTestId('scrape-race-scope')).toHaveValue('jra')
    fireEvent.change(screen.getByTestId('start-period-input'), { target: { value: '2016-01' } })
    fireEvent.change(screen.getByTestId('end-period-input'), { target: { value: '2026-09' } })
    fireEvent.click(screen.getByTestId('execute-button'))
    await waitFor(() => expect(batch.value.start).toHaveBeenCalledWith('2016-01', '2026-09', false, 'jra'))
    fireEvent.change(screen.getByTestId('scrape-race-scope'), { target: { value: 'all' } })
    fireEvent.click(screen.getByTestId('execute-button'))
    await waitFor(() => expect(batch.value.start).toHaveBeenLastCalledWith('2016-01', '2026-09', false, 'all'))
  })

  it.each(['jra', 'all'] as const)('pins %s in the matching dry-run request and result label', async raceScope => {
    batch.value.loading = false
    batch.value.status = 'idle'
    batch.value.isExecutionLocked = false
    batch.value.jobId = null
    serve()
    const original = fetchMock.getMockImplementation()!
    const dry = Object.fromEntries([
      'total_target_count', 'unique_url_count', 'estimated_request_count', 'cache_hit_count',
      'cache_miss_count', 'resume_hit_count', 'skipped_count', 'db_existing_skip_count',
      'db_existing_race_count', 'db_existing_horse_count', 'db_existing_result_count',
      'db_existing_pedigree_count', 'new_fetch_required_count', 'already_covered_count',
      'estimated_runtime_sec',
    ].map(key => [key, 0]))
    fetchMock.mockImplementation(async (url: string, init: RequestInit = {}) => {
      if (url === '/api/scrape' && init.method === 'POST') {
        expect(JSON.parse(String(init.body))).toEqual({ start_date: '20260901', end_date: '20260930',
          force_rescrape: false, dry_run: true, race_scope: raceScope })
        return json({ job_id: JOB, status: 'queued' })
      }
      if (url === `/api/scrape/status/${JOB}`) return json({ job_id: JOB, status: 'completed',
        result: { fetch_summary: { mode: 'dry-run', race_scope: raceScope, dry_run: dry } } })
      return original(url, init)
    })
    render(<DataCollectionPage />)
    await waitFor(() => expect(screen.getByTestId('dry-run-button')).toBeEnabled())
    fireEvent.change(screen.getByTestId('start-period-input'), { target: { value: '2026-09' } })
    fireEvent.change(screen.getByTestId('end-period-input'), { target: { value: '2026-09' } })
    fireEvent.change(screen.getByTestId('scrape-race-scope'), { target: { value: raceScope } })
    fireEvent.click(screen.getByTestId('dry-run-button'))
    expect(await screen.findByTestId('dry-run-race-scope'))
      .toHaveTextContent(raceScope === 'jra' ? '中央競馬のみ' : '中央・地方競馬')
    expect(fetchMock.mock.calls.filter(([, init]) => init?.method === 'POST')).toHaveLength(1)
    expect(batch.value.start).not.toHaveBeenCalled()
  })

  it('rechecks central capability before preflight instead of trusting an older successful page check', async () => {
    batch.value.loading = false
    batch.value.status = 'idle'
    batch.value.isExecutionLocked = false
    batch.value.jobId = null
    serve()
    const original = fetchMock.getMockImplementation()!
    fetchMock.mockImplementation((url: string, init: RequestInit = {}) => url === '/api/health'
      ? Promise.resolve(json({ status: 'healthy' })) : original(url, init))
    render(<DataCollectionPage />)
    await waitFor(() => expect(screen.getByTestId('dry-run-button')).toBeEnabled())
    fireEvent.change(screen.getByTestId('start-period-input'), { target: { value: '2026-09' } })
    fireEvent.change(screen.getByTestId('end-period-input'), { target: { value: '2026-09' } })
    fireEvent.click(screen.getByTestId('dry-run-button'))
    expect(await screen.findByTestId('jra-scrape-server-pending')).toHaveTextContent('サーバーへの反映待ち')
    expect(fetchMock.mock.calls.some(([url]) => url === '/api/health')).toBe(true)
    expect(fetchMock.mock.calls.some(([, init]) => init?.method === 'POST')).toBe(false)
    expect(batch.value.start).not.toHaveBeenCalled()
  })

  it('explains that restored dry-run timing is not the whole acquisition ETA', async () => {
    batch.value.loading = false
    batch.value.status = 'idle'
    batch.value.isExecutionLocked = false
    batch.value.jobId = null
    localStorage.setItem('keiba-ai-pro:active-dry-run-job:v2:owner-a', JSON.stringify({
      ownerUserId: 'owner-a', jobId: JOB, startedAt: Date.parse('2026-10-12T07:00:00Z'),
      startDate: '20260901', endDate: '20260930', batchStartPeriod: '2026-09',
      batchEndPeriod: '2026-09', monthIndex: 0, totalMonths: 1,
    }))
    const dry = {
      total_target_count: 100, unique_url_count: 100, estimated_request_count: 5,
      cache_hit_count: 0, cache_miss_count: 5, resume_hit_count: 0, skipped_count: 95,
      db_existing_skip_count: 95, db_existing_race_count: 95, db_existing_horse_count: 1000,
      db_existing_result_count: 1000, db_existing_pedigree_count: 50,
      new_fetch_required_count: 5, already_covered_count: 95, estimated_runtime_sec: 5,
    }
    serve([{ job_id: JOB, status: 'completed', request_payload: { dry_run: true },
      result: { fetch_summary: { mode: 'dry-run', dry_run: dry } } }])
    render(<DataCollectionPage />)
    const summary = await screen.findByTestId('dry-run-result')
    expect(summary).toHaveTextContent('通信の最低目安')
    expect(summary).toHaveTextContent('全体の所要時間ではありません')
    expect(screen.getByTestId('dry-run-race-scope')).toHaveTextContent('中央・地方競馬')
    expect(batch.value.start).not.toHaveBeenCalled()
  })
})
