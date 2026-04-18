'use client'

import { useState, useEffect } from 'react'
import Link from 'next/link'
import { Logo } from '@/components/Logo'
import { Toast } from '@/components/Toast'
import { ConfirmDialog } from '@/components/ConfirmDialog'
import { supabase } from '@/lib/supabase'
import { useJobPoller } from '@/hooks/useJobPoller'

export default function TrainPage() {
  const [loading, setLoading] = useState(false)
  const [target, setTarget] = useState<'win' | 'place3' | 'win_tie' | 'speed_deviation'>('win')
  const [modelType, setModelType] = useState<'logistic_regression' | 'lightgbm'>('lightgbm')
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
  const [jobId, setJobId] = useState<string | null>(null)
  const [toast, setToast] = useState({ visible: false, message: '', type: 'success' as 'success' | 'error' })
  const [confirmDelete, setConfirmDelete] = useState<string | null>(null)
  const [showAdvanced, setShowAdvanced] = useState(false)

  const { status: jobStatus, progress: jobProgress, pct: jobPct } = useJobPoller({
    jobId,
    getStatusUrl: id => `/api/ml/train/status/${id}`,
    onCompleted: statusData => {
      setLoading(false)
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
    onError: msg => { setLoading(false); showToast(msg, 'error') },
  })

  const showToast = (message: string, type: 'success' | 'error' = 'success') =>
    setToast({ visible: true, message, type })

  useEffect(() => { loadModels() }, [])

  const loadModels = async () => {
    try {
      const res = await fetch(`/api/models?ultimate=true`)
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
      const res = await fetch(`/api/models/${modelId}`, { method: 'DELETE' })
      if (!res.ok) throw new Error('削除失敗')
      loadModels()
      showToast(`モデル ${modelId.slice(0, 8)}... を削除しました`)
    } catch {
      showToast('削除に失敗しました', 'error')
    } finally {
      setDeletingId(null)
    }
  }

  const handleTrain = async () => {
    setLoading(true)
    setTrainResult(null)
    setJobId(null)

    let authHeaders: Record<string, string> = {}
    try {
      if (supabase) {
        const { data: { session } } = await supabase.auth.getSession()
        if (session?.access_token) authHeaders = { Authorization: `Bearer ${session.access_token}` }
      }
    } catch {}

    try {
      // 1. 非同期ジョブ起動（すぐに job_id が返る）
      const startRes = await fetch(`/api/ml/train/start`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json', ...authHeaders },
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
        const errorData = await startRes.json()
        throw new Error(errorData.detail || errorData.error || `HTTP ${startRes.status}`)
      }

      const startData = await startRes.json()
      setJobId(startData.job_id)

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
          <span className="text-sm text-[#888]">モデル学習</span>
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
              <select value={modelType} onChange={e => setModelType(e.target.value as any)} className={FLD}>
                <option value="lightgbm">LightGBM（推奨）</option>
                <option value="logistic_regression">Logistic Regression</option>
              </select>
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
            disabled={loading}
            className="w-full py-3 bg-white text-black font-medium rounded-lg hover:bg-[#eee] disabled:opacity-40 disabled:cursor-not-allowed transition-colors"
          >
            {loading ? (jobProgress || '学習中...') : '学習開始'}
          </button>

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
            <h2 className="text-sm font-medium text-[#888]">保存済みモデル</h2>
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
                {models.map((m, i) => (
                  <div key={i} className="flex items-center justify-between px-5 py-4 hover:bg-[#161616] transition-colors">
                    <div className="min-w-0 flex-1">
                      <div className="text-sm font-medium font-mono">{m.model_id}</div>
                      <div className="text-xs text-[#555] mt-0.5">{m.target} | {m.model_type}</div>
                      {(m.training_date_from || m.training_date_to) && (
                        <div className="text-xs text-[#666] mt-1">
                          期間: {m.training_date_from ?? '?'} 〜 {m.training_date_to ?? '?'}
                        </div>
                      )}
                      {m.n_rows > 0 && (
                        <div className="text-xs text-[#444] mt-0.5">{m.n_rows.toLocaleString()} 件</div>
                      )}
                    </div>
                    <div className="flex items-center gap-4 ml-4 shrink-0">
                      <div className="text-right">
                        <div className="text-sm text-[#4ade80] font-medium">AUC {m.auc?.toFixed(4)}</div>
                        <div className="text-xs text-[#555]">CV {m.cv_auc_mean?.toFixed(4)}</div>
                      </div>
                      <button
                        onClick={() => handleDeleteModel(m.model_id)}
                        disabled={deletingId === m.model_id}
                        className="text-xs text-[#555] hover:text-red-400 transition-colors disabled:opacity-40 px-2 py-1"
                      >
                        {deletingId === m.model_id ? '...' : '削除'}
                      </button>
                    </div>
                  </div>
                ))}
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
