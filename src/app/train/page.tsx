'use client'

import { useState, useEffect, useCallback } from 'react'
import Link from 'next/link'
import { formatModelCreatedAt } from '@/lib/model-display'
import { Logo } from '@/components/Logo'
import { Toast } from '@/components/Toast'
import { ConfirmDialog } from '@/components/ConfirmDialog'
import { ModelEvaluationSummary } from '@/components/ModelEvaluationSummary'
import { authFetch } from '@/lib/auth-fetch'
import { useJobPoller } from '@/hooks/useJobPoller'

const TRAIN_UI_STATE_KEY = 'keiba-ai-pro:train-ui:v1'
const TRAIN_JOB_ID_PATTERN = /^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/i
const BASELINE_FEATURE_PIPELINE = 'local-db-shared-v1'
const OPTIMIZED_FEATURE_PIPELINE = 'local-db-shared-optimized-v1'
const CONTEXT_FEATURE_PIPELINE = 'local-db-context-v1'
const FEATURE_PROFILE_VERSION = 1
const SHARED_TARGET = 'speed_deviation' as const

type TrainingCapability = 'checking' | 'enabled' | 'disabled'
type ComputeDevice = 'auto' | 'cpu' | 'gpu'
type FeaturePipeline = typeof BASELINE_FEATURE_PIPELINE | typeof OPTIMIZED_FEATURE_PIPELINE | typeof CONTEXT_FEATURE_PIPELINE
type AccelerationCapability = { gpuAvailable: boolean; featureCacheSupported: boolean; contextProfileSupported: boolean }
type TrainingAcceleration = {
  device?: 'cpu' | 'gpu'
  cache_hit?: boolean
  preparation_seconds?: number
  fit_seconds?: number
  total_seconds?: number
  fallback_reason?: string | null
}

function elapsedLabel(seconds: unknown) {
  if (typeof seconds !== 'number' || !Number.isFinite(seconds) || seconds < 0) return null
  const rounded = Math.round(seconds)
  return rounded < 60 ? `${rounded}秒` : `${Math.floor(rounded / 60)}分${rounded % 60}秒`
}

function TrainingAccelerationSummary({ acceleration }: { acceleration?: TrainingAcceleration | null }) {
  if (!acceleration) return null
  const total = elapsedLabel(acceleration.total_seconds)
  const preparation = elapsedLabel(acceleration.preparation_seconds)
  const fit = elapsedLabel(acceleration.fit_seconds)
  return (
    <div className="mt-3 space-y-2 text-xs text-[#888]" data-testid="training-acceleration-result">
      <div className="flex flex-wrap gap-2">
        {(acceleration.device === 'gpu' || acceleration.device === 'cpu') && (
          <span className="rounded border border-[#2a2a2a] px-2 py-1">{acceleration.device.toUpperCase()}</span>
        )}
        {typeof acceleration.cache_hit === 'boolean' && (
          <span className="rounded border border-[#2a2a2a] px-2 py-1">{acceleration.cache_hit ? '特徴量再利用' : '特徴量生成'}</span>
        )}
        {total && <span className="rounded border border-[#2a2a2a] px-2 py-1">学習処理 {total}</span>}
      </div>
      {(total || preparation || fit || acceleration.fallback_reason) && (
        <details>
          <summary className="cursor-pointer text-[#666] hover:text-white">処理の内訳</summary>
          <div className="mt-2 flex flex-wrap gap-x-4 gap-y-1">
            {preparation && <span>準備 {preparation}</span>}
            {fit && <span>学習・校正・評価 {fit}</span>}
          </div>
          {(total || preparation || fit) && <p className="mt-2 text-[#666]">スナップショット作成・公開処理は含みません。</p>}
          {acceleration.fallback_reason && acceleration.device === 'cpu' && <p className="mt-2">CPUに切り替えて実行しました。</p>}
        </details>
      )}
    </div>
  )
}

export default function TrainPage() {
  const [loading, setLoading] = useState(false)
  const [target, setTarget] = useState<'win' | 'place3' | 'win_tie' | 'speed_deviation'>('speed_deviation')
  const modelType = 'lightgbm' as const
  const [testSize, setTestSize] = useState(0.2)
  const cvFolds = 5
  const [trainingDateFrom, setTrainingDateFrom] = useState('')
  const [trainingDateTo, setTrainingDateTo] = useState('')
  const [trainResult, setTrainResult] = useState<any>(null)
  const [models, setModels] = useState<any[]>([])
  const [deletingId, setDeletingId] = useState<string | null>(null)
  const [activatingId, setActivatingId] = useState<string | null>(null)
  const [jobId, setJobId] = useState<string | null>(null)
  const [toast, setToast] = useState({ visible: false, message: '', type: 'success' as 'success' | 'error' })
  const [confirmDelete, setConfirmDelete] = useState<string | null>(null)
  const [showAdvanced, setShowAdvanced] = useState(false)
  const [settingsHydrated, setSettingsHydrated] = useState(false)
  const [trainingCapability, setTrainingCapability] = useState<TrainingCapability>('checking')
  const [accelerationCapability, setAccelerationCapability] = useState<AccelerationCapability | null>(null)
  const [computeDevice, setComputeDevice] = useState<ComputeDevice>('auto')
  const [reuseFeatures, setReuseFeatures] = useState(true)
  const [featurePipeline, setFeaturePipeline] = useState<FeaturePipeline>(OPTIMIZED_FEATURE_PIPELINE)
  const [jobAcceleration, setJobAcceleration] = useState<TrainingAcceleration | null>(null)
  const effectiveComputeDevice = computeDevice === 'gpu' && accelerationCapability?.gpuAvailable !== true ? 'auto' : computeDevice
  const contextProfileUnavailable = featurePipeline === CONTEXT_FEATURE_PIPELINE
    && accelerationCapability?.contextProfileSupported !== true

  const loadTrainingCapability = useCallback(async () => {
    setTrainingCapability('checking')
    try {
      const response = await authFetch('/api/ml/train/capability', {
        cache: 'no-store',
        signal: AbortSignal.timeout(6_000),
      })
      const payload: unknown = await response.json().catch(() => null)
      const enabled = response.ok
        && typeof payload === 'object'
        && payload !== null
        && !Array.isArray(payload)
        && (payload as Record<string, unknown>).enabled === true
      setTrainingCapability(enabled ? 'enabled' : 'disabled')
      const acceleration = typeof payload === 'object' && payload !== null && !Array.isArray(payload)
        ? (payload as Record<string, unknown>).acceleration
        : null
      setAccelerationCapability(typeof acceleration === 'object' && acceleration !== null && !Array.isArray(acceleration)
        ? {
          gpuAvailable: (acceleration as Record<string, unknown>).gpu_available === true,
          featureCacheSupported: (acceleration as Record<string, unknown>).feature_cache_supported === true,
          contextProfileSupported: Array.isArray((payload as Record<string, unknown>).feature_profiles)
            && ((payload as Record<string, unknown>).feature_profiles as unknown[]).some(profile =>
              typeof profile === 'object' && profile !== null
                && (profile as Record<string, unknown>).pipeline === CONTEXT_FEATURE_PIPELINE
                && (profile as Record<string, unknown>).feature_count === 80),
        }
        : null)
    } catch {
      setTrainingCapability('disabled')
      setAccelerationCapability(null)
    }
  }, [])

  const { progress: jobProgress, pct: jobPct } = useJobPoller({
    jobId,
    getStatusUrl: id => `/api/ml/train/status/${id}`,
    onCompleted: statusData => {
      setLoading(false)
      setJobId(null)
      const result = statusData.result || {}
      setTrainResult({
        model_id: result.model_id,
        target,
        auc: result.metrics?.auc,
        logloss: result.metrics?.logloss,
        evaluation: result.evaluation,
        feature_pipeline: result.feature_pipeline,
        probability_validation: result.probability_validation ?? result.evaluation?.probability_validation,
        training_time_seconds: result.training_time_seconds ?? result.training_time,
        training_parallel_workers: result.training_parallel_workers,
        native_threads: result.native_threads,
        acceleration: result.acceleration,
        n_rows: result.data_count,
        message: result.message,
      })
      const correlation = result.evaluation?.primary?.rank_correlation
      showToast(
        target === 'speed_deviation'
          ? `学習完了 — 順位相関: ${typeof correlation === 'number' ? correlation.toFixed(3) : '未計測'}`
          : '学習が完了しました',
      )
      loadModels()
    },
    onError: msg => { setLoading(false); setJobId(null); showToast(msg, 'error') },
    onProgress: (_message, statusData) => {
      if (statusData.acceleration && typeof statusData.acceleration === 'object') {
        setJobAcceleration(statusData.acceleration)
      }
    },
  })

  const showToast = (message: string, type: 'success' | 'error' = 'success') =>
    setToast({ visible: true, message, type })

  useEffect(() => {
    void loadTrainingCapability()
  }, [loadTrainingCapability])

  useEffect(() => {
    loadModels()
    try {
      const raw = localStorage.getItem(TRAIN_UI_STATE_KEY)
      const saved = raw ? JSON.parse(raw) : null
      if (saved && typeof saved === 'object') {
        if (typeof saved.testSize === 'number') setTestSize(saved.testSize)
        if (typeof saved.trainingDateFrom === 'string') setTrainingDateFrom(saved.trainingDateFrom)
        if (typeof saved.trainingDateTo === 'string') setTrainingDateTo(saved.trainingDateTo)
        if (typeof saved.showAdvanced === 'boolean') setShowAdvanced(saved.showAdvanced)
        if (['auto', 'cpu', 'gpu'].includes(saved.computeDevice)) setComputeDevice(saved.computeDevice)
        if (typeof saved.reuseFeatures === 'boolean') setReuseFeatures(saved.reuseFeatures)
        // Older settings stored the fixed baseline, not a user-selected profile.
        if (saved.featureProfileVersion === FEATURE_PROFILE_VERSION
          && [BASELINE_FEATURE_PIPELINE, OPTIMIZED_FEATURE_PIPELINE, CONTEXT_FEATURE_PIPELINE].includes(saved.featurePipeline)) {
          setFeaturePipeline(saved.featurePipeline)
        }
        if (typeof saved.activeJobId === 'string' && TRAIN_JOB_ID_PATTERN.test(saved.activeJobId)) {
          // Keep the label of an older accepted job, never its settings for a new run.
          if (['win', 'place3', 'win_tie', 'speed_deviation'].includes(saved.target)) setTarget(saved.target)
          setJobId(saved.activeJobId)
          setLoading(true)
        }
      }
    } catch {
      localStorage.removeItem(TRAIN_UI_STATE_KEY)
    } finally {
      setSettingsHydrated(true)
    }
  }, [])

  useEffect(() => {
    if (!settingsHydrated) return
    localStorage.setItem(TRAIN_UI_STATE_KEY, JSON.stringify({
      target,
      modelType,
      testSize,
      cvFolds,
      featurePipeline,
      featureProfileVersion: FEATURE_PROFILE_VERSION,
      computeDevice,
      reuseFeatures,
      trainingDateFrom,
      trainingDateTo,
      showAdvanced,
      activeJobId: jobId,
    }))
  }, [
    settingsHydrated,
    target,
    modelType,
    testSize,
    cvFolds,
    computeDevice,
    reuseFeatures,
    featurePipeline,
    trainingDateFrom,
    trainingDateTo,
    showAdvanced,
    jobId,
  ])

  const loadModels = async () => {
    try {
      const res = await authFetch(`/api/models`)
      if (res.ok) { const d = await res.json(); setModels(d.models || []) }
    } catch {}
  }

  const handleDeleteModel = async (modelId: string) => {
    setConfirmDelete(modelId)
  }

  const doDeleteModel = async (modelId: string) => {
    setConfirmDelete(null)
    setDeletingId(modelId)
    try {
      const res = await authFetch(`/api/models/${encodeURIComponent(modelId)}`, { method: 'DELETE' })
      const data = await res.json().catch(() => ({}))
      if (!res.ok) throw new Error(typeof data.detail === 'string' ? data.detail : '削除できません')
      await loadModels()
      showToast(`モデルを削除しました`)
    } catch (error) {
      showToast(error instanceof Error ? error.message : '削除できません', 'error')
    } finally {
      setDeletingId(null)
    }
  }

  const handleActivateModel = async (modelId: string) => {
    setActivatingId(modelId)
    try {
      const res = await authFetch(`/api/models/${modelId}/activate`, { method: 'PUT' })
      const data = await res.json().catch(() => ({}))
      if (!res.ok) throw new Error(data.detail || '変更できません')
      await loadModels()
      showToast('既定モデルを変更しました')
    } catch (error) {
      showToast(error instanceof Error ? error.message : '変更できません', 'error')
    } finally {
      setActivatingId(null)
    }
  }

  const TARGET_LABELS: Record<string, string> = {
    win: '単勝（1着予測）',
    place3: '複勝（3着以内）',
    speed_deviation: '速度偏差（回帰）',
    win_tie: 'タイム同着',
  }

  const MODEL_TYPE_LABELS: Record<string, string> = {
    lightgbm: 'LightGBM',
    lightgbm_rank: 'LightGBM Rank',
    lightgbm_no_odds: 'LightGBM (No Odds)',
    logistic_regression: 'Logistic Regression',
  }

  /** 学習期間を "YYYY/MM/DD 〜 YYYY/MM/DD" に整形 */
  const formatDateRange = (from?: string, to?: string): string => {
    const fmt = (s?: string) => {
      if (!s) return '?'
      if (s.length === 8) return `${s.slice(0,4)}/${s.slice(4,6)}/${s.slice(6,8)}`
      return s.replace(/-/g, '/')
    }
    if (!from && !to) return ''
    return `${fmt(from)} 〜 ${fmt(to)}`
  }

  const trackAcceptedJob = (acceptedJobId: string) => {
    try {
      localStorage.setItem(TRAIN_UI_STATE_KEY, JSON.stringify({
        target: SHARED_TARGET,
        modelType,
        testSize,
        cvFolds,
        featurePipeline,
        featureProfileVersion: FEATURE_PROFILE_VERSION,
        computeDevice: effectiveComputeDevice,
        reuseFeatures,
        trainingDateFrom,
        trainingDateTo,
        showAdvanced,
        activeJobId: acceptedJobId,
      }))
    } catch {
      showToast('ジョブIDを保存できません', 'error')
    }
    setJobId(acceptedJobId)
  }

  const handleTrain = async () => {
    if (loading || trainingCapability !== 'enabled' || contextProfileUnavailable) return
    setTarget(SHARED_TARGET)
    setLoading(true)
    setTrainResult(null)
    setJobId(null)
    setJobAcceleration(null)

    try {
      // 1. 非同期ジョブ起動（すぐに job_id が返る）
      const startRes = await authFetch(`/api/ml/train/start`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          target: SHARED_TARGET, model_type: modelType, test_size: testSize, cv_folds: cvFolds,
          feature_pipeline: featurePipeline,
          compute_device: effectiveComputeDevice,
          reuse_features: reuseFeatures,
          use_sqlite: true, use_optimizer: true, use_optuna: false,
          ultimate_mode: true,
          training_date_from: trainingDateFrom || null,
          training_date_to: trainingDateTo || null,
        })
      })

      if (!startRes.ok) {
        const errorData: unknown = await startRes.json().catch(() => null)
        const errorRecord = typeof errorData === 'object' && errorData !== null && !Array.isArray(errorData)
          ? errorData as Record<string, unknown>
          : null
        const detail = errorRecord?.detail
        const detailRecord = typeof detail === 'object' && detail !== null && !Array.isArray(detail)
          ? detail as Record<string, unknown>
          : null
        const activeJobId = typeof detailRecord?.job_id === 'string' ? detailRecord.job_id : ''
        if (
          startRes.status === 409
          && detailRecord?.code === 'train-job-active'
          && TRAIN_JOB_ID_PATTERN.test(activeJobId)
        ) {
          trackAcceptedJob(activeJobId)
          return
        }
        const message = typeof detail === 'string'
          ? detail
          : typeof detailRecord?.message === 'string'
            ? detailRecord.message
            : typeof errorRecord?.error === 'string'
              ? errorRecord.error
              : `HTTP ${startRes.status}`
        throw new Error(message)
      }

      const startData: unknown = await startRes.json()
      const acceptedJobId = typeof startData === 'object'
        && startData !== null
        && !Array.isArray(startData)
        && typeof (startData as Record<string, unknown>).job_id === 'string'
        ? (startData as Record<string, unknown>).job_id as string
        : ''
      if (!TRAIN_JOB_ID_PATTERN.test(acceptedJobId)) {
        throw new Error('ジョブIDを確認できません')
      }
      trackAcceptedJob(acceptedJobId)

    } catch (error: any) {
      setLoading(false)
      showToast(`学習エラー: ${error.message}`, 'error')
    }
  }

  const FLD = 'w-full px-4 py-3 bg-[#0a0a0a] border border-[#1e1e1e] rounded-lg text-white focus:outline-none focus:border-[#333] transition-colors'

  return (
    <div className="min-h-screen bg-[#0a0a0a] text-white">
      <header className="border-b border-[#1e1e1e] px-6 py-4 flex items-center justify-between">
        <Logo href="/home" />
        <div className="flex items-center gap-4">
          <Link href="/home" className="flex items-center gap-1 text-xs text-[#555] hover:text-white transition-colors">
            <svg className="w-3 h-3" fill="none" viewBox="0 0 24 24" stroke="currentColor">
              <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M15 19l-7-7 7-7" />
            </svg>
            ホーム
          </Link>
          <span className="text-sm text-[#888]">モデル作成</span>
          <Link
            href="/feature-lab"
            className="flex items-center gap-1.5 px-3 py-1.5 rounded border border-[#2a2a2a] bg-[#111] text-xs text-[#aaa] hover:text-white hover:border-[#444] transition-colors"
            title="特徴量の重要度・カバレッジを確認"
          >
            <svg className="w-3.5 h-3.5" fill="none" viewBox="0 0 24 24" stroke="currentColor">
              <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M9 19v-6a2 2 0 00-2-2H5a2 2 0 00-2 2v6a2 2 0 002 2h2a2 2 0 002-2zm0 0V9a2 2 0 012-2h2a2 2 0 012 2v10m-6 0a2 2 0 002 2h2a2 2 0 002-2m0 0V5a2 2 0 012-2h2a2 2 0 012 2v14a2 2 0 01-2 2h-2a2 2 0 01-2-2z" />
            </svg>
            特徴量ラボ
          </Link>
        </div>
      </header>

      <main className="max-w-3xl mx-auto px-6 py-10 space-y-6">
        <div className="bg-[#111] border border-[#1e1e1e] rounded-lg p-6 space-y-5">
          <h2 className="text-sm font-medium text-white">学習設定</h2>
          <p className="mt-2 text-xs leading-relaxed text-[#888]">
            最新の保存済みDBから、選択期間内の学習可能データを使用します。
            作成後は予測画面でモデルを選び「最新データで再予測」してください。使用中モデルは自動で切り替わりません。
          </p>
          <p className="text-xs text-[#888]">保存済みDB・共通特徴量</p>
          <div className="space-y-1 text-xs" data-testid="training-feature-profile">
            <p className="text-[#aaa]">{featurePipeline === CONTEXT_FEATURE_PIPELINE ? '条件・展開80項目（比較候補）' : featurePipeline === OPTIMIZED_FEATURE_PIPELINE ? '候補46項目' : '基準47項目'}</p>
            {featurePipeline === OPTIMIZED_FEATURE_PIPELINE && (
              <p className="text-[#777]">直近5走の複勝圏率を除外。安定した優位性は未確認です。</p>
            )}
            {featurePipeline === CONTEXT_FEATURE_PIPELINE && (
              <div className="space-y-1 text-[#888]" data-testid="training-context-profile-note">
                <p>既存46項目に、過去の条件適性・馬体重推移・枠・脚質／展開・血統の34項目を追加します。</p>
                <p>対象レースより前の情報だけを使用。欠損は推測で埋めず、購入判断は未承認の比較候補です。</p>
                <p className="text-[#666]">馬場状態は良／稍重／重／不良です。芝の傷みや当日の内外有利を直接観測した値ではありません。</p>
                <p className="text-[#666]">使用柵・内外回り・芝の損傷・実ラップは未取得です。展開は過去の通過順位から作る傾向値です。</p>
              </div>
            )}
            <p className="text-[#666]">使用中モデルは自動で切り替えません。</p>
          </div>
          <p className="text-xs text-[#666]" data-testid="training-acceleration-setting">
            {effectiveComputeDevice === 'auto' ? 'GPU / CPU 自動選択' : `${effectiveComputeDevice.toUpperCase()}学習`}
            {reuseFeatures && <span className="ml-3">特徴量再利用</span>}
          </p>
          <div className="grid grid-cols-2 gap-4">
            <div>
              <label className="text-xs text-[#666] block mb-2">予測ターゲット</label>
              <div aria-label="予測ターゲット" className={FLD}>速度スコア（回帰）</div>
            </div>
            <div>
              <label className="text-xs text-[#666] block mb-2">モデルタイプ</label>
              <div className={FLD}>LightGBM（推奨）</div>
            </div>
          </div>

          {/* 学習データ期間 */}
          <div>
            <label className="text-xs text-[#666] block mb-2">学習データ期間（省略すると全データ使用）</label>
            <div className="grid grid-cols-2 gap-4">
              <input type="month" value={trainingDateFrom} onChange={e => setTrainingDateFrom(e.target.value)} placeholder="開始年月" className={FLD} />
              <input type="month" value={trainingDateTo}   onChange={e => setTrainingDateTo(e.target.value)}   placeholder="終了年月" className={FLD} />
            </div>
          </div>

          {/* 詳細設定（折りたたみ） */}
          <div className="border border-[#1e1e1e] rounded-lg overflow-hidden">
            <button
              onClick={() => setShowAdvanced(v => !v)}
              className="w-full flex items-center justify-between px-4 py-3 bg-[#0d0d0d] hover:bg-[#161616] transition-colors"
            >
              <span className="text-xs text-[#555]">詳細設定（上級者向け）</span>
              <svg className={`w-3 h-3 text-[#444] transition-transform ${showAdvanced ? 'rotate-180' : ''}`} fill="none" viewBox="0 0 24 24" stroke="currentColor">
                <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M19 9l-7 7-7-7" />
              </svg>
            </button>
            {showAdvanced && (
              <div className="px-4 pb-4 pt-3 space-y-4 border-t border-[#1e1e1e]">
                <div>
                  <label htmlFor="shared-feature-pipeline" className="text-xs text-[#666] block mb-2">特徴量構成</label>
                  <select id="shared-feature-pipeline" value={featurePipeline} disabled={loading}
                    onChange={e => setFeaturePipeline(e.target.value as FeaturePipeline)} className={FLD}>
                    <option value={OPTIMIZED_FEATURE_PIPELINE}>候補46項目</option>
                    <option value={BASELINE_FEATURE_PIPELINE}>基準47項目</option>
                    <option value={CONTEXT_FEATURE_PIPELINE} disabled={accelerationCapability?.contextProfileSupported !== true}>条件・展開を追加（比較候補80項目）</option>
                  </select>
                </div>
                <div>
                  <label htmlFor="shared-compute-device" className="text-xs text-[#666] block mb-2">学習方式</label>
                  <select id="shared-compute-device" value={effectiveComputeDevice} disabled={loading}
                    onChange={e => setComputeDevice(e.target.value as ComputeDevice)} className={FLD}>
                    <option value="auto">自動（GPU / CPU）</option>
                    <option value="cpu">CPU</option>
                    <option value="gpu" disabled={accelerationCapability?.gpuAvailable !== true}>GPU</option>
                  </select>
                </div>
                <label className="flex items-center gap-2 text-xs text-[#888]">
                  <input type="checkbox" checked={reuseFeatures} disabled={loading}
                    onChange={e => setReuseFeatures(e.target.checked)} className="accent-white" />
                  特徴量を再利用
                </label>
                <div>
                  <div>
                    <label htmlFor="shared-test-size" className="text-xs text-[#666] block mb-2">最終評価の割合</label>
                    <input id="shared-test-size" type="number" min="0.1" max="0.5" step="0.05" value={testSize}
                      onChange={e => setTestSize(parseFloat(e.target.value))} className={FLD} />
                  </div>
                </div>
                <p className="text-xs text-[#777]">固定2候補を比較。順位相関・RMSEで確認します。</p>
                <p className="text-xs text-[#666]">学習・校正・最終評価は時系列で分離します。直近データが校正・評価に割り当てられる場合もあり、全件がモデルの学習に入るわけではありません。</p>
              </div>
            )}
          </div>

          <button
            onClick={handleTrain}
            disabled={loading || trainingCapability !== 'enabled' || contextProfileUnavailable}
            className="w-full py-3 bg-white text-black font-medium rounded-lg hover:bg-[#eee] disabled:opacity-40 disabled:cursor-not-allowed transition-colors"
          >
            {loading
              ? '作成中…'
              : trainingCapability === 'checking'
                ? '確認中…'
                : trainingCapability === 'enabled'
                  ? 'モデル作成'
                  : '利用不可'}
          </button>
          {!loading && contextProfileUnavailable && (
            <p className="text-xs text-[#888]">このAPIでは条件・展開候補を利用できません。特徴量構成を選び直すか、API反映後に再確認してください。</p>
          )}
          {!loading && trainingCapability === 'disabled' && (
            <div className="flex items-center justify-center gap-3 text-xs text-[#666]">
              <span>ローカル管理者のみ</span>
              <button
                type="button"
                onClick={() => void loadTrainingCapability()}
                className="text-[#aaa] transition-colors hover:text-white"
              >
                再確認
              </button>
            </div>
          )}

          {loading && jobId && (
            <div className="p-4 bg-[#0a0a0a] border border-[#1e1e1e] rounded-lg space-y-2">
              <div className="flex items-center justify-between">
                <div className="flex items-center gap-2">
                  <span className="inline-block w-2 h-2 rounded-full bg-yellow-400 animate-pulse" />
                  <span className="text-xs text-[#888]">学習中</span>
                  {(jobAcceleration?.device === 'gpu' || jobAcceleration?.device === 'cpu') && (
                    <span className="text-xs text-[#666]">{jobAcceleration.device.toUpperCase()}</span>
                  )}
                  {jobAcceleration?.cache_hit === true && <span className="text-xs text-[#666]">特徴量再利用</span>}
                </div>
                <span className="text-xs text-[#555] tabular-nums">{jobPct}%</span>
              </div>
              {/* Progress bar */}
              <div className="h-1 bg-[#1e1e1e] rounded-full overflow-hidden">
                <div
                  className="h-full bg-white rounded-full transition-all duration-700 ease-out"
                  style={{ width: `${jobPct}%` }}
                />
              </div>
              <div className="text-xs text-[#555]">{jobProgress}</div>
            </div>
          )}
        </div>

        {trainResult && (
          <div className="bg-[#111] border border-[#1e1e1e] rounded-lg p-6">
            <div className="mb-4 flex items-center justify-between gap-3">
              <div className="text-sm font-medium text-white">学習結果</div>
              <div className="text-xs text-[#555]">{trainResult.n_rows?.toLocaleString()}件</div>
            </div>
            <ModelEvaluationSummary
              evaluation={trainResult.evaluation}
              target={trainResult.target}
              legacyAuc={trainResult.auc}
              legacyLogloss={trainResult.logloss}
              featurePipeline={trainResult.feature_pipeline}
              probabilityValidation={trainResult.probability_validation}
              trainingTimeSeconds={trainResult.training_time_seconds}
              trainingParallelWorkers={trainResult.training_parallel_workers}
              nativeThreads={trainResult.native_threads}
            />
            <TrainingAccelerationSummary acceleration={trainResult.acceleration} />
            <div className="mt-3 truncate text-[10px] font-mono text-[#333]" title={trainResult.model_id}>
              {trainResult.model_id}
            </div>
          </div>
        )}

        <div>
          <div className="flex items-center justify-between mb-3">
            <div className="flex items-center gap-2">
              <h2 className="text-sm font-medium text-[#888]">保存済みモデル</h2>
              <span className="text-xs text-[#444]">({models.length}件)</span>
            </div>
            <Link
              href="/feature-lab"
              className="flex items-center gap-1 text-xs text-[#555] hover:text-[#aaa] transition-colors"
            >
              特徴量の重要度を確認 →
            </Link>
          </div>
          <div className="bg-[#111] border border-[#1e1e1e] rounded-lg overflow-hidden">
            {models.length === 0 ? (
              <div className="p-8 text-center text-[#555] text-sm">モデルがありません</div>
            ) : (
              <div className="divide-y divide-[#1a1a1a]">
                {models.map((m, i) => {
                  const targetLabel = TARGET_LABELS[m.target] ?? m.target ?? '不明'
                  const typeLabel = MODEL_TYPE_LABELS[m.model_type] ?? m.model_type ?? '不明'
                  const createdDate = formatModelCreatedAt(m)
                  const dateRange = formatDateRange(m.training_date_from, m.training_date_to)
                  const isActivating = activatingId === m.model_id
                  const isDeleting = deletingId === m.model_id
                  return (
                    <div
                      key={i}
                      data-testid={`model-card-${m.model_id}`}
                      className={`px-5 py-4 hover:bg-[#161616] transition-colors ${m.is_active ? 'border-l-2 border-[#4ade80]' : ''}`}
                    >
                      {/* 1行目: モデル名と状態 */}
                      <div className="flex items-start justify-between gap-4">
                        <div className="flex items-center gap-2 min-w-0">
                          <span className="text-sm font-medium text-white">{m.display_name || targetLabel}</span>
                          <span className="text-xs text-[#555]">{typeLabel}</span>
                          {m.is_active && (
                            <span className="shrink-0 text-xs px-1.5 py-0.5 rounded bg-[#0a2a0a] text-[#4ade80] border border-[#1a4a1a] font-medium">
                              既定
                            </span>
                          )}
                          {m.feature_pipeline === 'local-win-nll-reference-v1'
                            ? <span className="text-xs text-[#fde68a]">参考運用</span>
                            : m.is_local_candidate && <span className="text-xs text-[#7dd3fc]">研究候補</span>}
                        </div>
                      </div>
                      {/* 2行目: 作成日時 + 学習期間 */}
                      <div className="mt-1.5 flex flex-wrap gap-x-4 gap-y-0.5 text-xs text-[#555]">
                        <span>作成: {createdDate}</span>
                        {dateRange && <span>学習期間: {dateRange}</span>}
                        {m.feature_count > 0 && <span>特徴量: {m.feature_count}個</span>}
                        {m.n_rows > 0 && <span>{m.n_rows.toLocaleString()}件</span>}
                      </div>
                      <div className="mt-3">
                        <ModelEvaluationSummary
                          evaluation={m.evaluation}
                          target={m.target}
                          legacyAuc={m.auc}
                          featurePipeline={m.feature_pipeline}
                          probabilityValidation={m.probability_validation ?? m.evaluation?.probability_validation}
                          trainingTimeSeconds={m.training_time_seconds}
                          trainingParallelWorkers={m.training_parallel_workers}
                          nativeThreads={m.native_threads}
                        />
                        <TrainingAccelerationSummary acceleration={m.acceleration} />
                      </div>
                      {/* 3行目: アクション */}
                      <div className="mt-3 flex items-center gap-2">
                        {!m.is_active && !m.is_local_candidate && (
                          <button
                            onClick={() => handleActivateModel(m.model_id)}
                            disabled={isActivating}
                            title="モデル未指定の予測で使う"
                            className="text-xs px-3 py-1 rounded border border-[#333] text-[#aaa] hover:border-[#555] hover:text-white transition-colors disabled:opacity-40"
                          >
                            {isActivating ? '変更中...' : '既定にする'}
                          </button>
                        )}
                        <div className="flex-1" />
                        <button
                          onClick={() => handleDeleteModel(m.model_id)}
                          disabled={isDeleting || m.is_active || m.is_local_candidate}
                          title={m.is_active ? '使用中モデルは削除できません' : m.is_local_candidate ? '研究候補の原本は保護されています' : 'このモデルを削除'}
                          className="text-xs text-[#555] hover:text-red-400 transition-colors disabled:opacity-30 px-2 py-1"
                        >
                          {isDeleting ? '...' : '削除'}
                        </button>
                      </div>
                      {/* モデルID（折りたたみ用の小さいテキスト） */}
                      <div className="mt-1 text-[10px] text-[#333] font-mono truncate" title={m.model_id}>
                        {m.model_id}
                      </div>
                    </div>
                  )
                })}
              </div>
            )}
          </div>
        </div>

        <div className="p-5 bg-[#111] border border-[#1e1e1e] rounded-lg flex items-center justify-between gap-4">
          <div>
            <div className="text-xs text-[#666] mb-0.5">次のステップ — 03</div>
            <div className="text-sm font-medium">予測実行</div>
            <div className="text-xs text-[#555] mt-0.5">学習したモデルでレース結果を予測します</div>
          </div>
          <Link
            href="/predict-batch"
            className="shrink-0 flex items-center gap-1.5 bg-white text-black text-sm font-medium px-5 py-2.5 rounded hover:bg-[#eee] transition-colors"
          >
            予測実行へ
            <svg className="w-3.5 h-3.5" fill="none" viewBox="0 0 24 24" stroke="currentColor">
              <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M9 5l7 7-7 7" />
            </svg>
          </Link>
        </div>
      </main>

      <Toast
        message={toast.message}
        type={toast.type}
        isVisible={toast.visible}
        onClose={() => setToast(t => ({ ...t, visible: false }))}
      />
      <ConfirmDialog
        isOpen={confirmDelete !== null}
        title="モデルを削除"
        message={`モデル ${confirmDelete ?? ''} を削除しますか？\nこの操作は元に戻せません。`}
        confirmLabel="削除"
        danger
        onConfirm={() => confirmDelete && doDeleteModel(confirmDelete)}
        onCancel={() => setConfirmDelete(null)}
      />
    </div>
  )
}
