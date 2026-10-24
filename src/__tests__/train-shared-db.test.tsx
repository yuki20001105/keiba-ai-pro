import { act, cleanup, fireEvent, render, screen, waitFor, within } from '@testing-library/react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import type { JobPollerOptions } from '@/hooks/useJobPoller'

const { fetchMock, poller } = vi.hoisted(() => ({
  fetchMock: vi.fn(), poller: { options: null as JobPollerOptions | null },
}))
vi.mock('@/lib/auth-fetch', () => ({ authFetch: fetchMock }))
vi.mock('@/components/Logo', () => ({ Logo: () => null }))
vi.mock('@/components/Toast', () => ({ Toast: () => null }))
vi.mock('@/hooks/useJobPoller', () => ({ useJobPoller: (options: JobPollerOptions) => {
  poller.options = options
  return { progress: '準備中', pct: 0 }
} }))

import TrainPage from '@/app/train/page'

const JOB = '11111111-1111-4111-8111-111111111111'
const STATE_KEY = 'keiba-ai-pro:train-ui:v1'

beforeEach(() => {
  localStorage.clear()
  fetchMock.mockReset()
  poller.options = null
  fetchMock.mockImplementation(async (url: string) => {
    if (url === '/api/ml/train/capability') return Response.json({ enabled: true })
    if (url === '/api/models') return Response.json({ models: [] })
    if (url === '/api/ml/train/start') return Response.json({ job_id: JOB })
    throw new Error(`Unexpected request: ${url}`)
  })
})
afterEach(cleanup)

describe('normal model creation using the shared DB feature pipeline', () => {
  it('offers the context profile only when supported and requires explicit opt-in', async () => {
    fetchMock.mockImplementation(async (url: string) => {
      if (url === '/api/ml/train/capability') return Response.json({ enabled: true,
        feature_profiles: [{ pipeline: 'local-db-context-v1', feature_count: 80, reference_only: true }],
        acceleration: { gpu_available: true, feature_cache_supported: true } })
      if (url === '/api/models') return Response.json({ models: [] })
      if (url === '/api/ml/train/start') return Response.json({ job_id: JOB })
      throw new Error(`Unexpected request: ${url}`)
    })
    render(<TrainPage />)
    const create = await screen.findByRole('button', { name: 'モデル作成' })
    fireEvent.click(screen.getByRole('button', { name: '詳細設定（上級者向け）' }))
    const profile = screen.getByRole('combobox', { name: '特徴量構成' })
    expect(profile).toHaveValue('local-db-shared-optimized-v1')
    expect(fetchMock.mock.calls.some(([url]) => /train\/start|activate/.test(url))).toBe(false)
    await waitFor(() => expect(within(profile).getByRole('option', { name: '条件・展開を追加（比較候補80項目）' })).toBeEnabled())
    fireEvent.change(profile, { target: { value: 'local-db-context-v1' } })
    expect(screen.getByTestId('training-context-profile-note')).toHaveTextContent('対象レースより前')
    expect(screen.getByTestId('training-context-profile-note')).toHaveTextContent('直接観測した値ではありません')
    fireEvent.click(create)
    await waitFor(() => expect(poller.options?.jobId).toBe(JOB))
    const request = fetchMock.mock.calls.find(([url]) => url === '/api/ml/train/start')
    expect(JSON.parse(request![1].body)).toMatchObject({ feature_pipeline: 'local-db-context-v1',
      target: 'speed_deviation', use_optuna: false, reuse_features: true })
    expect(fetchMock.mock.calls.some(([url]) => /activate|analyze|scrape|performance/.test(url))).toBe(false)
  })

  it('does not start a restored context selection against an older API', async () => {
    localStorage.setItem(STATE_KEY, JSON.stringify({ featureProfileVersion: 1,
      featurePipeline: 'local-db-context-v1', showAdvanced: true }))
    render(<TrainPage />)
    const create = await screen.findByRole('button', { name: 'モデル作成' })
    await waitFor(() => expect(create).toBeDisabled())
    expect(screen.getByText(/このAPIでは条件・展開候補を利用できません/)).toBeInTheDocument()
    fireEvent.click(create)
    expect(fetchMock.mock.calls.some(([url]) => /train\/start/.test(url))).toBe(false)
    fireEvent.change(screen.getByRole('combobox', { name: '特徴量構成' }), { target: { value: 'local-db-shared-optimized-v1' } })
    await waitFor(() => expect(create).toBeEnabled())
  })

  it('shows an activated probability candidate as reference operation while preserving speed training', async () => {
    fetchMock.mockImplementation(async (url: string) => {
      if (url === '/api/ml/train/capability') return Response.json({ enabled: true })
      if (url === '/api/models') return Response.json({ models: [
        { model_id: 'win-nll-reference', target: 'win', model_type: 'lightgbm', is_active: true,
          display_name: '全履歴・勝率NLL（参考運用）', feature_pipeline: 'local-win-nll-reference-v1',
          feature_count: 50, probability_validation: { calibrated: true, validated: false, status: 'unverified', eligible_for_ev: false },
          evaluation: { primary: { rank_correlation: .55, rmse: .23 } } },
      ] })
      throw new Error(`Unexpected request: ${url}`)
    })
    render(<TrainPage />)
    const reference = within(await screen.findByTestId('model-card-win-nll-reference'))
    expect(reference.getByText('全履歴・勝率NLL（参考運用）')).toBeInTheDocument()
    expect(reference.getByText('参考運用')).toBeInTheDocument()
    expect(reference.getByText('既定')).toBeInTheDocument()
    expect(reference.getByText('(RMSE 勝敗0/1)')).toBeInTheDocument()
    expect(reference.getByTestId('win-nll-reference-state')).toHaveTextContent('購入判断未承認')
    expect(reference.getByRole('button', { name: '削除' })).toBeDisabled()
    expect(screen.getByLabelText('予測ターゲット')).toHaveTextContent('速度スコア（回帰）')
    expect(fetchMock.mock.calls.every(([url]) => url === '/api/models' || url === '/api/ml/train/capability')).toBe(true)
  })

  it('ignores old optimizer/target settings, sends the fixed safe request and persists the accepted job', async () => {
    localStorage.setItem(STATE_KEY, JSON.stringify({ target: 'win', useOptuna: true,
      optunaTrials: 1000, optunaTimeout: 3600, cvFolds: 10, showAdvanced: true,
      featurePipeline: 'local-db-shared-v1',
      trainingDateFrom: '2021-06', trainingDateTo: '2021-07' }))
    render(<TrainPage />)
    expect(screen.getByText('保存済みDB・共通特徴量')).toBeInTheDocument()
    expect(screen.getByLabelText('予測ターゲット')).toHaveTextContent('速度スコア（回帰）')
    expect(screen.getByRole('combobox', { name: '学習方式' })).toHaveValue('auto')
    expect(screen.getByRole('checkbox', { name: '特徴量を再利用' })).toBeChecked()
    expect(screen.queryByText('Optuna 最適化')).not.toBeInTheDocument()
    expect(screen.queryByText('CVフォールド数')).not.toBeInTheDocument()
    const create = await screen.findByRole('button', { name: 'モデル作成' })
    await waitFor(() => expect(create).toBeEnabled())
    fireEvent.click(create)
    await waitFor(() => expect(poller.options?.jobId).toBe(JOB))
    const request = fetchMock.mock.calls.find(([url]) => url === '/api/ml/train/start')
    expect(JSON.parse(request![1].body)).toEqual({
      target: 'speed_deviation', model_type: 'lightgbm', test_size: 0.2, cv_folds: 5,
      feature_pipeline: 'local-db-shared-optimized-v1', use_sqlite: true, use_optimizer: true,
      compute_device: 'auto', reuse_features: true,
      use_optuna: false, ultimate_mode: true, training_date_from: '2021-06', training_date_to: '2021-07',
    })
    expect(JSON.parse(localStorage.getItem(STATE_KEY)!).activeJobId).toBe(JOB)
    expect(fetchMock.mock.calls.some(([url]) => /activate|analyze|performance|scrape/.test(url))).toBe(false)
  })

  it('allows an available GPU and cache opt-out without changing safe training settings', async () => {
    fetchMock.mockImplementation(async (url: string) => {
      if (url === '/api/ml/train/capability') return Response.json({ enabled: true,
        acceleration: { gpu_available: true, feature_cache_supported: true, default_device: 'auto' } })
      if (url === '/api/models') return Response.json({ models: [] })
      if (url === '/api/ml/train/start') return Response.json({ job_id: JOB })
      throw new Error(`Unexpected request: ${url}`)
    })
    render(<TrainPage />)
    const create = await screen.findByRole('button', { name: 'モデル作成' })
    fireEvent.click(screen.getByRole('button', { name: '詳細設定（上級者向け）' }))
    const device = screen.getByRole('combobox', { name: '学習方式' })
    expect(within(device).getByRole('option', { name: 'GPU' })).toBeEnabled()
    fireEvent.change(device, { target: { value: 'gpu' } })
    fireEvent.click(screen.getByRole('checkbox', { name: '特徴量を再利用' }))
    fireEvent.click(create)
    await waitFor(() => expect(poller.options?.jobId).toBe(JOB))
    const request = fetchMock.mock.calls.find(([url]) => url === '/api/ml/train/start')
    expect(JSON.parse(request![1].body)).toMatchObject({
      compute_device: 'gpu', reuse_features: false, feature_pipeline: 'local-db-shared-optimized-v1',
      target: 'speed_deviation', use_optuna: false,
    })
    expect(JSON.parse(localStorage.getItem(STATE_KEY)!)).toMatchObject({ computeDevice: 'gpu', reuseFeatures: false })
    expect(device).toBeDisabled()
    expect(fetchMock.mock.calls.some(([url]) => /activate/.test(url))).toBe(false)
  })

  it('disables unavailable GPU and safely restores an old GPU setting as automatic', async () => {
    localStorage.setItem(STATE_KEY, JSON.stringify({ computeDevice: 'gpu', showAdvanced: true }))
    fetchMock.mockImplementation(async (url: string) => {
      if (url === '/api/ml/train/capability') return Response.json({ enabled: true,
        acceleration: { gpu_available: false, feature_cache_supported: true, default_device: 'auto' } })
      if (url === '/api/models') return Response.json({ models: [] })
      if (url === '/api/ml/train/start') return Response.json({ job_id: JOB })
      throw new Error(`Unexpected request: ${url}`)
    })
    render(<TrainPage />)
    const create = await screen.findByRole('button', { name: 'モデル作成' })
    const device = screen.getByRole('combobox', { name: '学習方式' })
    expect(device).toHaveValue('auto')
    expect(within(device).getByRole('option', { name: 'GPU' })).toBeDisabled()
    fireEvent.click(create)
    await waitFor(() => expect(poller.options?.jobId).toBe(JOB))
    const request = fetchMock.mock.calls.find(([url]) => url === '/api/ml/train/start')
    expect(JSON.parse(request![1].body)).toMatchObject({ compute_device: 'auto', reuse_features: true })
  })

  it('defaults to the 46-feature candidate without promising superiority or switching the active model', async () => {
    render(<TrainPage />)
    await screen.findByRole('button', { name: 'モデル作成' })
    const profile = within(screen.getByTestId('training-feature-profile'))
    expect(profile.getByText('候補46項目')).toBeInTheDocument()
    expect(profile.getByText('直近5走の複勝圏率を除外。安定した優位性は未確認です。')).toBeInTheDocument()
    expect(profile.getByText('使用中モデルは自動で切り替えません。')).toBeInTheDocument()
    expect(JSON.parse(localStorage.getItem(STATE_KEY)!)).toMatchObject({
      featurePipeline: 'local-db-shared-optimized-v1', featureProfileVersion: 1,
    })
    expect(fetchMock.mock.calls.some(([url]) => /activate|train\/start/.test(url))).toBe(false)
  })

  it('allows the preserved 47-feature baseline and posts the exact selected profile', async () => {
    render(<TrainPage />)
    const create = await screen.findByRole('button', { name: 'モデル作成' })
    fireEvent.click(screen.getByRole('button', { name: '詳細設定（上級者向け）' }))
    const profile = screen.getByRole('combobox', { name: '特徴量構成' })
    expect(profile).toHaveValue('local-db-shared-optimized-v1')
    fireEvent.change(profile, { target: { value: 'local-db-shared-v1' } })
    expect(within(screen.getByTestId('training-feature-profile')).getByText('基準47項目')).toBeInTheDocument()
    fireEvent.click(create)
    await waitFor(() => expect(poller.options?.jobId).toBe(JOB))
    const request = fetchMock.mock.calls.find(([url]) => url === '/api/ml/train/start')
    expect(JSON.parse(request![1].body)).toMatchObject({
      feature_pipeline: 'local-db-shared-v1', compute_device: 'auto', reuse_features: true,
      target: 'speed_deviation', use_optuna: false,
    })
    expect(JSON.parse(localStorage.getItem(STATE_KEY)!)).toMatchObject({
      featurePipeline: 'local-db-shared-v1', featureProfileVersion: 1, activeJobId: JOB,
    })
    expect(profile).toBeDisabled()
    expect(fetchMock.mock.calls.some(([url]) => /activate/.test(url))).toBe(false)
  })

  it.each([
    ['local-db-shared-v1', 1, 'local-db-shared-v1'],
    ['local-db-shared-optimized-v1', 1, 'local-db-shared-optimized-v1'],
    ['unknown-pipeline', 1, 'local-db-shared-optimized-v1'],
    ['local-db-shared-v1', 0, 'local-db-shared-optimized-v1'],
  ])('restores only a recognized versioned profile: %s / %s', async (savedProfile, version, expectedProfile) => {
    localStorage.setItem(STATE_KEY, JSON.stringify({ featurePipeline: savedProfile,
      featureProfileVersion: version, showAdvanced: true }))
    render(<TrainPage />)
    const create = await screen.findByRole('button', { name: 'モデル作成' })
    expect(screen.getByRole('combobox', { name: '特徴量構成' })).toHaveValue(expectedProfile)
    fireEvent.click(create)
    await waitFor(() => expect(poller.options?.jobId).toBe(JOB))
    const request = fetchMock.mock.calls.find(([url]) => url === '/api/ml/train/start')
    expect(JSON.parse(request![1].body).feature_pipeline).toBe(expectedProfile)
  })

  it('restores an accepted older job without starting another training job', async () => {
    localStorage.setItem(STATE_KEY, JSON.stringify({ target: 'win', activeJobId: JOB }))
    render(<TrainPage />)
    await waitFor(() => expect(poller.options?.jobId).toBe(JOB))
    expect(poller.options?.getStatusUrl(JOB)).toBe(`/api/ml/train/status/${JOB}`)
    expect(screen.getByRole('button', { name: '作成中…' })).toBeDisabled()
    expect(fetchMock.mock.calls.some(([url]) => url === '/api/ml/train/start')).toBe(false)
  })

  it('keeps the administrator capability guard', async () => {
    fetchMock.mockImplementation(async (url: string) => {
      if (url === '/api/ml/train/capability') return Response.json({ enabled: false })
      if (url === '/api/models') return Response.json({ models: [] })
      throw new Error(`Unexpected request: ${url}`)
    })
    render(<TrainPage />)
    expect(await screen.findByRole('button', { name: '利用不可' })).toBeDisabled()
    expect(screen.getByText('ローカル管理者のみ')).toBeInTheDocument()
    expect(fetchMock.mock.calls.some(([url]) => url === '/api/ml/train/start')).toBe(false)
  })

  it('shows shared completed metrics, calibration and compute metadata without switching the active model', async () => {
    render(<TrainPage />)
    const create = await screen.findByRole('button', { name: 'モデル作成' })
    fireEvent.click(create)
    await waitFor(() => expect(poller.options?.jobId).toBe(JOB))
    await act(async () => { poller.options?.onCompleted?.({ job_id: JOB, status: 'completed', result: {
      model_id: 'shared-test-model', data_count: 2000, feature_pipeline: 'local-db-shared-v1',
      training_time_seconds: 15, training_parallel_workers: 2, native_threads: 3,
      evaluation: { rmse_unit: 'm/s', primary: { rank_correlation: 0.456, rmse: 0.222 },
        probability_validation: { status: 'unverified', calibrated: true, validated: false,
          eligible_for_ev: false, metrics: { nll: 2.001, brier: 0.09 } } },
    } }) })
    expect(screen.getByText('学習結果')).toBeInTheDocument()
    expect(within(screen.getByTestId('model-primary-metrics')).getByText('0.456')).toBeInTheDocument()
    expect(screen.getByTestId('model-calibration-state')).toHaveTextContent('校正済み・未検証')
    expect(screen.getByTestId('model-training-runtime')).toHaveTextContent('15秒')
    expect(poller.options?.jobId).toBeNull()
    expect(JSON.parse(localStorage.getItem(STATE_KEY)!).activeJobId).toBeNull()
    expect(fetchMock.mock.calls.some(([url]) => /activate/.test(url))).toBe(false)
  })

  it('labels a completed optimized model as a candidate and keeps EV validation separate', async () => {
    render(<TrainPage />)
    fireEvent.click(await screen.findByRole('button', { name: 'モデル作成' }))
    await waitFor(() => expect(poller.options?.jobId).toBe(JOB))
    await act(async () => { poller.options?.onCompleted?.({ job_id: JOB, status: 'completed', result: {
      model_id: 'optimized-model', data_count: 453857, feature_pipeline: 'local-db-shared-optimized-v1',
      evaluation: { rmse_unit: 'm/s', primary: { rank_correlation: 0.42471, rmse: 0.24558 },
        probability_validation: { status: 'unverified', calibrated: true, validated: false,
          eligible_for_ev: false } },
    } }) })
    expect(screen.getByTestId('model-feature-profile')).toHaveTextContent('候補46項目')
    const primary = within(screen.getByTestId('model-primary-metrics'))
    expect(primary.getByText('0.425')).toBeInTheDocument()
    expect(primary.getByText('0.246')).toBeInTheDocument()
    expect(primary.queryByText('回収率')).not.toBeInTheDocument()
    expect(screen.getByTestId('model-calibration-state')).toHaveTextContent('校正済み・未検証')
    expect(within(screen.getByTestId('model-probability-validation')).getByText('未達')).toBeInTheDocument()
    expect(fetchMock.mock.calls.some(([url]) => /activate/.test(url))).toBe(false)
  })

  it('shows actual GPU/cache timing and progress while retaining rank correlation and RMSE', async () => {
    render(<TrainPage />)
    fireEvent.click(await screen.findByRole('button', { name: 'モデル作成' }))
    await waitFor(() => expect(poller.options?.jobId).toBe(JOB))
    await act(async () => { poller.options?.onProgress?.('GPUで学習中', {
      acceleration: { device: 'gpu', cache_hit: true },
    }) })
    expect(screen.getByText('GPU')).toBeInTheDocument()
    await act(async () => { poller.options?.onCompleted?.({ job_id: JOB, status: 'completed', result: {
      model_id: 'accelerated-model', data_count: 453857, feature_pipeline: 'local-db-shared-v1',
      acceleration: { device: 'gpu', cache_hit: true, preparation_seconds: 4.2, fit_seconds: 21.8, total_seconds: 28.4 },
      evaluation: { rmse_unit: 'm/s', primary: { rank_correlation: 0.456, rmse: 0.222 } },
    } }) })
    const metadata = within(screen.getByTestId('training-acceleration-result'))
    expect(metadata.getByText('GPU')).toBeInTheDocument()
    expect(metadata.getByText('特徴量再利用')).toBeInTheDocument()
    expect(metadata.getByText('学習処理 28秒')).toBeInTheDocument()
    expect(metadata.getByText('準備 4秒')).toBeInTheDocument()
    expect(metadata.getByText('スナップショット作成・公開処理は含みません。')).toBeInTheDocument()
    const primary = within(screen.getByTestId('model-primary-metrics'))
    expect(primary.getByText('0.456')).toBeInTheDocument()
    expect(primary.getByText('0.222')).toBeInTheDocument()
    expect(primary.queryByText('回収率')).not.toBeInTheDocument()
    expect(fetchMock.mock.calls.some(([url]) => /activate/.test(url))).toBe(false)
  })

  it('shows CPU fallback and does not render malformed timing as a measured duration', async () => {
    render(<TrainPage />)
    fireEvent.click(await screen.findByRole('button', { name: 'モデル作成' }))
    await waitFor(() => expect(poller.options?.jobId).toBe(JOB))
    await act(async () => { poller.options?.onCompleted?.({ job_id: JOB, status: 'completed', result: {
      model_id: 'cpu-fallback-model', feature_pipeline: 'local-db-shared-v1',
      acceleration: { device: 'cpu', cache_hit: false, total_seconds: -10, fallback_reason: 'gpu-unavailable' },
    } }) })
    const metadata = within(screen.getByTestId('training-acceleration-result'))
    expect(metadata.getByText('CPU')).toBeInTheDocument()
    expect(metadata.getByText('特徴量生成')).toBeInTheDocument()
    expect(metadata.getByText('CPUに切り替えて実行しました。')).toBeInTheDocument()
    expect(metadata.queryByText(/学習処理/)).not.toBeInTheDocument()
    expect(metadata.queryByText('スナップショット作成・公開処理は含みません。')).not.toBeInTheDocument()
  })

  it('preserves five-metric legacy and protected fixed-candidate cards alongside a shared card', async () => {
    fetchMock.mockImplementation(async (url: string) => {
      if (url === '/api/ml/train/capability') return Response.json({ enabled: true })
      if (url === '/api/models') return Response.json({ models: [
        { model_id: 'legacy', target: 'speed_deviation', model_type: 'lightgbm', is_active: true, auc: 0.7 },
        { model_id: 'fixed', target: 'speed_deviation', model_type: 'lightgbm', is_local_candidate: true, auc: 0.4 },
        { model_id: 'shared', target: 'speed_deviation', model_type: 'lightgbm', feature_pipeline: 'local-db-shared-v1',
          probability_validation: { calibrated: false, validated: false, status: 'unverified' },
          training_time_seconds: 20 },
      ] })
      throw new Error(`Unexpected request: ${url}`)
    })
    render(<TrainPage />)
    const legacy = within(await screen.findByTestId('model-card-legacy'))
    const fixed = within(screen.getByTestId('model-card-fixed'))
    const shared = within(screen.getByTestId('model-card-shared'))
    expect(legacy.getByText('回収率')).toBeInTheDocument()
    expect(fixed.getByText('回収率')).toBeInTheDocument()
    expect(fixed.queryByRole('button', { name: '既定にする' })).not.toBeInTheDocument()
    expect(fixed.getByRole('button', { name: '削除' })).toBeDisabled()
    expect(legacy.getByRole('button', { name: '削除' })).toBeDisabled()
    expect(shared.queryByText('回収率')).not.toBeInTheDocument()
    expect(shared.getByTestId('model-calibration-state')).toHaveTextContent('未校正')
    expect(shared.getByTestId('model-training-runtime')).toHaveTextContent('20秒')
    expect(fetchMock.mock.calls.every(([url]) => url === '/api/models' || url === '/api/ml/train/capability')).toBe(true)
  })
})
