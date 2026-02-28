'use client'

import { useState, useEffect } from 'react'
import Link from 'next/link'
import { Logo } from '@/components/Logo'

const API_URL = process.env.NEXT_PUBLIC_API_URL || 'http://localhost:8000'

export default function PredictBatchPage() {
  const [loading, setLoading] = useState(false)
  const [raceId, setRaceId] = useState('')
  const [modelId, setModelId] = useState<string | null>(null)
  const [models, setModels] = useState<any[]>([])
  const [predictions, setPredictions] = useState<any[]>([])
  const [recommendations, setRecommendations] = useState<any>(null)

  useEffect(() => {
    loadModels()
  }, [])

  const loadModels = async () => {
    try {
      const res = await fetch(`/api/models?ultimate=true`)
      if (res.ok) {
        const data = await res.json()
        setModels(data.models || [])
        if (data.models?.length > 0) setModelId(data.models[0].model_id)
      }
    } catch {}
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
      const res = await fetch(`/api/analyze-race`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ race_id: raceId, model_id: modelId, bankroll: 10000, risk_mode: 'balanced', ultimate_mode: true })
      })
      if (!res.ok) { const e = await res.json(); throw new Error(e.detail || `HTTP ${res.status}`) }
      const data = await res.json()
      setPredictions(data.predictions || [])
      setRecommendations(data.recommendations || null)
    } catch (e: any) {
      alert(`予測エラー: ${e.message}`)
    } finally {
      setLoading(false)
    }
  }

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
          <span className="text-sm text-[#888]">予測実行</span>
        </div>
      </header>

      <main className="max-w-3xl mx-auto px-6 py-10 space-y-6">
        <div className="bg-[#111] border border-[#1e1e1e] rounded-lg p-6 space-y-4">
          <div>
            <label className="text-xs text-[#666] block mb-2">レースID</label>
            <input
              type="text"
              placeholder="例: 202406010101"
              value={raceId}
              onChange={e => setRaceId(e.target.value)}
              onKeyDown={e => e.key === 'Enter' && handlePredict()}
              className="w-full px-4 py-3 bg-[#0a0a0a] border border-[#1e1e1e] rounded-lg text-white placeholder-[#444] focus:outline-none focus:border-[#333] transition-colors"
            />
          </div>
          <div>
            <label className="text-xs text-[#666] block mb-2">使用モデル</label>
            <select
              value={modelId || ''}
              onChange={e => setModelId(e.target.value)}
              className="w-full px-4 py-3 bg-[#0a0a0a] border border-[#1e1e1e] rounded-lg text-white focus:outline-none focus:border-[#333] transition-colors"
            >
              <option value="">最新モデルを自動選択</option>
              {models.map((m, i) => (
                <option key={i} value={m.model_id}>{m.model_id} (AUC: {m.auc?.toFixed(4)})</option>
              ))}
            </select>
          </div>
          <button
            onClick={handlePredict}
            disabled={loading}
            className="w-full py-3 bg-white text-black font-medium rounded-lg hover:bg-[#eee] disabled:opacity-40 disabled:cursor-not-allowed transition-colors"
          >
            {loading ? '予測中...' : '予測実行'}
          </button>
        </div>

        {predictions.length > 0 && (
          <div className="bg-[#111] border border-[#1e1e1e] rounded-lg overflow-hidden">
            <div className="px-5 py-3 border-b border-[#1e1e1e]">
              <span className="text-sm font-medium text-[#888]">予測結果</span>
            </div>
            <table className="w-full text-sm">
              <thead>
                <tr className="border-b border-[#1e1e1e]">
                  {['馬番', '馬名', '騎手', '勝率', 'オッズ'].map(h => (
                    <th key={h} className="px-4 py-3 text-left text-xs text-[#555] font-normal first:pl-5">{h}</th>
                  ))}
                </tr>
              </thead>
              <tbody>
                {predictions.map((p, i) => (
                  <tr key={i} className="border-b border-[#1a1a1a] hover:bg-[#161616] transition-colors">
                    <td className="px-4 py-3 pl-5 font-bold">{p.horse_no}</td>
                    <td className="px-4 py-3">{p.horse_name}</td>
                    <td className="px-4 py-3 text-[#888]">{p.jockey_name}</td>
                    <td className="px-4 py-3 text-[#4ade80] font-medium">{(p.probability * 100).toFixed(1)}%</td>
                    <td className="px-4 py-3 text-[#888]">{p.odds}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}

        {recommendations && (
          <div className="bg-[#111] border border-[#1e1e1e] rounded-lg p-6">
            <div className="text-sm font-medium text-[#888] mb-4">購入推奨</div>
            <div className="grid grid-cols-3 gap-3 mb-4">
              <div className="bg-[#0a0a0a] border border-[#1e1e1e] rounded-lg p-4">
                <div className="text-xs text-[#555] mb-1">レースレベル</div>
                <div className="font-bold">{recommendations.race_level}</div>
              </div>
              <div className="bg-[#0a0a0a] border border-[#1e1e1e] rounded-lg p-4">
                <div className="text-xs text-[#555] mb-1">推奨単価</div>
                <div className="font-bold">¥{recommendations.unit_price}</div>
              </div>
              <div className="bg-[#0a0a0a] border border-[#1e1e1e] rounded-lg p-4">
                <div className="text-xs text-[#555] mb-1">券種</div>
                <div className="font-bold">{recommendations.bet_type}</div>
              </div>
            </div>
            <p className="text-sm text-[#888]">{recommendations.strategy}</p>
          </div>
        )}
        <div className="p-5 bg-[#111] border border-[#1e1e1e] rounded-lg flex items-center justify-between gap-4">
          <div>
            <div className="text-xs text-[#666] mb-0.5">次のステップ — 04</div>
            <div className="text-sm font-medium">履歴・統計</div>
            <div className="text-xs text-[#555] mt-0.5">購入履歴と成績を確認します</div>
          </div>
          <Link
            href="/dashboard"
            className="shrink-0 flex items-center gap-1.5 bg-white text-black text-sm font-medium px-5 py-2.5 rounded hover:bg-[#eee] transition-colors"
          >
            履歴・統計へ
            <svg className="w-3.5 h-3.5" fill="none" viewBox="0 0 24 24" stroke="currentColor">
              <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M9 5l7 7-7 7" />
            </svg>
          </Link>
        </div>      </main>
    </div>
  )
}
