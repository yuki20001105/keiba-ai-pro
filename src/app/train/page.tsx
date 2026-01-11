'use client'

import { useState, useEffect } from 'react'
import { supabase } from '@/lib/supabase'
import { useRouter } from 'next/navigation'
import { useUltimateMode } from '@/contexts/UltimateModeContext'

export default function TrainPage() {
  const router = useRouter()
  const [loading, setLoading] = useState(false)
  const [authLoading, setAuthLoading] = useState(true)
  const [userId, setUserId] = useState<string | null>(null)
  const { ultimateMode, setUltimateMode } = useUltimateMode()

  // 学習設定
  const [target, setTarget] = useState<'win' | 'place3'>('win')
  const [modelType, setModelType] = useState<'logistic_regression' | 'lightgbm'>('lightgbm')
  const [testSize, setTestSize] = useState(0.2)

  // Optuna設定
  const [useOptuna, setUseOptuna] = useState(false)
  const [optunaTrials, setOptunaTrials] = useState(50)
  const [cvFolds, setCvFolds] = useState(5)
  const [optunaTimeout, setOptunaTimeout] = useState(300)

  // 学習結果
  const [trainResult, setTrainResult] = useState<any>(null)
  const [models, setModels] = useState<any[]>([])

  useEffect(() => {
    const getUser = async () => {
      setAuthLoading(true)
      if (!supabase) {
        console.error('Supabase client not initialized')
        setAuthLoading(false)
        return
      }
      const { data: { user } } = await supabase.auth.getUser()
      if (!user) {
        router.push('/auth/login')
        return
      }
      setUserId(user.id)
      await loadModels()
      setAuthLoading(false)
    }
    getUser()
  }, [router, ultimateMode])

  const loadModels = async () => {
    try {
      const response = await fetch(`http://localhost:8000/api/models?ultimate=${ultimateMode}`)
      if (response.ok) {
        const data = await response.json()
        setModels(data.models || [])
      }
    } catch (error) {
      console.error('モデル一覧取得エラー:', error)
    }
  }

  const handleTrain = async () => {
    setLoading(true)
    setTrainResult(null)

    try {
      const response = await fetch('http://localhost:8000/api/train', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          target,
          model_type: modelType,
          test_size: testSize,
          cv_folds: cvFolds,
          use_sqlite: true,
          use_optimizer: modelType === 'lightgbm',
          use_optuna: useOptuna && modelType === 'lightgbm',
          optuna_trials: optunaTrials,
          optuna_timeout: optunaTimeout,
          ultimate_mode: ultimateMode
        })
      })

      if (!response.ok) {
        const errorData = await response.json()
        throw new Error(errorData.detail || `HTTP ${response.status}`)
      }

      const data = await response.json()
      setTrainResult(data)
      alert('学習完了！')
      loadModels()
    } catch (error: any) {
      alert(`学習エラー: ${error.message}`)
    } finally {
      setLoading(false)
    }
  }

  return (
    <div className="min-h-screen bg-gradient-to-br from-slate-900 via-blue-900 to-slate-900 py-8 px-4">
      <div className="max-w-6xl mx-auto">
        {/* ヘッダー */}
        <div className="mb-8 text-center">
          <h1 className="text-4xl font-bold text-transparent bg-clip-text bg-gradient-to-r from-blue-400 to-cyan-400 mb-2">
            🧠 モデル学習
          </h1>
          <p className="text-gray-400">機械学習モデルのトレーニング</p>
        </div>

        {/* Ultimate版切り替え */}
        <div className="mb-6 p-6 bg-slate-800/50 backdrop-blur-sm rounded-xl border border-blue-500/30">
          <div className="flex items-center justify-between">
            <div>
              <h2 className="text-xl font-bold text-blue-400 mb-1">Ultimate版モード</h2>
              <p className="text-gray-400 text-sm">90列以上の特徴量で学習（通常: 60列）</p>
            </div>
            <button
              onClick={() => setUltimateMode(!ultimateMode)}
              className={`px-6 py-3 rounded-lg font-semibold transition-all ${
                ultimateMode
                  ? 'bg-gradient-to-r from-purple-600 to-pink-600 text-white shadow-lg shadow-purple-500/50'
                  : 'bg-slate-700 text-gray-300 hover:bg-slate-600'
              }`}
            >
              {ultimateMode ? '✨ Ultimate版 ON' : 'Ultimate版 OFF'}
            </button>
          </div>
        </div>

        {/* 学習設定 */}
        <div className="mb-6 p-6 bg-slate-800/50 backdrop-blur-sm rounded-xl border border-blue-500/30">
          <h2 className="text-xl font-bold text-blue-400 mb-4">⚙️ 学習設定</h2>
          
          <div className="grid grid-cols-1 md:grid-cols-2 gap-6">
            {/* 予測ターゲット */}
            <div>
              <label className="block text-gray-400 text-sm mb-2">予測ターゲット</label>
              <select
                value={target}
                onChange={(e) => setTarget(e.target.value as 'win' | 'place3')}
                className="w-full px-4 py-3 bg-slate-700/50 border border-blue-500/30 rounded-lg text-white focus:outline-none focus:border-blue-400"
              >
                <option value="win">🏆 単勝（1着予測）</option>
                <option value="place3">🎯 複勝（3着以内予測）</option>
              </select>
            </div>

            {/* モデルタイプ */}
            <div>
              <label className="block text-gray-400 text-sm mb-2">モデルタイプ</label>
              <select
                value={modelType}
                onChange={(e) => setModelType(e.target.value as any)}
                className="w-full px-4 py-3 bg-slate-700/50 border border-blue-500/30 rounded-lg text-white focus:outline-none focus:border-blue-400"
              >
                <option value="logistic_regression">Logistic Regression</option>
                <option value="lightgbm">LightGBM（推奨）</option>
              </select>
            </div>

            {/* テストサイズ */}
            {/* テストサイズ */}
            <div>
              <label className="block text-gray-400 text-sm mb-2">テストデータ割合</label>
              <input
                type="number"
                min="0.1"
                max="0.5"
                step="0.05"
                value={testSize}
                onChange={(e) => setTestSize(parseFloat(e.target.value))}
                className="w-full px-4 py-3 bg-slate-700/50 border border-blue-500/30 rounded-lg text-white focus:outline-none focus:border-blue-400"
              />
            </div>

            {/* CVフォールド数 */}
            <div>
              <label className="block text-gray-400 text-sm mb-2">CVフォールド数</label>
              <input
                type="number"
                min="2"
                max="10"
                step="1"
                value={cvFolds}
                onChange={(e) => setCvFolds(parseInt(e.target.value))}
                className="w-full px-4 py-3 bg-slate-700/50 border border-blue-500/30 rounded-lg text-white focus:outline-none focus:border-blue-400"
              />
            </div>
          </div>

          {/* Optuna最適化設定 */}
          {modelType === 'lightgbm' && (
            <div className="mt-6 p-6 bg-gradient-to-r from-purple-900/20 to-pink-900/20 border border-purple-500/30 rounded-lg">
              <div className="flex items-center justify-between mb-4">
                <div>
                  <h3 className="text-lg font-bold text-purple-400">🔬 Optunaハイパーパラメータ最適化</h3>
                  <p className="text-gray-400 text-sm">ベイズ最適化で最適なパラメータを自動探索</p>
                </div>
                <label className="relative inline-flex items-center cursor-pointer">
                  <input
                    type="checkbox"
                    checked={useOptuna}
                    onChange={(e) => setUseOptuna(e.target.checked)}
                    className="sr-only peer"
                  />
                  <div className="w-14 h-7 bg-slate-700 peer-focus:outline-none peer-focus:ring-4 peer-focus:ring-purple-800 rounded-full peer peer-checked:after:translate-x-full peer-checked:after:border-white after:content-[''] after:absolute after:top-0.5 after:left-[4px] after:bg-white after:border-gray-300 after:border after:rounded-full after:h-6 after:w-6 after:transition-all peer-checked:bg-gradient-to-r peer-checked:from-purple-600 peer-checked:to-pink-600"></div>
                </label>
              </div>

              {useOptuna && (
                <div className="grid grid-cols-1 md:grid-cols-2 gap-4 mt-4">
                  {/* 試行回数 */}
                  <div>
                    <label className="block text-gray-400 text-sm mb-2">
                      試行回数 (Trials): <span className="text-purple-400 font-semibold">{optunaTrials}</span>
                    </label>
                    <input
                      type="range"
                      min="3"
                      max="100"
                      step="1"
                      value={optunaTrials}
                      onChange={(e) => setOptunaTrials(parseInt(e.target.value))}
                      className="w-full h-2 bg-slate-700 rounded-lg appearance-none cursor-pointer accent-purple-600"
                    />
                    <div className="flex justify-between text-xs text-gray-500 mt-1">
                      <span>3</span>
                      <span>100</span>
                    </div>
                  </div>

                  {/* タイムアウト */}
                  <div>
                    <label className="block text-gray-400 text-sm mb-2">タイムアウト (秒)</label>
                    <input
                      type="number"
                      min="60"
                      max="3600"
                      step="60"
                      value={optunaTimeout}
                      onChange={(e) => setOptunaTimeout(parseInt(e.target.value))}
                      className="w-full px-4 py-3 bg-slate-700/50 border border-purple-500/30 rounded-lg text-white focus:outline-none focus:border-purple-400"
                    />
                  </div>
                </div>
              )}

              {useOptuna && (
                <div className="mt-4 p-4 bg-purple-900/20 rounded-lg border border-purple-500/20">
                  <p className="text-sm text-gray-400">
                    ⏱️ 推定実行時間: <span className="text-purple-400 font-semibold">{Math.round(optunaTrials * cvFolds * 0.3 / 60)}分 〜 {Math.round(optunaTrials * cvFolds * 0.5 / 60)}分</span>
                  </p>
                </div>
              )}
            </div>
          )}

          <button
            onClick={handleTrain}
            disabled={loading}
            className="mt-6 w-full px-8 py-4 bg-gradient-to-r from-green-600 to-teal-600 text-white font-bold rounded-lg hover:from-green-500 hover:to-teal-500 disabled:opacity-50 disabled:cursor-not-allowed transition-all shadow-lg shadow-green-500/20"
          >
            {loading ? '学習中...' : '🚀 学習開始'}
          </button>
        </div>

        {/* 学習結果 */}
        {trainResult && (
          <div className="mb-6 p-6 bg-slate-800/50 backdrop-blur-sm rounded-xl border border-green-500/30">
            <h2 className="text-xl font-bold text-green-400 mb-4">✅ 学習結果</h2>
            <div className="grid grid-cols-2 md:grid-cols-4 gap-4">
              <div className="p-4 bg-slate-700/50 rounded-lg">
                <p className="text-gray-400 text-sm">AUC</p>
                <p className="text-2xl font-bold text-white">{trainResult.auc?.toFixed(4)}</p>
              </div>
              <div className="p-4 bg-slate-700/50 rounded-lg">
                <p className="text-gray-400 text-sm">Log Loss</p>
                <p className="text-2xl font-bold text-white">{trainResult.logloss?.toFixed(4)}</p>
              </div>
              <div className="p-4 bg-slate-700/50 rounded-lg">
                <p className="text-gray-400 text-sm">学習データ数</p>
                <p className="text-2xl font-bold text-white">{trainResult.n_rows}</p>
              </div>
              <div className="p-4 bg-slate-700/50 rounded-lg">
                <p className="text-gray-400 text-sm">モデルID</p>
                <p className="text-sm font-bold text-cyan-400">{trainResult.model_id}</p>
              </div>
            </div>
          </div>
        )}

        {/* 保存済みモデル一覧 */}
        <div className="p-6 bg-slate-800/50 backdrop-blur-sm rounded-xl border border-blue-500/30">
          <h2 className="text-xl font-bold text-blue-400 mb-4">📦 保存済みモデル</h2>
          {models.length === 0 ? (
            <p className="text-gray-400">モデルがありません</p>
          ) : (
            <div className="space-y-3">
              {models.map((model: any, index: number) => (
                <div key={index} className="p-4 bg-slate-700/50 rounded-lg border border-blue-500/20 hover:border-blue-400 transition-all">
                  <div className="flex items-center justify-between">
                    <div className="flex-1">
                      <p className="text-white font-semibold">{model.model_id}</p>
                      <p className="text-gray-400 text-sm">{model.target} | {model.model_type}</p>
                    </div>
                    <div className="text-right">
                      <p className="text-cyan-400 font-bold">AUC: {model.auc?.toFixed(4)}</p>
                      <p className="text-gray-400 text-sm">{model.n_rows} rows</p>
                    </div>
                  </div>
                </div>
              ))}
            </div>
          )}
        </div>
      </div>
    </div>
  )
}
