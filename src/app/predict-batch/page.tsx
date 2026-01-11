'use client'

import { useState, useEffect } from 'react'
import { supabase } from '@/lib/supabase'
import { useRouter } from 'next/navigation'
import { useUltimateMode } from '@/contexts/UltimateModeContext'

export default function PredictBatchPage() {
  const router = useRouter()
  const [loading, setLoading] = useState(false)
  const [authLoading, setAuthLoading] = useState(true)
  const [userId, setUserId] = useState<string | null>(null)
  const { ultimateMode, setUltimateMode } = useUltimateMode()

  // 予測設定
  const [raceId, setRaceId] = useState('')
  const [modelId, setModelId] = useState<string | null>(null)
  const [models, setModels] = useState<any[]>([])

  // 予測結果
  const [predictions, setPredictions] = useState<any[]>([])
  const [recommendations, setRecommendations] = useState<any>(null)

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
        if (data.models && data.models.length > 0) {
          setModelId(data.models[0].model_id)
        }
      }
    } catch (error) {
      console.error('モデル一覧取得エラー:', error)
    }
  }

  const handlePredict = async () => {
    if (!raceId.trim()) {
      alert('レースIDを入力してください')
      return
    }

    setLoading(true)
    setPredictions([])
    setRecommendations(null)

    try {
      const response = await fetch('http://localhost:8000/api/analyze_race', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          race_id: raceId,
          model_id: modelId,
          bankroll: 10000,
          risk_mode: 'balanced',
          ultimate_mode: ultimateMode
        })
      })

      if (!response.ok) {
        const errorData = await response.json()
        throw new Error(errorData.detail || `HTTP ${response.status}`)
      }

      const data = await response.json()
      setPredictions(data.predictions || [])
      setRecommendations(data.recommendations || null)
      alert('予測完了！')
    } catch (error: any) {
      alert(`予測エラー: ${error.message}`)
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
            🎯 予測 & 購入推奨
          </h1>
          <p className="text-gray-400">レース結果予測と購入戦略提案</p>
        </div>

        {/* Ultimate版切り替え */}
        <div className="mb-6 p-6 bg-slate-800/50 backdrop-blur-sm rounded-xl border border-blue-500/30">
          <div className="flex items-center justify-between">
            <div>
              <h2 className="text-xl font-bold text-blue-400 mb-1">Ultimate版モード</h2>
              <p className="text-gray-400 text-sm">90列特徴量で予測精度向上</p>
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

        {/* 予測設定 */}
        <div className="mb-6 p-6 bg-slate-800/50 backdrop-blur-sm rounded-xl border border-blue-500/30">
          <h2 className="text-xl font-bold text-blue-400 mb-4">⚙️ 予測設定</h2>
          
          <div className="grid grid-cols-1 md:grid-cols-2 gap-6 mb-6">
            {/* レースID */}
            <div>
              <label className="block text-gray-400 text-sm mb-2">レースID</label>
              <input
                type="text"
                placeholder="例: 202406010101"
                value={raceId}
                onChange={(e) => setRaceId(e.target.value)}
                className="w-full px-4 py-3 bg-slate-700/50 border border-blue-500/30 rounded-lg text-white placeholder-gray-500 focus:outline-none focus:border-blue-400"
              />
            </div>

            {/* モデル選択 */}
            <div>
              <label className="block text-gray-400 text-sm mb-2">使用モデル</label>
              <select
                value={modelId || ''}
                onChange={(e) => setModelId(e.target.value)}
                className="w-full px-4 py-3 bg-slate-700/50 border border-blue-500/30 rounded-lg text-white focus:outline-none focus:border-blue-400"
              >
                <option value="">最新モデルを自動選択</option>
                {models.map((model: any, index: number) => (
                  <option key={index} value={model.model_id}>
                    {model.model_id} (AUC: {model.auc?.toFixed(4)})
                  </option>
                ))}
              </select>
            </div>
          </div>

          <button
            onClick={handlePredict}
            disabled={loading}
            className="w-full px-8 py-4 bg-gradient-to-r from-purple-600 to-pink-600 text-white font-bold rounded-lg hover:from-purple-500 hover:to-pink-500 disabled:opacity-50 disabled:cursor-not-allowed transition-all shadow-lg shadow-purple-500/20"
          >
            {loading ? '予測中...' : '🚀 予測実行'}
          </button>
        </div>

        {/* 予測結果 */}
        {predictions.length > 0 && (
          <div className="mb-6 p-6 bg-slate-800/50 backdrop-blur-sm rounded-xl border border-green-500/30">
            <h2 className="text-xl font-bold text-green-400 mb-4">📊 予測結果</h2>
            <div className="space-y-3">
              {predictions.map((pred: any, index: number) => (
                <div key={index} className="p-4 bg-slate-700/50 rounded-lg border border-blue-500/20 hover:border-blue-400 transition-all">
                  <div className="flex items-center justify-between">
                    <div className="flex items-center gap-4">
                      <div className="text-2xl font-bold text-white w-12">{pred.horse_no}</div>
                      <div>
                        <p className="text-white font-semibold">{pred.horse_name}</p>
                        <p className="text-gray-400 text-sm">{pred.jockey_name}</p>
                      </div>
                    </div>
                    <div className="text-right">
                      <p className="text-cyan-400 font-bold text-xl">{(pred.probability * 100).toFixed(1)}%</p>
                      <p className="text-gray-400 text-sm">オッズ: {pred.odds}</p>
                    </div>
                  </div>
                </div>
              ))}
            </div>
          </div>
        )}

        {/* 購入推奨 */}
        {recommendations && (
          <div className="p-6 bg-slate-800/50 backdrop-blur-sm rounded-xl border border-yellow-500/30">
            <h2 className="text-xl font-bold text-yellow-400 mb-4">💰 購入推奨</h2>
            <div className="grid grid-cols-1 md:grid-cols-3 gap-4 mb-4">
              <div className="p-4 bg-slate-700/50 rounded-lg">
                <p className="text-gray-400 text-sm">レースレベル</p>
                <p className="text-2xl font-bold text-white">{recommendations.race_level}</p>
              </div>
              <div className="p-4 bg-slate-700/50 rounded-lg">
                <p className="text-gray-400 text-sm">推奨単価</p>
                <p className="text-2xl font-bold text-white">¥{recommendations.unit_price}</p>
              </div>
              <div className="p-4 bg-slate-700/50 rounded-lg">
                <p className="text-gray-400 text-sm">推奨券種</p>
                <p className="text-2xl font-bold text-white">{recommendations.bet_type}</p>
              </div>
            </div>
            <div className="p-4 bg-yellow-900/20 rounded-lg border border-yellow-500/30">
              <p className="text-yellow-300 text-sm">{recommendations.strategy}</p>
            </div>
          </div>
        )}
      </div>
    </div>
  )
}
