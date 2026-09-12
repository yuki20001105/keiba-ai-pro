'use client'

import { useState, useEffect, useCallback } from 'react'
import Link from 'next/link'
import { formatModelCreatedAt } from '@/lib/model-display'
import { Logo } from '@/components/Logo'
import { Toast } from '@/components/Toast'
import { ConfirmDialog } from '@/components/ConfirmDialog'
import { authFetch } from '@/lib/auth-fetch'
import { useJobPoller } from '@/hooks/useJobPoller'

const TRAIN_UI_STATE_KEY = 'keiba-ai-pro:train-ui:v1'
const TRAIN_POLL_TIMEOUT_MS = 24 * 60 * 60 * 1000
const TRAIN_JOB_ID_PATTERN = /^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/i

type TrainingCapability = 'checking' | 'enabled' | 'disabled'

export default function TrainPage() {
  const [loading, setLoading] = useState(false)
  const [target, setTarget] = useState<'win' | 'place3' | 'win_tie' | 'speed_deviation'>('win')
  const modelType = 'lightgbm' as const
  const [testSize, setTestSize] = useState(0.2)
  const [cvFolds, setCvFolds] = useState(5)
  const [useOptuna, setUseOptuna] = useState(false)
  const [optunaTrials, setOptunaTrials] = useState(50)
  const [optunaTimeout, setOptunaTimeout] = useState(300)
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
    } catch {
      setTrainingCapability('disabled')
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
        auc: result.metrics?.auc,
        logloss: result.metrics?.logloss,
        n_rows: result.data_count,
        message: result.message,
      })
      showToast(`学習完了 — AUC: ${result.metrics?.auc?.toFixed(4) ?? '?'}`)
      loadModels()
    },
    onError: msg => { setLoading(false); setJobId(null); showToast(msg, 'error') },
    maxMs: TRAIN_POLL_TIMEOUT_MS,
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
        if (['win', 'place3', 'win_tie', 'speed_deviation'].includes(saved.target)) setTarget(saved.target)
        if (typeof saved.testSize === 'number') setTestSize(saved.testSize)
        if (typeof saved.cvFolds === 'number') setCvFolds(saved.cvFolds)
        if (typeof saved.useOptuna === 'boolean') setUseOptuna(saved.useOptuna)
        if (typeof saved.optunaTrials === 'number') setOptunaTrials(saved.optunaTrials)
        if (typeof saved.optunaTimeout === 'number') setOptunaTimeout(saved.optunaTimeout)
        if (typeof saved.trainingDateFrom === 'string') setTrainingDateFrom(saved.trainingDateFrom)
        if (typeof saved.trainingDateTo === 'string') setTrainingDateTo(saved.trainingDateTo)
        if (typeof saved.showAdvanced === 'boolean') setShowAdvanced(saved.showAdvanced)
        if (typeof saved.activeJobId === 'string' && TRAIN_JOB_ID_PATTERN.test(saved.activeJobId)) {
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
      useOptuna,
      optunaTrials,
      optunaTimeout,
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
    useOptuna,
    optunaTrials,
    optunaTimeout,
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
      const res = await authFetch(`/api/models/${modelId}`, { method: 'DELETE' })
      if (!res.ok) throw new Error('削除失敗')
      loadModels()
      showToast(`モデルを削除しました`)
    } catch {
      showToast('削除に失敗しました', 'error')
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
        target,
        modelType,
        testSize,
        cvFolds,
        useOptuna,
        optunaTrials,
        optunaTimeout,
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
    if (loading || trainingCapability !== 'enabled') return
    setLoading(true)
    setTrainResult(null)
    setJobId(null)

    try {
      // 1. 非同期ジョブ起動（すぐに job_id が返る）
      const startRes = await authFetch(`/api/ml/train/start`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          target, model_type: modelType, test_size: testSize, cv_folds: cvFolds,
          use_sqlite: true, use_optimizer: modelType === 'lightgbm',
          use_optuna: useOptuna, optuna_trials: optunaTrials, optuna_timeout: optunaTimeout,
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
          <div className="grid grid-cols-2 gap-4">
            <div>
              <label className="text-xs text-[#666] block mb-2">予測ターゲット</label>
              <select value={target} onChange={e => setTarget(e.target.value as any)} className={FLD}>
                <option value="win">単勝（1着予測）</option>
                <option value="place3">複勝（3着以内）</option>
                <option value="win_tie">タイム同着（1着+同タイム馬）</option>
                <option value="speed_deviation">速度偏差（回帰）</option>
              </select>
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
                <div className="grid grid-cols-2 gap-4">
                  <div>
                    <label className="text-xs text-[#666] block mb-2">テストデータ割合</label>
                    <input type="number" min="0.1" max="0.5" step="0.05" value={testSize}
                      onChange={e => setTestSize(parseFloat(e.target.value))} className={FLD} />
                  </div>
                  <div>
                    <label className="text-xs text-[#666] block mb-2">CVフォールド数</label>
                    <input type="number" min="2" max="10" value={cvFolds}
                      onChange={e => setCvFolds(parseInt(e.target.value))} className={FLD} />
                  </div>
                </div>
                {modelType === 'lightgbm' && (
                  <div className="space-y-3">
                    <div className="flex items-center justify-between">
                      <div>
                        <div className="text-xs text-[#888] font-medium">Optuna 最適化</div>
                        <div className="text-xs text-[#555] mt-0.5">ベイズ最適化でパラメータ自動探索（時間がかかります）</div>
                      </div>
                      <button
                        onClick={() => setUseOptuna(v => !v)}
                        className={`relative inline-flex h-5 w-10 items-center rounded-full transition-colors ${useOptuna ? 'bg-white' : 'bg-[#333]'}`}
                      >
                        <span className={`inline-block h-3.5 w-3.5 transform rounded-full transition-transform ${useOptuna ? 'translate-x-5 bg-black' : 'translate-x-0.5 bg-[#888]'}`} />
                      </button>
                    </div>
                    {useOptuna && (
                      <div className="grid grid-cols-2 gap-4 pt-2 border-t border-[#1e1e1e]">
                        <div>
                          <label className="text-xs text-[#666] block mb-2">試行回数: {optunaTrials}</label>
                          <input type="range" min="3" max="100" value={optunaTrials}
                            onChange={e => setOptunaTrials(parseInt(e.target.value))} className="w-full accent-white" />
                        </div>
                        <div>
                          <label className="text-xs text-[#666] block mb-2">タイムアウト (秒)</label>
                          <input type="number" min="60" max="3600" step="60" value={optunaTimeout}
                            onChange={e => setOptunaTimeout(parseInt(e.target.value))} className={FLD} />
                        </div>
                        <div className="col-span-2 text-xs text-[#444]">
                          推定時間: {Math.round(optunaTrials * cvFolds * 0.3 / 60)}〜{Math.round(optunaTrials * cvFolds * 0.5 / 60)} 分
                        </div>
                      </div>
                    )}
                  </div>
                )}
              </div>
            )}
          </div>

          <button
            onClick={handleTrain}
            disabled={loading || trainingCapability !== 'enabled'}
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
            <div className="flex items-center gap-3 mb-4">
              <div className="text-sm font-medium text-white">学習結果</div>
              {trainResult.auc != null && (
                <span className={`text-xs px-2 py-0.5 rounded font-medium ${
                  trainResult.auc >= 0.75 ? 'bg-[#0a2a0a] text-[#4ade80] border border-[#1a4a1a]' :
                  trainResult.auc >= 0.70 ? 'bg-[#0a1a2a] text-[#60a5fa] border border-[#1a3a5a]' :
                  'bg-[#1a1a0a] text-[#facc15] border border-[#3a3a1a]'
                }`}>
                  {trainResult.auc >= 0.75 ? '優秀' : trainResult.auc >= 0.70 ? '良好' : '要改善'}
                </span>
              )}
            </div>
            <div className="grid grid-cols-2 md:grid-cols-4 gap-3">
              {[
                { label: 'AUC', value: trainResult.auc?.toFixed(4) },
                { label: 'Log Loss', value: trainResult.logloss?.toFixed(4) },
                { label: '学習データ数', value: trainResult.n_rows?.toLocaleString() },
                { label: 'モデルID', value: trainResult.model_id, small: true },
              ].map(s => (
                <div key={s.label} className="bg-[#0a0a0a] border border-[#1e1e1e] rounded-lg p-4">
                  <div className="text-xs text-[#555] mb-1">{s.label}</div>
                  <div className={`font-bold ${s.small ? 'text-xs text-[#4ade80] break-all' : 'text-xl'}`}>{s.value}</div>
                </div>
              ))}
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
                  const aucVal = m.auc ? m.auc.toFixed(4) : '—'
                  const cvVal = m.cv_auc_mean && m.cv_auc_mean > 0 ? m.cv_auc_mean.toFixed(4) : '—'
                  const isActivating = activatingId === m.model_id
                  const isDeleting = deletingId === m.model_id
                  return (
                    <div
                      key={i}
                      className={`px-5 py-4 hover:bg-[#161616] transition-colors ${m.is_active ? 'border-l-2 border-[#4ade80]' : ''}`}
                    >
                      {/* 1行目: ターゲット名 + アクティブバッジ + AUC */}
                      <div className="flex items-start justify-between gap-4">
                        <div className="flex items-center gap-2 min-w-0">
                          <span className="text-sm font-medium text-white">{targetLabel}</span>
                          <span className="text-xs text-[#555]">{typeLabel}</span>
                          {m.is_active && (
                            <span className="shrink-0 text-xs px-1.5 py-0.5 rounded bg-[#0a2a0a] text-[#4ade80] border border-[#1a4a1a] font-medium">
                              既定
                            </span>
                          )}
                        </div>
                        <div className="text-right shrink-0">
                          <div className={`text-sm font-medium tabular-nums ${
                            !m.auc ? 'text-[#555]' :
                            m.auc >= 0.80 ? 'text-[#4ade80]' :
                            m.auc >= 0.70 ? 'text-[#60a5fa]' : 'text-[#facc15]'
                          }`}>AUC {aucVal}</div>
                          {cvVal !== '—' && (
                            <div className="text-xs text-[#555] tabular-nums">CV {cvVal}</div>
                          )}
                        </div>
                      </div>
                      {/* 2行目: 作成日時 + 学習期間 */}
                      <div className="mt-1.5 flex flex-wrap gap-x-4 gap-y-0.5 text-xs text-[#555]">
                        <span>作成: {createdDate}</span>
                        {dateRange && <span>学習期間: {dateRange}</span>}
                        {m.feature_count > 0 && <span>特徴量: {m.feature_count}個</span>}
                        {m.n_rows > 0 && <span>{m.n_rows.toLocaleString()}件</span>}
                      </div>
                      {/* 3行目: アクション */}
                      <div className="mt-3 flex items-center gap-2">
                        {!m.is_active && (
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
                          disabled
                          title="モデル削除には別の永続的な廃止承認が必要です"
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
