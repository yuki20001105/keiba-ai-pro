import { test, expect } from '@playwright/test'
import { mockAuth, mockDataStats } from './helpers/mock-api'

test.describe('データ取得 Dry-run UI', () => {
  test.beforeEach(async ({ page }) => {
    await mockAuth(page)
    await mockDataStats(page)
    await page.route('/api/scrape/health**', route =>
      route.fulfill({ status: 200, json: { status: 'healthy' } })
    )
    await page.route('/api/scrape/history**', route =>
      route.fulfill({ status: 200, json: { count: 0, jobs: [] } })
    )
  })

  test('Dry-runボタンと結果カードを表示できる', async ({ page }) => {
    const storageKey = 'keiba-ai-pro:active-dry-run-job:v2:e2e-user-id'
    let dryRunRequestBody: Record<string, unknown> | null = null

    await page.route('/api/scrape', async route => {
      if (route.request().method() !== 'POST') {
        return route.continue()
      }
      const body = route.request().postDataJSON() as Record<string, unknown>
      if (body?.dry_run === true) {
        dryRunRequestBody = body
        return route.fulfill({ status: 200, json: { job_id: 'dry-run-job-001', status: 'queued', mode: 'dry-run' } })
      }
      return route.fulfill({ status: 200, json: { job_id: 'exec-job-001', status: 'queued' } })
    })

    let allowDryRunCompletion = false
    await page.route('/api/scrape/status/dry-run-job-001**', route => {
      if (!allowDryRunCompletion) {
        return route.fulfill({ status: 200, json: { status: 'running', progress: 'planning' } })
      }
      return route.fulfill({
        status: 200,
        json: {
          status: 'completed',
          result: {
            success: true,
            dry_run: true,
            fetch_summary: {
              dry_run: {
                total_target_count: 24,
                unique_url_count: 20,
                estimated_request_count: 8,
                cache_hit_count: 10,
                cache_miss_count: 10,
                resume_hit_count: 2,
                skipped_count: 12,
                db_existing_skip_count: 14,
                db_existing_race_count: 7,
                db_existing_horse_count: 96,
                db_existing_result_count: 7,
                db_existing_pedigree_count: 94,
                new_fetch_required_count: 8,
                already_covered_count: 26,
                estimated_runtime_sec: 8,
              },
              rate_limit_policy: {
                min_interval_sec: 1.0,
                scope: 'per-host',
              },
              retry_backoff_policy: {
                max_retries: 3,
                backoff: { type: 'exponential_with_jitter' },
                retry_after: 'respected',
              },
              circuit_breaker_policy: {
                failure_threshold: 3,
                cooldown_sec: 120,
              },
            },
          },
        },
      })
    })

    await page.goto('/data-collection')

    await expect(page.getByRole('button', { name: '事前確認' })).toBeVisible()
    await expect(page.getByText('事前確認は通信なし')).toHaveCount(0)
    await page.getByTestId('start-period-input').fill('2026-01')
    await page.getByTestId('end-period-input').fill('2026-01')

    await page.getByRole('button', { name: '事前確認' }).click()

    const progress = page.getByTestId('dry-run-progress')
    await expect(progress).toContainText('確認中 1/1')
    await expect(progress).toContainText(/\d+秒/)
    await expect(page.getByTestId('dry-run-result')).not.toBeVisible()
    await expect(page.getByTestId('execute-button')).toBeDisabled()
    await expect(page.locator('input[type="month"]').first()).toBeDisabled()
    await expect(page.locator('input[type="month"]').nth(1)).toBeDisabled()
    await expect(page.getByTestId('force-rescrape-input')).toHaveCount(0)
    await expect.poll(() => page.evaluate(key => {
      const raw = localStorage.getItem(key)
      return raw ? JSON.parse(raw).jobId : null
    }, storageKey)).toBe('dry-run-job-001')

    allowDryRunCompletion = true
    const result = page.getByTestId('dry-run-result')
    await expect(result.getByText('事前確認', { exact: true })).toBeVisible()
    await expect(result.getByText('新規', { exact: true }).locator('..')).toContainText('8')
    await expect(result.getByText('既存', { exact: true }).locator('..')).toContainText('26')
    await expect(result.getByText('HTTP', { exact: true })).toHaveCount(0)
    await expect(result.getByText('時間', { exact: true }).locator('..')).toContainText('8秒')
    expect(dryRunRequestBody).toMatchObject({ dry_run: true, force_rescrape: false })
    await expect.poll(() => page.evaluate(key => localStorage.getItem(key), storageKey)).toBeNull()
    await expect(page.getByText('rate limit policy')).toHaveCount(0)
  })

  test('Dry-runエラー時に0件ではなくエラーメッセージを表示する', async ({ page }) => {
    await page.route('/api/scrape', async route => {
      if (route.request().method() !== 'POST') {
        return route.continue()
      }
      return route.fulfill({
        status: 500,
        json: { detail: 'Dry-run結果を取得できませんでした。期間を短くするか、再実行してください。' },
      })
    })

    await page.goto('/data-collection')
    await page.getByRole('button', { name: '事前確認' }).click()

    await expect(page.getByTestId('dry-run-error')).toContainText(
      'Dry-run結果を取得できませんでした。期間を短くするか、再実行してください。'
    )
    await expect(page.getByTestId('dry-run-result')).not.toBeVisible()
  })

  test('事前確認が未完了なら確認ダイアログだけで知らせる', async ({ page }) => {
    let dialogMessage = ''
    page.once('dialog', dialog => {
      dialogMessage = dialog.message()
      void dialog.dismiss()
    })

    await page.goto('/data-collection')
    await page.getByRole('button', { name: '取得開始' }).click()

    expect(dialogMessage).toContain('事前確認は未完了です。')
    await expect(page.getByText('事前確認が未完了です。')).toHaveCount(0)
  })

  test('owner-active-jobを日本語表示し、実行中情報を自動確認して完了後に解除する', async ({ page }) => {
    let historyCalls = 0
    let allowCompletion = false

    await page.route('/api/scrape/history**', route => {
      historyCalls += 1
      if (historyCalls === 1) {
        return route.fulfill({ status: 200, json: { count: 0, jobs: [] } })
      }
      if (!allowCompletion) {
        return route.fulfill({
          status: 200,
          json: {
            count: 1,
            jobs: [{
              job_id: 'active-job-001',
              status: 'running',
              created_at: '2026-09-11T17:27:04Z',
              updated_at: '2026-09-11T17:28:04Z',
              request_payload: {
                start_date: '20200101',
                end_date: '20200131',
                force_rescrape: false,
                dry_run: false,
              },
            }],
          },
        })
      }
      return route.fulfill({
        status: 200,
        json: {
          count: 1,
          jobs: [{
            job_id: 'active-job-001',
            status: 'completed',
            created_at: '2026-09-11T17:27:04Z',
            updated_at: '2026-09-11T18:00:00Z',
            request_payload: {
              start_date: '20200101',
              end_date: '20200131',
              force_rescrape: false,
              dry_run: false,
            },
            result: {
              fetch_summary: {
                mode: 'execute',
                start_date: '20200101',
                end_date: '20200131',
                saved_races: 10,
                saved_horses: 153,
                elapsed_time_sec: 120,
              },
            },
          }],
        },
      })
    })
    await page.route('/api/scrape', route => route.fulfill({
      status: 409,
      json: { detail: 'owner-active-job' },
    }))

    await page.goto('/data-collection')
    await expect(page.getByTestId('dry-run-button')).toBeEnabled()
    await page.getByTestId('dry-run-button').click()

    const active = page.getByTestId('active-scrape-job')
    await expect(active).toBeVisible()
    await expect(active).toContainText('別の取得が実行中')
    await expect(page.getByTestId('dry-run-error')).toHaveCount(0)
    await expect(active).toContainText('期間: 2020/01/01～2020/01/31')
    await expect(active).toContainText('開始: 2026/9/12 2:28:04')
    await expect(page.getByTestId('dry-run-button')).toBeDisabled()
    await expect(page.getByTestId('execute-button')).toBeDisabled()

    allowCompletion = true
    await expect(active).toHaveCount(0)
    await expect(page.getByTestId('dry-run-button')).toBeEnabled()
    await expect(page.getByTestId('execute-button')).toBeEnabled()
    expect(historyCalls).toBeGreaterThanOrEqual(3)
  })

  test('409より前の古い履歴確認を待った後、競合後の履歴を必ず再取得する', async ({ page }) => {
    let historyCalls = 0
    let markStartRequestStarted!: () => void
    let releaseStartResponse!: () => void
    let markStaleRequestStarted!: () => void
    let releaseStaleResponse!: () => void
    const startRequestStarted = new Promise<void>(resolve => { markStartRequestStarted = resolve })
    const startResponseGate = new Promise<void>(resolve => { releaseStartResponse = resolve })
    const staleRequestStarted = new Promise<void>(resolve => { markStaleRequestStarted = resolve })
    const staleResponseGate = new Promise<void>(resolve => { releaseStaleResponse = resolve })

    const completedJob = {
      job_id: 'completed-job-before-conflict',
      status: 'completed',
      updated_at: '2026-09-11T17:00:00Z',
      result: {
        fetch_summary: {
          mode: 'execute',
          start_date: '20260801',
          end_date: '20260831',
          saved_races: 1,
          saved_horses: 10,
          elapsed_time_sec: 5,
        },
      },
    }

    await page.route('/api/scrape/history**', async route => {
      historyCalls += 1
      if (historyCalls === 1) {
        return route.fulfill({ status: 200, json: { count: 1, jobs: [completedJob] } })
      }
      if (historyCalls === 2) {
        markStaleRequestStarted()
        await staleResponseGate
        return route.fulfill({ status: 200, json: { count: 0, jobs: [] } })
      }
      return route.fulfill({
        status: 200,
        json: {
          count: 1,
          jobs: [{
            job_id: 'active-job-after-conflict',
            status: 'running',
            created_at: '2026-09-11T17:27:04Z',
            updated_at: '2026-09-11T17:28:04Z',
            request_payload: {
              start_date: '20200101',
              end_date: '20200131',
              force_rescrape: false,
              dry_run: false,
            },
          }],
        },
      })
    })
    await page.route('/api/scrape', async route => {
      if (route.request().method() !== 'POST') return route.fallback()
      markStartRequestStarted()
      await startResponseGate
      return route.fulfill({
        status: 409,
        json: { detail: 'owner-active-job' },
      })
    })

    await page.goto('/data-collection')
    await page.getByTestId('dry-run-button').click()
    await startRequestStarted

    await page.getByTestId('refresh-history-button').click()
    await staleRequestStarted
    await expect(page.getByTestId('execute-button')).toBeDisabled()

    releaseStartResponse()
    await expect.poll(() => historyCalls).toBe(2)
    releaseStaleResponse()

    await expect(page.getByTestId('active-scrape-job')).toContainText('別の取得が実行中')
    await expect(page.getByTestId('dry-run-error')).toHaveCount(0)
    await expect(page.getByTestId('dry-run-button')).toBeDisabled()
    await expect(page.getByTestId('execute-button')).toBeDisabled()
    expect(historyCalls).toBeGreaterThanOrEqual(3)
  })

  test('60秒を超えた保存済みDry-runを同じjob IDで再開し、追加POSTしない', async ({ page }) => {
    const storageKey = 'keiba-ai-pro:active-dry-run-job:v2:e2e-user-id'
    let postCount = 0
    let statusPolls = 0

    await page.addInitScript(({ key, startedAt }) => {
      localStorage.setItem(key, JSON.stringify({
        ownerUserId: 'e2e-user-id',
        jobId: 'dry-run-long-001',
        startedAt,
        startDate: '20260101',
        endDate: '20260131',
        batchStartPeriod: '2026-01',
        batchEndPeriod: '2026-01',
        monthIndex: 0,
        totalMonths: 1,
      }))
    }, { key: storageKey, startedAt: Date.now() - 61_000 })
    await page.route('/api/scrape', route => {
      if (route.request().method() === 'POST') postCount += 1
      return route.fulfill({ status: 500, json: { detail: 'unexpected POST' } })
    })
    await page.route('/api/scrape/status/dry-run-long-001**', route => {
      statusPolls += 1
      if (statusPolls === 1) {
        return route.fulfill({
          status: 200,
          json: {
            status: 'running',
            created_at: new Date(Date.now() - 61_000).toISOString(),
            request_payload: { start_date: '20260101', end_date: '20260131', dry_run: true },
          },
        })
      }
      return route.fulfill({
        status: 200,
        json: {
          status: 'completed',
          result: {
            fetch_summary: {
              dry_run: {
                total_target_count: 24,
                unique_url_count: 20,
                estimated_request_count: 8,
                cache_hit_count: 10,
                cache_miss_count: 10,
                resume_hit_count: 2,
                skipped_count: 12,
                db_existing_skip_count: 14,
                db_existing_race_count: 7,
                db_existing_horse_count: 96,
                db_existing_result_count: 7,
                db_existing_pedigree_count: 94,
                new_fetch_required_count: 8,
                already_covered_count: 26,
                estimated_runtime_sec: 8,
              },
            },
          },
        },
      })
    })

    await page.goto('/data-collection')

    await expect(page.getByTestId('dry-run-result')).toBeVisible()
    expect(statusPolls).toBeGreaterThanOrEqual(2)
    expect(postCount).toBe(0)
    await expect.poll(() => page.evaluate(key => localStorage.getItem(key), storageKey)).toBeNull()
  })

  test('複数月の途中で再読み込みした場合は1か月だけで全期間完了扱いにしない', async ({ page }) => {
    const storageKey = 'keiba-ai-pro:active-dry-run-job:v2:e2e-user-id'
    let postCount = 0

    await page.addInitScript(({ key, startedAt }) => {
      localStorage.setItem(key, JSON.stringify({
        ownerUserId: 'e2e-user-id',
        jobId: 'dry-run-partial-001',
        startedAt,
        startDate: '20260201',
        endDate: '20260228',
        batchStartPeriod: '2026-01',
        batchEndPeriod: '2026-03',
        monthIndex: 1,
        totalMonths: 3,
      }))
    }, { key: storageKey, startedAt: Date.now() - 61_000 })
    await page.route('/api/scrape', route => {
      if (route.request().method() === 'POST') postCount += 1
      return route.fulfill({ status: 500, json: { detail: 'unexpected POST' } })
    })
    await page.route('/api/scrape/status/dry-run-partial-001**', route => route.fulfill({
      status: 200,
      json: {
        status: 'completed',
        result: {
          fetch_summary: {
            dry_run: {
              total_target_count: 1,
              unique_url_count: 1,
              estimated_request_count: 1,
              cache_hit_count: 0,
              cache_miss_count: 1,
              resume_hit_count: 0,
              skipped_count: 0,
              db_existing_skip_count: 0,
              db_existing_race_count: 0,
              db_existing_horse_count: 0,
              db_existing_result_count: 0,
              db_existing_pedigree_count: 0,
              new_fetch_required_count: 1,
              already_covered_count: 0,
              estimated_runtime_sec: 1,
            },
          },
        },
      },
    }))

    await page.goto('/data-collection')

    await expect(page.getByRole('main').getByText('事前確認をやり直してください。', { exact: true })).toBeVisible()
    await expect(page.getByTestId('dry-run-result')).toHaveCount(0)
    expect(postCount).toBe(0)
    await expect.poll(() => page.evaluate(key => localStorage.getItem(key), storageKey)).toBeNull()
  })

  test('別ユーザーへ誤帰属し得る旧グローバル保存キーは採用しない', async ({ page }) => {
    let statusPolls = 0
    await page.addInitScript(() => {
      localStorage.setItem('keiba-ai-pro:active-dry-run-job:v1', JSON.stringify({
        jobId: 'foreign-user-job',
        startedAt: Date.now() - 61_000,
        startDate: '20260101',
        endDate: '20260131',
      }))
    })
    await page.route('/api/scrape/status/**', route => {
      statusPolls += 1
      return route.fulfill({ status: 200, json: { status: 'not_found' } })
    })

    await page.goto('/data-collection')

    await expect(page.getByTestId('dry-run-button')).toBeEnabled()
    await expect(page.getByTestId('execute-button')).toBeEnabled()
    expect(statusPolls).toBe(0)
  })

  test('現在ユーザーの保存済みDry-runが壊れている場合はfail-closedにする', async ({ page }) => {
    const storageKey = 'keiba-ai-pro:active-dry-run-job:v2:e2e-user-id'
    await page.addInitScript(key => {
      localStorage.setItem(key, JSON.stringify({ ownerUserId: 'e2e-user-id', jobId: 'missing-fields' }))
    }, storageKey)

    await page.goto('/data-collection')

    await expect(page.getByTestId('active-job-check-error')).toContainText('保存済みDry-runジョブ')
    await expect(page.getByTestId('dry-run-button')).toBeDisabled()
    await expect(page.getByTestId('execute-button')).toBeDisabled()
    await expect.poll(() => page.evaluate(key => localStorage.getItem(key), storageKey)).not.toBeNull()
  })

  test('画面移動時は監視だけを終了し、保存済みjob IDは残す', async ({ page }) => {
    const storageKey = 'keiba-ai-pro:active-dry-run-job:v2:e2e-user-id'
    let statusPolls = 0
    await page.addInitScript(({ key, startedAt }) => {
      localStorage.setItem(key, JSON.stringify({
        ownerUserId: 'e2e-user-id',
        jobId: 'dry-run-navigation-001',
        startedAt,
        startDate: '20260101',
        endDate: '20260131',
        batchStartPeriod: '2026-01',
        batchEndPeriod: '2026-01',
        monthIndex: 0,
        totalMonths: 1,
      }))
    }, { key: storageKey, startedAt: Date.now() - 61_000 })
    await page.route('/api/scrape/status/dry-run-navigation-001**', route => {
      statusPolls += 1
      return route.fulfill({ status: 200, json: { status: 'running' } })
    })

    await page.goto('/data-collection')
    await expect.poll(() => statusPolls).toBeGreaterThanOrEqual(1)
    await page.goto('/home')
    await page.waitForTimeout(100)
    const pollsAfterNavigation = statusPolls
    await page.waitForTimeout(1_300)

    expect(statusPolls).toBe(pollsAfterNavigation)
    await expect.poll(() => page.evaluate(key => localStorage.getItem(key), storageKey)).not.toBeNull()
  })

  test('履歴の空応答が確定するまでは開始を禁止し、その後の新規activeカードを維持する', async ({ page }) => {
    const jobId = '66666666-6666-4666-8666-666666666666'
    let historyCalls = 0
    let postCount = 0
    let jobStatus: 'running' | 'completed' = 'running'
    let releasePendingHistory!: () => void
    const pendingHistoryGate = new Promise<void>(resolve => {
      releasePendingHistory = resolve
    })

    await page.route('/api/scrape/history**', async route => {
      historyCalls += 1
      if (historyCalls === 1) {
        return route.fulfill({
          status: 200,
          json: {
            count: 1,
            jobs: [{
              job_id: 'completed-before-pending-history',
              status: 'completed',
              result: {
                fetch_summary: {
                  mode: 'execute',
                  start_date: '20251201',
                  end_date: '20251231',
                  saved_races: 1,
                },
              },
            }],
          },
        })
      }
      if (historyCalls === 2) await pendingHistoryGate
      if (postCount === 0) {
        return route.fulfill({ status: 200, json: { count: 0, jobs: [] } })
      }
      return route.fulfill({
        status: 200,
        json: {
          count: 1,
          jobs: [{
            job_id: jobId,
            status: jobStatus,
            created_at: '2026-09-12T08:00:00Z',
            request_payload: {
              start_date: '20260101',
              end_date: '20260131',
              force_rescrape: false,
              dry_run: false,
            },
            ...(jobStatus === 'completed' ? { result: { races_collected: 1 } } : {}),
          }],
        },
      })
    })
    await page.route('/api/scrape', route => {
      if (route.request().method() !== 'POST') return route.fallback()
      postCount += 1
      return route.fulfill({ status: 200, json: { job_id: jobId, status: 'queued' } })
    })
    await page.route(`/api/scrape/status/${jobId}**`, route => route.fulfill({
      status: 200,
      json: jobStatus === 'completed'
        ? { job_id: jobId, status: 'completed', result: { races_collected: 1 } }
        : { job_id: jobId, status: 'running', progress: { done: 1, total: 10 } },
    }))
    page.on('dialog', dialog => dialog.accept())

    await page.goto('/data-collection')
    await page.getByTestId('start-period-input').fill('2026-01')
    await page.getByTestId('end-period-input').fill('2026-01')
    await page.getByTestId('latest-fetch-summary').getByRole('button', { name: '更新' }).click()
    await expect.poll(() => historyCalls).toBe(2)

    await expect(page.getByTestId('dry-run-button')).toBeDisabled()
    await expect(page.getByTestId('execute-button')).toBeDisabled()
    expect(postCount).toBe(0)

    releasePendingHistory()
    await expect(page.getByTestId('dry-run-button')).toBeEnabled()
    await expect(page.getByTestId('execute-button')).toBeEnabled()
    await page.getByTestId('execute-button').click()

    const active = page.getByTestId('active-scrape-job')
    await expect(active).toContainText(`ID: ${jobId}`)
    await page.waitForTimeout(700)
    await expect(active).toBeVisible()
    expect(postCount).toBe(1)

    jobStatus = 'completed'
    await expect(active).toHaveCount(0)
  })

  test('同じ画面から開始した通常取得を履歴応答前から停止可能として表示する', async ({ page }) => {
    const jobId = '33333333-3333-4333-8333-333333333333'
    let postCount = 0
    let historyCalls = 0
    let jobStatus: 'running' | 'completed' = 'running'
    let releaseHistoryAfterStart!: () => void
    const historyAfterStartGate = new Promise<void>(resolve => {
      releaseHistoryAfterStart = resolve
    })

    await page.route('/api/scrape/history**', async route => {
      historyCalls += 1
      if (postCount === 0) {
        return route.fulfill({ status: 200, json: { count: 0, jobs: [] } })
      }
      await historyAfterStartGate
      return route.fulfill({
        status: 200,
        json: {
          count: 1,
          jobs: [{
            job_id: jobId,
            status: jobStatus,
            created_at: '2026-09-12T08:00:00Z',
            request_payload: {
              start_date: '20260101',
              end_date: '20260131',
              force_rescrape: false,
              dry_run: false,
            },
            ...(jobStatus === 'completed' ? { result: { races_collected: 1 } } : {}),
          }],
        },
      })
    })
    await page.route('/api/scrape', route => {
      if (route.request().method() !== 'POST') return route.fallback()
      postCount += 1
      return route.fulfill({
        status: 200,
        json: {
          job_id: jobId,
          status: 'queued',
          created_at: '2026-09-12T08:00:00Z',
        },
      })
    })
    await page.route(`/api/scrape/status/${jobId}**`, route => route.fulfill({
      status: 200,
      json: jobStatus === 'completed'
        ? { job_id: jobId, status: 'completed', result: { races_collected: 1 } }
        : { job_id: jobId, status: 'running', progress: { done: 1, total: 10 } },
    }))
    page.on('dialog', dialog => dialog.accept())

    await page.goto('/data-collection')
    await page.getByTestId('start-period-input').fill('2026-01')
    await page.getByTestId('end-period-input').fill('2026-01')
    await page.getByTestId('execute-button').click()

    const active = page.getByTestId('active-scrape-job')
    await expect(active).toBeVisible()
    await expect(active).toContainText(`ID: ${jobId}`)
    await expect(active).toContainText('期間: 2026/01/01～2026/01/31')
    await expect(page.getByTestId('cancel-active-job-button')).toHaveText('取消')
    await expect(page.getByTestId('cancel-active-job-button')).toBeEnabled()
    await expect.poll(() => historyCalls).toBeGreaterThanOrEqual(2)
    await expect(active).toBeVisible()

    jobStatus = 'completed'
    releaseHistoryAfterStart()
    await expect(active).toHaveCount(0)
    expect(postCount).toBe(1)
  })

  test('Dry-run停止と完了が競合した場合は完了結果を残して次月だけ停止する', async ({ page }) => {
    const storageKey = 'keiba-ai-pro:active-dry-run-job:v2:e2e-user-id'
    const jobId = '44444444-4444-4444-8444-444444444444'
    let jobStatus: 'running' | 'completed' = 'running'
    let postCount = 0
    let cancelCalls = 0
    const completedResult = {
      success: true,
      dry_run: true,
      fetch_summary: {
        dry_run: {
          total_target_count: 12,
          unique_url_count: 10,
          estimated_request_count: 4,
          cache_hit_count: 5,
          cache_miss_count: 5,
          resume_hit_count: 1,
          skipped_count: 6,
          db_existing_skip_count: 7,
          db_existing_race_count: 3,
          db_existing_horse_count: 48,
          db_existing_result_count: 3,
          db_existing_pedigree_count: 47,
          new_fetch_required_count: 4,
          already_covered_count: 13,
          estimated_runtime_sec: 4,
        },
        rate_limit_policy: {},
        retry_backoff_policy: {},
        circuit_breaker_policy: {},
      },
    }

    await page.route('/api/scrape', route => {
      if (route.request().method() !== 'POST') return route.fallback()
      postCount += 1
      return route.fulfill({ status: 200, json: { job_id: jobId, status: 'queued', mode: 'dry-run' } })
    })
    await page.route(`/api/scrape/status/${jobId}**`, route => route.fulfill({
      status: 200,
      json: jobStatus === 'completed'
        ? { job_id: jobId, status: 'completed', result: completedResult }
        : { job_id: jobId, status: 'running', progress: 'planning' },
    }))
    await page.route('/api/scrape/history**', route => route.fulfill({
      status: 200,
      json: postCount === 0
        ? { count: 0, jobs: [] }
        : {
          count: 1,
          jobs: [{
            job_id: jobId,
            status: jobStatus,
            created_at: '2026-09-12T08:00:00Z',
            request_payload: {
              start_date: '20260101',
              end_date: '20260131',
              force_rescrape: false,
              dry_run: true,
            },
            ...(jobStatus === 'completed' ? { result: completedResult } : {}),
          }],
        },
    }))
    await page.route(`/api/scrape/cancel/${jobId}`, route => {
      cancelCalls += 1
      jobStatus = 'completed'
      return route.fulfill({ status: 409, json: { detail: 'job-already-terminal' } })
    })
    page.on('dialog', dialog => dialog.accept())

    await page.goto('/data-collection')
    await page.getByTestId('start-period-input').fill('2026-01')
    await page.getByTestId('end-period-input').fill('2026-02')
    await page.getByTestId('dry-run-button').click()
    await expect(page.getByTestId('active-scrape-job')).toBeVisible()

    await page.getByTestId('cancel-active-job-button').click()
    await expect.poll(() => cancelCalls).toBe(1)

    const result = page.getByTestId('dry-run-result')
    await expect(result).toBeVisible()
    await expect(result).toContainText('新規')
    await expect(result).toContainText('4')
    await expect(page.getByText(
      '一部確認済み（次月は未実行）',
      { exact: true },
    )).toBeVisible()
    await page.waitForTimeout(1_200)
    expect(postCount).toBe(1)
    await expect.poll(() => page.evaluate(key => localStorage.getItem(key), storageKey)).toBeNull()
  })

  test('停止API失敗時は同じjobを保持して新規実行をlockし、自動再確認を続ける', async ({ page }) => {
    const jobId = '55555555-5555-4555-8555-555555555555'
    let historyCalls = 0
    let cancelCalls = 0

    await page.route('/api/scrape/history**', route => {
      historyCalls += 1
      return route.fulfill({
        status: 200,
        json: {
          count: 1,
          jobs: [{
            job_id: jobId,
            status: 'running',
            created_at: '2026-09-12T08:00:00Z',
            request_payload: {
              start_date: '20260101',
              end_date: '20260131',
              force_rescrape: false,
              dry_run: false,
            },
          }],
        },
      })
    })
    await page.route(`/api/scrape/cancel/${jobId}`, route => {
      cancelCalls += 1
      return route.fulfill({
        status: 403,
        json: { detail: 'Admin mode verification required' },
      })
    })
    page.on('dialog', dialog => dialog.accept())

    await page.goto('/data-collection')
    const active = page.getByTestId('active-scrape-job')
    await expect(active).toBeVisible()
    await page.getByTestId('cancel-active-job-button').click()

    await expect.poll(() => cancelCalls).toBe(1)
    await expect(active.getByRole('alert')).toContainText('管理機能の確認期限が切れた可能性があります')
    await expect(page.getByTestId('cancel-active-job-button')).toBeEnabled()
    await expect(page.getByTestId('dry-run-button')).toBeDisabled()
    await expect(page.getByTestId('execute-button')).toBeDisabled()
    const callsAfterFailure = historyCalls
    await expect.poll(() => historyCalls).toBeGreaterThan(callsAfterFailure)
    await expect(active).toContainText(`ID: ${jobId}`)
  })

  test('実行中ジョブの停止を一度だけ送信し、終端確認まで新規実行をlockする', async ({ page }) => {
    const jobId = '11111111-1111-4111-8111-111111111111'
    let jobStatus: 'running' | 'cancelling' | 'cancelled' = 'running'
    let historyCalls = 0
    let cancelCalls = 0

    await page.route('/api/scrape/history**', route => {
      historyCalls += 1
      return route.fulfill({
        status: 200,
        json: {
          count: 1,
          jobs: [{
            job_id: jobId,
            status: jobStatus,
            created_at: '2026-09-12T07:46:12Z',
            updated_at: '2026-09-12T07:47:40Z',
            cancel_requested_at: jobStatus === 'cancelling' || jobStatus === 'cancelled'
              ? '2026-09-12T07:50:00Z'
              : null,
            cancelled_at: jobStatus === 'cancelled' ? '2026-09-12T07:50:03Z' : null,
            request_payload: {
              start_date: '20250201',
              end_date: '20250228',
              force_rescrape: false,
              dry_run: false,
            },
          }],
        },
      })
    })
    await page.route(`/api/scrape/cancel/${jobId}`, route => {
      cancelCalls += 1
      jobStatus = 'cancelling'
      return route.fulfill({
        status: 202,
        json: {
          job_id: jobId,
          status: 'cancelling',
          cancel_requested_at: '2026-09-12T07:50:00Z',
          duplicate: false,
        },
      })
    })
    page.on('dialog', dialog => dialog.accept())

    await page.goto('/data-collection')
    const active = page.getByTestId('active-scrape-job')
    await expect(active).toContainText('別の取得が実行中')
    await page.getByTestId('cancel-active-job-button').click()

    await expect.poll(() => cancelCalls).toBe(1)
    await expect(page.getByTestId('cancel-active-job-button')).toHaveCount(0)
    await expect(active).toContainText('保存済みデータは残ります')
    await expect(page.getByTestId('dry-run-button')).toBeDisabled()
    await expect(page.getByTestId('execute-button')).toBeDisabled()

    jobStatus = 'cancelled'
    await expect(active).toHaveCount(0)
    await expect(page.getByTestId('dry-run-button')).toBeEnabled()
    await expect(page.getByTestId('execute-button')).toBeEnabled()
    expect(cancelCalls).toBe(1)
    expect(historyCalls).toBeGreaterThanOrEqual(2)
  })

  test('Dry-runの停止中はjob IDを保持し、cancelled確認後だけ削除する', async ({ page }) => {
    const storageKey = 'keiba-ai-pro:active-dry-run-job:v2:e2e-user-id'
    const jobId = '22222222-2222-4222-8222-222222222222'
    let jobStatus: 'running' | 'cancelling' | 'cancelled' = 'running'
    let postCount = 0
    let cancelCalls = 0

    await page.route('/api/scrape', route => {
      if (route.request().method() !== 'POST') return route.fallback()
      postCount += 1
      return route.fulfill({ status: 200, json: { job_id: jobId, status: 'queued', mode: 'dry-run' } })
    })
    await page.route(`/api/scrape/status/${jobId}**`, route => route.fulfill({
      status: 200,
      json: {
        job_id: jobId,
        status: jobStatus,
        created_at: '2026-09-12T07:46:12Z',
        cancel_requested_at: jobStatus === 'cancelling' ? '2026-09-12T07:50:00Z' : null,
        request_payload: {
          start_date: '20260101',
          end_date: '20260131',
          force_rescrape: false,
          dry_run: true,
        },
      },
    }))
    await page.route('/api/scrape/history**', route => route.fulfill({
      status: 200,
      json: postCount === 0
        ? { count: 0, jobs: [] }
        : {
          count: 1,
          jobs: [{
            job_id: jobId,
            status: jobStatus,
            created_at: '2026-09-12T07:46:12Z',
            cancel_requested_at: jobStatus === 'cancelling' ? '2026-09-12T07:50:00Z' : null,
            request_payload: {
              start_date: '20260101',
              end_date: '20260131',
              force_rescrape: false,
              dry_run: true,
            },
          }],
        },
    }))
    await page.route(`/api/scrape/cancel/${jobId}`, route => {
      cancelCalls += 1
      jobStatus = 'cancelling'
      return route.fulfill({
        status: 202,
        json: {
          job_id: jobId,
          status: 'cancelling',
          cancel_requested_at: '2026-09-12T07:50:00Z',
          duplicate: false,
        },
      })
    })
    page.on('dialog', dialog => dialog.accept())

    await page.goto('/data-collection')
    await page.getByTestId('start-period-input').fill('2026-01')
    await page.getByTestId('end-period-input').fill('2026-02')
    await page.getByTestId('dry-run-button').click()
    await expect.poll(() => page.evaluate(key => localStorage.getItem(key), storageKey)).not.toBeNull()

    await page.getByTestId('cancel-active-job-button').click()
    await expect.poll(() => cancelCalls).toBe(1)
    await expect(page.getByTestId('cancel-active-job-button')).toHaveCount(0)
    await expect(page.getByTestId('active-scrape-job')).toContainText('保存済みデータは残ります')
    await expect.poll(() => page.evaluate(key => localStorage.getItem(key), storageKey)).not.toBeNull()

    jobStatus = 'cancelled'
    await expect.poll(() => page.evaluate(key => localStorage.getItem(key), storageKey)).toBeNull()
    await expect(page.getByTestId('active-scrape-job')).toHaveCount(0)
    expect(postCount).toBe(1)
    expect(cancelCalls).toBe(1)
  })
})
