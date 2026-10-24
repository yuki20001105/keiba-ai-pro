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

  it('waiting_resources の詳細メッセージを表示して監視を継続する', async () => {
    const payload = {
      status: 'waiting_resources',
      progress: { message: 'メモリ解放待ち。自動再開します。' },
    }
    const fetchSpy = mockFetch(payload)
    const onProgress = vi.fn()

    const { result } = renderHook(() =>
      useJobPoller({
        jobId: 'job-001',
        getStatusUrl: id => `/api/status/${id}`,
        intervalMs: 1000,
        onProgress,
      })
    )
    await tick(1000)

    expect(result.current.status).toBe('waiting_resources')
    expect(result.current.progress).toBe('メモリ解放待ち。自動再開します。')
    expect(onProgress).toHaveBeenCalledWith(payload.progress.message, payload)

    await tick(1000)
    expect(fetchSpy).toHaveBeenCalledTimes(2)
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

  it('maxMs 未指定なら10分を超えても終端状態まで監視する', async () => {
    const fetchSpy = mockFetch({ status: 'running', progress: '作成中' })
    const onError = vi.fn()

    const { result } = renderHook(() =>
      useJobPoller({
        jobId: 'job-001',
        getStatusUrl: id => `/api/status/${id}`,
        intervalMs: 60_000,
        onError,
      })
    )
    await tick(11 * 60_000)

    expect(result.current.status).toBe('running')
    expect(fetchSpy.mock.calls.length).toBeGreaterThanOrEqual(10)
    expect(onError).not.toHaveBeenCalled()
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

describe('期限付き監視と古い応答の抑止', () => {
  function deferred<T>() {
    let resolve!: (value: T) => void
    const promise = new Promise<T>(yes => { resolve = yes })
    return { promise, resolve }
  }
  const response = (data: object) => ({ ok: true, json: async () => data }) as Response

  it('ハングした要求を10秒で中断し、ジョブIDを維持して再確認する', async () => {
    const pending = deferred<Response>()
    const fetchSpy = vi.spyOn(global, 'fetch')
      .mockReturnValueOnce(pending.promise)
      .mockResolvedValue(response({ status: 'completed', job_id: 'job-001' }))
    const onError = vi.fn()
    const onCompleted = vi.fn()
    const { result } = renderHook(() => useJobPoller({
      jobId: 'job-001', getStatusUrl: id => `/api/status/${id}`, intervalMs: 1000, onError, onCompleted,
    }))
    await tick(1000)
    const signal = fetchSpy.mock.calls[0][1]?.signal
    expect(signal?.aborted).toBe(false)
    await tick(10_000)
    expect(signal?.aborted).toBe(true)
    expect(result.current.status).toBe('running')
    expect(result.current.progress).toContain('接続を再確認中')
    expect(onError).not.toHaveBeenCalled()
    await tick(1000)
    expect(onCompleted).toHaveBeenCalledWith({ status: 'completed', job_id: 'job-001' })
    expect(fetchSpy.mock.calls.every(([url]) => url === '/api/status/job-001')).toBe(true)
    // The abandoned transport deliberately ignores AbortSignal.
    await act(async () => { pending.resolve(response({ status: 'error', error: 'obsolete' })) })
    expect(result.current.status).toBe('completed')
    expect(onError).not.toHaveBeenCalled()
  })

  it('JSON本文がハングしても同じ期限で解除する', async () => {
    const pending = deferred<object>()
    const fetchSpy = vi.spyOn(global, 'fetch')
      .mockResolvedValueOnce({ ok: true, json: () => pending.promise } as Response)
      .mockResolvedValue(response({ status: 'running', progress: '再接続済み' }))
    const { result } = renderHook(() => useJobPoller({
      jobId: 'job-001', getStatusUrl: id => `/api/status/${id}`, intervalMs: 1000,
    }))
    await tick(12_000)
    expect(fetchSpy).toHaveBeenCalledTimes(2)
    expect(fetchSpy.mock.calls[0][1]?.signal?.aborted).toBe(true)
    expect(result.current.progress).toBe('再接続済み')
    await act(async () => { pending.resolve({ status: 'completed' }) })
    expect(result.current.status).toBe('running')
  })

  it('ジョブ切り替え後の旧completed応答は新しい監視を停止しない', async () => {
    const old = deferred<Response>()
    const fetchSpy = vi.spyOn(global, 'fetch')
      .mockReturnValueOnce(old.promise)
      .mockResolvedValue(response({ status: 'running', progress: '新ジョブ' }))
    const onCompleted = vi.fn()
    const { result, rerender } = renderHook(({ jobId }) => useJobPoller({
      jobId, getStatusUrl: id => `/api/status/${id}`, intervalMs: 1000, onCompleted,
    }), { initialProps: { jobId: 'job-001' } })
    await tick(1000)
    rerender({ jobId: 'job-002' })
    expect(fetchSpy.mock.calls[0][1]?.signal?.aborted).toBe(true)
    await tick(1000)
    await act(async () => { old.resolve(response({ status: 'completed', job_id: 'job-001' })) })
    expect(onCompleted).not.toHaveBeenCalled()
    expect(result.current.progress).toBe('新ジョブ')
    await tick(1000)
    expect(fetchSpy).toHaveBeenCalledTimes(3)
    expect(fetchSpy.mock.calls[2][0]).toBe('/api/status/job-002')
  })

  it('reset後に届く旧応答はidle状態と停止済み監視を変更しない', async () => {
    const pending = deferred<Response>()
    const fetchSpy = vi.spyOn(global, 'fetch').mockReturnValue(pending.promise)
    const onCompleted = vi.fn()
    const { result } = renderHook(() => useJobPoller({
      jobId: 'job-001', getStatusUrl: id => `/api/status/${id}`, intervalMs: 1000, onCompleted,
    }))
    await tick(1000)
    act(() => result.current.reset())
    await act(async () => { pending.resolve(response({ status: 'completed' })) })
    await tick(3000)
    expect(result.current.status).toBe('idle')
    expect(onCompleted).not.toHaveBeenCalled()
    expect(fetchSpy).toHaveBeenCalledTimes(1)
  })

  it('アンマウント後はサーバージョブを止めずに要求だけ中断する', async () => {
    const pending = deferred<Response>()
    const fetchSpy = vi.spyOn(global, 'fetch').mockReturnValue(pending.promise)
    const onCompleted = vi.fn()
    const { unmount } = renderHook(() => useJobPoller({
      jobId: 'job-001', getStatusUrl: id => `/api/status/${id}`, intervalMs: 1000, onCompleted,
    }))
    await tick(1000)
    unmount()
    expect(fetchSpy.mock.calls[0][1]?.signal?.aborted).toBe(true)
    await act(async () => { pending.resolve(response({ status: 'completed' })) })
    await tick(3000)
    expect(onCompleted).not.toHaveBeenCalled()
    expect(fetchSpy).toHaveBeenCalledTimes(1)
  })

  it('一時HTTP失敗を通知し、応答復旧後は再確認メッセージを消す', async () => {
    vi.spyOn(global, 'fetch')
      .mockResolvedValueOnce({ ok: false, status: 503 } as Response)
      .mockResolvedValue(response({ status: 'running' }))
    const onError = vi.fn()
    const { result } = renderHook(() => useJobPoller({
      jobId: 'job-001', getStatusUrl: id => `/api/status/${id}`, intervalMs: 1000, onError,
    }))
    await tick(1000)
    expect(result.current.progress).toContain('接続を再確認中')
    expect(onError).not.toHaveBeenCalled()
    await tick(1000)
    expect(result.current.progress).toBe('')
    expect(result.current.status).toBe('running')
  })
})
