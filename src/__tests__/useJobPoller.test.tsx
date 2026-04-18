/**
 * useJobPoller フックの状態遷移テスト
 *
 * Python 側の test_train_inference_consistency.py（整合性テスト）に相当。
 * fetch をモックし、vi.useFakeTimers() でタイマーを制御する。
 *
 * テスト対象の状態遷移:
 *   idle → (jobId 設定) → running
 *   running → (completed レスポンス) → completed
 *   running → (error レスポンス) → error
 *   running → (not_found レスポンス) → error
 *   running → (timeout 超過) → error
 *   any → (reset()) → idle
 *
 * 実行:
 *   npm test
 *   npm run test:watch -- --reporter=verbose
 */
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest'
import { renderHook, act } from '@testing-library/react'
import { useJobPoller } from '@/hooks/useJobPoller'

// ─────────────────────────────────────────────────────────
// フェイクタイマー + fetch モックの共通セットアップ
// ─────────────────────────────────────────────────────────

/** テスト用 fetch モックを作成する */
function mockFetch(response: object) {
  return vi.spyOn(global, 'fetch').mockResolvedValue({
    ok: true,
    json: async () => response,
  } as Response)
}

/** intervalMs 分タイマーを進めてマイクロタスクを完結させる */
async function tick(ms: number) {
  await act(async () => {
    await vi.advanceTimersByTimeAsync(ms)
  })
}

beforeEach(() => {
  vi.useFakeTimers()
})

afterEach(() => {
  vi.useRealTimers()
  vi.restoreAllMocks()
})

// ─────────────────────────────────────────────────────────
// 初期状態
// ─────────────────────────────────────────────────────────
describe('初期状態', () => {
  it('jobId=null のとき status は idle', () => {
    const { result } = renderHook(() =>
      useJobPoller({ jobId: null, getStatusUrl: id => `/api/status/${id}` })
    )
    expect(result.current.status).toBe('idle')
    expect(result.current.progress).toBe('')
  })
})

// ─────────────────────────────────────────────────────────
// running 遷移
// ─────────────────────────────────────────────────────────
describe('running 遷移', () => {
  it('jobId が設定されると即座に running になる', () => {
    mockFetch({ status: 'running' })

    const { result } = renderHook(() =>
      useJobPoller({
        jobId: 'job-001',
        getStatusUrl: id => `/api/status/${id}`,
        intervalMs: 1000,
      })
    )
    expect(result.current.status).toBe('running')
  })

  it('running レスポンスの progress メッセージが反映される', async () => {
    mockFetch({ status: 'running', progress: '学習中... 20%' })

    const { result } = renderHook(() =>
      useJobPoller({
        jobId: 'job-001',
        getStatusUrl: id => `/api/status/${id}`,
        intervalMs: 1000,
      })
    )
    await tick(1000)
    expect(result.current.progress).toBe('学習中... 20%')
  })
})

// ─────────────────────────────────────────────────────────
// completed 遷移
// ─────────────────────────────────────────────────────────
describe('completed 遷移', () => {
  it('completed レスポンスで status が completed になる', async () => {
    mockFetch({ status: 'completed', result: 'model_v1' })

    const { result } = renderHook(() =>
      useJobPoller({
        jobId: 'job-001',
        getStatusUrl: id => `/api/status/${id}`,
        intervalMs: 1000,
      })
    )
    await tick(1000)
    expect(result.current.status).toBe('completed')
  })

  it('onCompleted コールバックがレスポンス全体を受け取る', async () => {
    const payload = { status: 'completed', result: 'model_v1', accuracy: 0.85 }
    mockFetch(payload)
    const onCompleted = vi.fn()

    renderHook(() =>
      useJobPoller({
        jobId: 'job-001',
        getStatusUrl: id => `/api/status/${id}`,
        intervalMs: 1000,
        onCompleted,
      })
    )
    await tick(1000)
    expect(onCompleted).toHaveBeenCalledOnce()
    expect(onCompleted).toHaveBeenCalledWith(payload)
  })

  it('completed 後はタイマーが停止しポーリングしない', async () => {
    const fetchSpy = mockFetch({ status: 'completed' })

    renderHook(() =>
      useJobPoller({
        jobId: 'job-001',
        getStatusUrl: id => `/api/status/${id}`,
        intervalMs: 1000,
      })
    )
    await tick(1000) // 1回目 → completed
    const callsAfterComplete = fetchSpy.mock.calls.length
    await tick(3000) // 追加 3 回分を送っても呼ばれない
    expect(fetchSpy.mock.calls.length).toBe(callsAfterComplete)
  })
})

// ─────────────────────────────────────────────────────────
// error 遷移
// ─────────────────────────────────────────────────────────
describe('error 遷移', () => {
  it('error レスポンスで status が error になる', async () => {
    mockFetch({ status: 'error', error: 'モデルエラー' })

    const { result } = renderHook(() =>
      useJobPoller({
        jobId: 'job-001',
        getStatusUrl: id => `/api/status/${id}`,
        intervalMs: 1000,
      })
    )
    await tick(1000)
    expect(result.current.status).toBe('error')
  })

  it('onError コールバックがエラーメッセージを受け取る', async () => {
    mockFetch({ status: 'error', error: 'モデルエラー' })
    const onError = vi.fn()

    renderHook(() =>
      useJobPoller({
        jobId: 'job-001',
        getStatusUrl: id => `/api/status/${id}`,
        intervalMs: 1000,
        onError,
      })
    )
    await tick(1000)
    expect(onError).toHaveBeenCalledWith('モデルエラー')
  })

  it('error レスポンスに error フィールドがないときデフォルトメッセージ', async () => {
    mockFetch({ status: 'error' })
    const onError = vi.fn()

    renderHook(() =>
      useJobPoller({
        jobId: 'job-001',
        getStatusUrl: id => `/api/status/${id}`,
        intervalMs: 1000,
        onError,
      })
    )
    await tick(1000)
    expect(onError).toHaveBeenCalledWith('エラーが発生しました')
  })

  it('not_found レスポンスで status が error になる', async () => {
    mockFetch({ status: 'not_found' })
    const onError = vi.fn()

    const { result } = renderHook(() =>
      useJobPoller({
        jobId: 'job-001',
        getStatusUrl: id => `/api/status/${id}`,
        intervalMs: 1000,
        onError,
      })
    )
    await tick(1000)
    expect(result.current.status).toBe('error')
    expect(onError).toHaveBeenCalledWith(
      'ジョブが見つかりません（サーバーが再起動した可能性があります）'
    )
  })
})

// ─────────────────────────────────────────────────────────
// タイムアウト
// ─────────────────────────────────────────────────────────
describe('タイムアウト', () => {
  it('maxMs を超えると status が error になる', async () => {
    mockFetch({ status: 'running' })
    const onError = vi.fn()

    const { result } = renderHook(() =>
      useJobPoller({
        jobId: 'job-001',
        getStatusUrl: id => `/api/status/${id}`,
        intervalMs: 1000,
        maxMs: 1500,  // t=2000 で elapsed=2000 > 1500 → タイムアウト
        onError,
      })
    )
    await tick(2000)
    expect(result.current.status).toBe('error')
    expect(result.current.progress).toBe('タイムアウト')
    expect(onError).toHaveBeenCalledWith('ジョブがタイムアウトしました')
  })

  it('タイムアウト前は running のまま', async () => {
    mockFetch({ status: 'running' })

    const { result } = renderHook(() =>
      useJobPoller({
        jobId: 'job-001',
        getStatusUrl: id => `/api/status/${id}`,
        intervalMs: 1000,
        maxMs: 5000,
      })
    )
    await tick(2000) // 2秒 — まだ範囲内
    expect(result.current.status).toBe('running')
  })
})

// ─────────────────────────────────────────────────────────
// reset()
// ─────────────────────────────────────────────────────────
describe('reset()', () => {
  it('running 中に reset() すると idle に戻る', async () => {
    mockFetch({ status: 'running' })

    const { result } = renderHook(() =>
      useJobPoller({
        jobId: 'job-001',
        getStatusUrl: id => `/api/status/${id}`,
        intervalMs: 1000,
      })
    )
    expect(result.current.status).toBe('running')
    act(() => result.current.reset())
    expect(result.current.status).toBe('idle')
    expect(result.current.progress).toBe('')
  })

  it('reset() 後はタイマーが停止してポーリングしない', async () => {
    const fetchSpy = mockFetch({ status: 'running' })

    const { result } = renderHook(() =>
      useJobPoller({
        jobId: 'job-001',
        getStatusUrl: id => `/api/status/${id}`,
        intervalMs: 1000,
      })
    )
    act(() => result.current.reset())
    const callsAtReset = fetchSpy.mock.calls.length
    await tick(3000)
    expect(fetchSpy.mock.calls.length).toBe(callsAtReset)
  })
})

// ─────────────────────────────────────────────────────────
// ネットワークエラー耐性
// ─────────────────────────────────────────────────────────
describe('ネットワークエラー耐性', () => {
  it('res.ok=false のときポーリングをスキップして running のまま', async () => {
    vi.spyOn(global, 'fetch').mockResolvedValue({
      ok: false,
      json: async () => ({}),
    } as Response)

    const { result } = renderHook(() =>
      useJobPoller({
        jobId: 'job-001',
        getStatusUrl: id => `/api/status/${id}`,
        intervalMs: 1000,
      })
    )
    await tick(1000)
    expect(result.current.status).toBe('running')
  })

  it('fetch が例外を投げてもポーリングが継続する', async () => {
    vi.spyOn(global, 'fetch').mockRejectedValue(new Error('Network error'))

    const { result } = renderHook(() =>
      useJobPoller({
        jobId: 'job-001',
        getStatusUrl: id => `/api/status/${id}`,
        intervalMs: 1000,
      })
    )
    await tick(2000)
    // エラーを無視して running を維持
    expect(result.current.status).toBe('running')
  })
})
