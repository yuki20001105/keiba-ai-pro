import { act, cleanup, fireEvent, render, screen } from '@testing-library/react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { RaceExplanationPanel } from '@/components/RaceExplanationPanel'
import { narrativeCacheKey } from '@/lib/prediction-explanation'
import type { Prediction, RacePredictResult } from '@/lib/race-analysis-types'

const { fetchMock } = vi.hoisted(() => ({ fetchMock: vi.fn() }))
vi.mock('@/lib/auth-fetch', () => ({ authFetch: fetchMock }))

function deferred<T>() {
  let resolve!: (value: T) => void
  const promise = new Promise<T>(yes => { resolve = yes })
  return { promise, resolve }
}
function horse(number: number): Prediction {
  return {
    horse_number: number, horse_name: `テスト${number}号`, predicted_rank: number,
    jockey_name: '騎手', trainer_name: '調教師', sex: '牡', age: 3, horse_weight: 480,
    odds: 4, popularity: number, win_probability: 0.2, p_raw: 0.4, p_norm: 0.2, expected_value: 0.8,
    explanation: { method: 'tree_shap', base_value: 0.1, features: [{
      feature: 'history', label: '過去成績', description: '過去成績', value: 1,
      contribution: 0.3, direction: 'positive', impact_pct: 100,
    }] },
  }
}
const result = (): RacePredictResult => ({
  success: true, model_id: 'model-1', explanation_method: 'tree_shap',
  race_info: { race_id: '202610100101', race_name: 'テストレース', venue: '東京', date: '20261010', distance: 1600, track_type: '芝', num_horses: 2 },
  predictions: [horse(1), horse(2)], recommendation: null, best_bet_type: null, pro_evaluation: null,
})
const reply = (explanation: string) => Response.json({ explanation, source: 'fallback' })

beforeEach(() => { fetchMock.mockReset(); localStorage.clear() })
afterEach(() => { cleanup(); vi.useRealTimers() })

describe('exact-prediction narrative ownership', () => {
  it('does not invoke prediction or narrative generation automatically', () => {
    render(<RaceExplanationPanel result={result()} />)
    expect(fetchMock).not.toHaveBeenCalled()
    expect(screen.getByText('速度スコアへの寄与')).toBeInTheDocument()
  })

  it('aborts a pending horse narrative and ignores its late response after switching horses', async () => {
    const old = deferred<Response>()
    fetchMock.mockReturnValueOnce(old.promise).mockResolvedValue(reply('2号の解説'))
    render(<RaceExplanationPanel result={result()} />)
    fireEvent.click(screen.getByRole('button', { name: '文章で解説' }))
    const signal = fetchMock.mock.calls[0][1].signal as AbortSignal
    fireEvent.click(screen.getByRole('button', { name: /テスト2号/ }))
    expect(signal.aborted).toBe(true)
    expect(screen.getByRole('button', { name: '文章で解説' })).toBeEnabled()
    fireEvent.click(screen.getByRole('button', { name: '文章で解説' }))
    await screen.findByText('2号の解説')
    await act(async () => { old.resolve(reply('1号の古い解説')) })
    expect(screen.queryByText('1号の古い解説')).not.toBeInTheDocument()
    expect(screen.getByText('2号の解説')).toBeInTheDocument()
    expect(localStorage.getItem(narrativeCacheKey('202610100101', 'model-1', 1))).toBeNull()
    expect(fetchMock.mock.calls.every(([url]) => url === '/api/prediction-explanation')).toBe(true)
  })

  it.each(['race', 'model', 'features'] as const)('ignores a late narrative after %s identity changes', async kind => {
    const old = deferred<Response>()
    fetchMock.mockReturnValue(old.promise)
    const initial = result()
    const { rerender } = render(<RaceExplanationPanel result={initial} />)
    fireEvent.click(screen.getByRole('button', { name: '文章で解説' }))
    const next = result()
    if (kind === 'race') next.race_info.race_id = '202610100102'
    if (kind === 'model') next.model_id = 'model-2'
    // The LLM's six-field projection remains the same, but SHAP evidence changed.
    if (kind === 'features') next.predictions[0].explanation!.features[0].value = 99
    rerender(<RaceExplanationPanel result={next} />)
    expect((fetchMock.mock.calls[0][1].signal as AbortSignal).aborted).toBe(true)
    await act(async () => { old.resolve(reply('古い予測の解説')) })
    expect(screen.queryByText('古い予測の解説')).not.toBeInTheDocument()
    expect(screen.getByRole('button', { name: '文章で解説' })).toBeEnabled()
    expect(fetchMock).toHaveBeenCalledTimes(1)
  })

  it('reuses only an exact evidence cache and ignores legacy or changed-evidence text', async () => {
    const key = narrativeCacheKey('202610100101', 'model-1', 1)
    localStorage.setItem(key, JSON.stringify({ explanation: '証拠不明の旧キャッシュ', source: 'fallback' }))
    fetchMock.mockResolvedValue(reply('最新の解説'))
    const { rerender } = render(<RaceExplanationPanel result={result()} />)
    expect(screen.queryByText('証拠不明の旧キャッシュ')).not.toBeInTheDocument()
    fireEvent.click(screen.getByRole('button', { name: '文章で解説' }))
    await screen.findByText('最新の解説')
    const cached = JSON.parse(localStorage.getItem(key)!)
    expect(cached.version).toBe(2)
    expect(cached.inputIdentity).toContain('history')
    const changed = result()
    changed.predictions[0].explanation!.features[0].contribution = 0.7
    rerender(<RaceExplanationPanel result={changed} />)
    expect(screen.queryByText('最新の解説')).not.toBeInTheDocument()
    rerender(<RaceExplanationPanel result={result()} />)
    expect(screen.getByText('最新の解説')).toBeInTheDocument()
    expect(fetchMock).toHaveBeenCalledTimes(1)
  })

  it('bounds an abort-ignoring transport to 35 seconds without automatic retry', async () => {
    vi.useFakeTimers()
    const pending = deferred<Response>()
    fetchMock.mockReturnValue(pending.promise)
    render(<RaceExplanationPanel result={result()} />)
    fireEvent.click(screen.getByRole('button', { name: '文章で解説' }))
    await act(async () => { await vi.advanceTimersByTimeAsync(35_000) })
    expect((fetchMock.mock.calls[0][1].signal as AbortSignal).aborted).toBe(true)
    expect(screen.getByText(/時間切れ/)).toBeInTheDocument()
    expect(screen.getByRole('button', { name: '文章で解説' })).toBeEnabled()
    await act(async () => { pending.resolve(reply('時間切れ後の解説')) })
    expect(screen.queryByText('時間切れ後の解説')).not.toBeInTheDocument()
    expect(fetchMock).toHaveBeenCalledTimes(1)
  })

  it('does not publish or cache late text after unmount', async () => {
    const pending = deferred<Response>()
    fetchMock.mockReturnValue(pending.promise)
    const { unmount } = render(<RaceExplanationPanel result={result()} />)
    fireEvent.click(screen.getByRole('button', { name: '文章で解説' }))
    unmount()
    expect((fetchMock.mock.calls[0][1].signal as AbortSignal).aborted).toBe(true)
    await act(async () => { pending.resolve(reply('閉じた画面の解説')) })
    expect(localStorage.length).toBe(0)
  })
})
