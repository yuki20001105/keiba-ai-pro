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

    await expect(page.getByRole('button', { name: 'Dry-run' })).toBeVisible()
    await expect(page.getByText('Dry-run は HTTPアクセスを実行しません')).toBeVisible()
    await page.getByTestId('start-period-input').fill('2026-01')
    await page.getByTestId('end-period-input').fill('2026-01')

    await page.getByRole('button', { name: 'Dry-run' }).click()

    await expect(page.getByText('Dry-run 実行中')).toBeVisible()
    await expect(page.getByText('見積もり生成中')).toBeVisible()
    await expect(page.getByText('HTTPアクセスは実行していません')).toBeVisible()
    await expect(page.getByText(/経過秒:\s*\d+\s*sec/)).toBeVisible()
    await expect(page.getByText('Dry-run 結果（実取得なし）')).not.toBeVisible()
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
    await expect(result.getByText('Dry-run 結果（実取得なし）')).toBeVisible()
    await expect(result.getByText('新規取得', { exact: true }).locator('..')).toContainText('8')
    await expect(result.getByText('既存データ', { exact: true }).locator('..')).toContainText('26')
    await expect(result.getByText('HTTP予定', { exact: true }).locator('..')).toContainText('8')
    await expect(result.getByText('推定時間', { exact: true }).locator('..')).toContainText('8 sec')
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
    await page.getByRole('button', { name: 'Dry-run' }).click()

    await expect(page.getByTestId('dry-run-error')).toContainText(
      'Dry-run結果を取得できませんでした。期間を短くするか、再実行してください。'
    )
    await expect(page.getByText('Dry-run 結果（実取得なし）')).not.toBeVisible()
  })

  test('Dry-run未実行で本実行するとwarnが表示される', async ({ page }) => {
    page.on('dialog', dialog => dialog.dismiss())

    await page.goto('/data-collection')
    await page.getByRole('button', { name: '取得開始' }).click()

    await expect(page.getByText('Dry-run未実行です。本実行は可能ですが、推定アクセス数の確認を推奨します。')).toBeVisible()
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

    await expect(page.getByTestId('dry-run-error')).toContainText('別のデータ取得が実行中です')
    await expect(page.getByTestId('dry-run-error')).not.toContainText('owner-active-job')
    const active = page.getByTestId('active-scrape-job')
    await expect(active).toBeVisible()
    await expect(active).toContainText('対象期間: 2020/01/01 ～ 2020/01/31')
    await expect(active).toContainText('開始時刻: 2026/9/12 2:28:04')
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
    let markStaleRequestStarted!: () => void
    let releaseStaleResponse!: () => void
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
    await page.route('/api/scrape', route => route.fulfill({
      status: 409,
      json: { detail: 'owner-active-job' },
    }))

    await page.goto('/data-collection')
    await page.getByTestId('latest-fetch-summary').getByRole('button', { name: '更新' }).click()
    await staleRequestStarted

    await page.getByTestId('dry-run-button').click()
    await expect(page.getByTestId('execute-button')).toBeDisabled()

    releaseStaleResponse()

    await expect(page.getByTestId('active-scrape-job')).toContainText('別のデータ取得が実行中です')
    await expect(page.getByTestId('dry-run-error')).toContainText('別のデータ取得が実行中です')
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

    await expect(page.getByRole('main').getByText('複数月の集計は完了していません')).toBeVisible()
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
})
