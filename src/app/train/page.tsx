'use client'

import { useState, useEffect } from 'react'
import Link from 'next/link'
import { Logo } from '@/components/Logo'
import { supabase } from '@/lib/supabase'

const API_URL = process.env.NEXT_PUBLIC_API_URL || 'http://localhost:8000'

export default function TrainPage() {
  const [loading, setLoading] = useState(false)
  const [target, setTarget] = useState<'win' | 'place3'>('win')
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
  const [jobStatus, setJobStatus] = useState<string>('')
  const [jobProgress, setJobProgress] = useState<string>('')

  useEffect(() => { loadModels() }, [])

  const loadModels = async () => {
    try {
      const res = await fetch(`/api/models?ultimate=true`)
      if (res.ok) { const d = await res.json(); setModels(d.models || []) }
    } catch {}
  }

  const handleDeleteModel = async (modelId: string) => {
    if (!confirm(`モデル ${modelId} を削除しますか？`)) return
    setDeletingId(modelId)
    try {
      const res = await fetch(`/api/models/${modelId}`, { method: 'DELETE' })
      if (!res.ok) throw new Error('削除失敗')
      loadModels()
    } catch {
      alert('削除に失敗しました')
    } finally {
      setDeletingId(null)
    }
  }

  const handleTrain = async () => {
    setLoading(true)
    setTrainResult(null)
    setJobId(null)
    setJobStatus('queued')
    setJobProgress('ジョブ起動中...')

    // Supabase セッショントークン取得
    const { data: { session } } = await supabase.auth.getSession()
    const authHeaders: Record<string, string> = session?.access_token
      ? { Authorization: `Bearer ${session.access_token}` }
      : {}

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
      const newJobId = startData.job_id
      setJobId(newJobId)
      setJobStatus('running')
      setJobProgress('学習開始...')

      // 2. 3秒ごとにステータスをポーリング
      const pollInterval = setInterval(async () => {
        try {
          const statusRes = await fetch(`/api/ml/train/status/${newJobId}`)
          if (!statusRes.ok) return
          const statusData = await statusRes.json()

          setJobStatus(statusData.status)
          setJobProgress(statusData.progress || '')

          if (statusData.status === 'completed') {
            clearInterval(pollInterval)
            setLoading(false)
            const result = statusData.result || {}
            setTrainResult({
              model_id: result.model_id,
              auc: result.metrics?.auc,
              logloss: result.metrics?.logloss,
              n_rows: result.data_count,
              message: result.message,
            })
            loadModels()
          } else if (statusData.status === 'error') {
            clearInterval(pollInterval)
            setLoading(false)
            alert(`学習エラー: ${statusData.error}`)
          }
        } catch {/* ポーリング中の一時エラーは無視 */}
      }, 3000)

    } catch (error: any) {
      setLoading(false)
      alert(`学習エラー: ${error.message}`)
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
        </div>
      </header>

      <main className="max-w-3xl mx-auto px-6 py-10 space-y-6">
        <div className="bg-[#111] border border-[#1e1e1e] rounded-lg p-6 space-y-5">
          <h2 className="text-sm font-medium text-[#888]">学習設定</h2>
          <div className="grid grid-cols-2 gap-4">
            <div>
              <label className="text-xs text-[#666] block mb-2">予測ターゲット</label>
              <select value={target} onChange={e => setTarget(e.target.value as any)} className={FLD}>
                <option value="win">単勝（1着予測）</option>
                <option value="place3">複勝（3着以内）</option>
              </select>
            </div>
            <div>
              <label className="text-xs text-[#666] block mb-2">モデルタイプ</label>
              <select value={modelType} onChange={e => setModelType(e.target.value as any)} className={FLD}>
                <option value="logistic_regression">Logistic Regression</option>
                <option value="lightgbm">LightGBM（推奨）</option>
              </select>
            </div>
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

          {/* 学習データ期間 */}
          <div className="border border-[#1e1e1e] rounded-lg p-4 space-y-3">
            <div>
              <div className="text-sm font-medium">学習データ期間</div>
              <div className="text-xs text-[#666] mt-0.5">指定しない場合は全データを使用します</div>
            </div>
            <div className="grid grid-cols-2 gap-4">
              <div>
                <label className="text-xs text-[#666] block mb-2">開始年月</label>
                <input
                  type="month"
                  value={trainingDateFrom}
                  onChange={e => setTrainingDateFrom(e.target.value)}
                  className={FLD}
                />
              </div>
              <div>
                <label className="text-xs text-[#666] block mb-2">終了年月</label>
                <input
                  type="month"
                  value={trainingDateTo}
                  onChange={e => setTrainingDateTo(e.target.value)}
                  className={FLD}
                />
              </div>
            </div>
          </div>

          {modelType === 'lightgbm' && (
            <div className="border border-[#1e1e1e] rounded-lg p-4 space-y-4">
              <div className="flex items-center justify-between">
                <div>
                  <div className="text-sm font-medium">Optuna 最適化</div>
                  <div className="text-xs text-[#666] mt-0.5">ベイズ最適化でパラメータ自動探索</div>
                </div>
                <button
                  onClick={() => setUseOptuna(v => !v)}
                  className={`relative inline-flex h-6 w-11 items-center rounded-full transition-colors ${useOptuna ? 'bg-white' : 'bg-[#333]'}`}
                >
                  <span className={`inline-block h-4 w-4 transform rounded-full transition-transform ${useOptuna ? 'translate-x-6 bg-black' : 'translate-x-1 bg-[#888]'}`} />
                </button>
              </div>
              {useOptuna && (
                <div className="grid grid-cols-2 gap-4 pt-2 border-t border-[#1e1e1e]">
                  <div>
                    <label className="text-xs text-[#666] block mb-2">試行回数: {optunaTrials}</label>
                    <input type="range" min="3" max="100" value={optunaTrials}
                      onChange={e => setOptunaTrials(parseInt(e.target.value))}
                      className="w-full accent-white" />
                  </div>
                  <div>
                    <label className="text-xs text-[#666] block mb-2">タイムアウト (秒)</label>
                    <input type="number" min="60" max="3600" step="60" value={optunaTimeout}
                      onChange={e => setOptunaTimeout(parseInt(e.target.value))} className={FLD} />
                  </div>
                  <div className="col-span-2 text-xs text-[#555]">
                    推定: {Math.round(optunaTrials * cvFolds * 0.3 / 60)}～{Math.round(optunaTrials * cvFolds * 0.5 / 60)} 分
                  </div>
                </div>
              )}
            </div>
          )}

          <button
            onClick={handleTrain}
            disabled={loading}
            className="w-full py-3 bg-white text-black font-medium rounded-lg hover:bg-[#eee] disabled:opacity-40 disabled:cursor-not-allowed transition-colors"
          >
            {loading ? (jobProgress || '学習中...') : '学習開始'}
          </button>

          {loading && jobId && (
            <div className="mt-3 p-4 bg-[#0a0a0a] border border-[#1e1e1e] rounded-lg space-y-2">
              <div className="flex items-center gap-2">
                <span className="inline-block w-2 h-2 rounded-full bg-yellow-400 animate-pulse" />
                <span className="text-xs text-[#888]">ジョブID: <span className="font-mono text-[#555]">{jobId.slice(0, 8)}...</span></span>
              </div>
              <div className="text-xs text-[#666]">{jobProgress}</div>
              <div className="text-xs text-[#444]">学習完了まで1〜3分かかります。このページを閉じないでください。</div>
            </div>
          )}
        </div>

        {trainResult && (
          <div className="bg-[#111] border border-[#1e1e1e] rounded-lg p-6">
            <div className="text-sm font-medium text-[#888] mb-4">学習結果</div>
            <div className="grid grid-cols-2 md:grid-cols-4 gap-3">
              {[
                { label: 'AUC', value: trainResult.auc?.toFixed(4) },
                { label: 'Log Loss', value: trainResult.logloss?.toFixed(4) },
                { label: '学習データ数', value: trainResult.n_rows },
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
          <h2 className="text-sm font-medium text-[#888] mb-3">保存済みモデル</h2>
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
    </div>
  )
}
