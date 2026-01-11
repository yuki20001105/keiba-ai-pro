'use client'

import { useState, useEffect } from 'react'
import { supabase } from '@/lib/supabase'
import { useRouter } from 'next/navigation'

export default function DashboardPage() {
  const router = useRouter()
  const [loading, setLoading] = useState(true)
  const [userId, setUserId] = useState<string | null>(null)
  const [ultimateMode, setUltimateMode] = useState(false)

  // データ統計
  const [dataStats, setDataStats] = useState({
    totalRaces: 0,
    totalHorses: 0,
    totalModels: 0,
    dbPath: ''
  })

  // 購入履歴
  const [bets, setBets] = useState<any[]>([])
  const [bankRecord, setBankRecord] = useState<any>(null)

  useEffect(() => {
    const initData = async () => {
      if (!supabase) {
        console.error('Supabase client not initialized')
        setLoading(false)
        return
      }
      const { data: { user } } = await supabase.auth.getUser()
      if (!user) {
        router.push('/auth/login')
        return
      }
      setUserId(user.id)
      await loadStats()
      await loadBets(user.id)
      setLoading(false)
    }
    initData()
  }, [router, ultimateMode])

  const loadStats = async () => {
    try {
      const dbPath = ultimateMode ? 'keiba_ultimate.db' : 'keiba.db'
      const response = await fetch(`http://localhost:8000/api/data_stats?ultimate=${ultimateMode}`)
      if (response.ok) {
        const data = await response.json()
        setDataStats({
          totalRaces: data.total_races || 0,
          totalHorses: data.total_horses || 0,
          totalModels: data.total_models || 0,
          dbPath: dbPath
        })
      }
    } catch (error) {
      console.error('統計取得エラー:', error)
    }
  }

  const loadBets = async (userId: string) => {
    try {
      const { data: betsData } = await supabase
        .from('bets')
        .select('*')
        .eq('user_id', userId)
        .order('created_at', { ascending: false })
        .limit(10)

      if (betsData) setBets(betsData)

      const { data: bankData } = await supabase
        .from('bank_records')
        .select('*')
        .eq('user_id', userId)
        .single()

      if (bankData) setBankRecord(bankData)
    } catch (error) {
      console.error('購入履歴取得エラー:', error)
    }
  }

  if (loading) {
    return (
      <div className="min-h-screen bg-gradient-to-br from-slate-900 via-blue-900 to-slate-900 flex items-center justify-center">
        <div className="text-2xl text-blue-400 font-bold">読み込み中...</div>
      </div>
    )
  }

  const totalBets = bets.length
  const winningBets = bets.filter(b => (b.profit_loss ?? 0) > 0).length
  const winRate = totalBets > 0 ? ((winningBets / totalBets) * 100).toFixed(1) : '0'
  const totalProfitLoss = bets.reduce((sum, b) => sum + (b.profit_loss ?? 0), 0)

  return (
    <div className="min-h-screen bg-gradient-to-br from-slate-900 via-blue-900 to-slate-900 py-8 px-4">
      <div className="max-w-7xl mx-auto">
        {/* ヘッダー */}
        <div className="mb-8 text-center">
          <h1 className="text-4xl font-bold text-transparent bg-clip-text bg-gradient-to-r from-blue-400 to-cyan-400 mb-2">
            📊 ダッシュボード
          </h1>
          <p className="text-gray-400">システム統計と購入履歴</p>
        </div>

        {/* Ultimate版切り替え */}
        <div className="mb-6 p-6 bg-slate-800/50 backdrop-blur-sm rounded-xl border border-blue-500/30">
          <div className="flex items-center justify-between">
            <div>
              <h2 className="text-xl font-bold text-blue-400 mb-1">Ultimate版モード</h2>
              <p className="text-gray-400 text-sm">統計表示を切り替え</p>
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

        {/* データ統計 */}
        <div className="mb-6">
          <h2 className="text-2xl font-bold text-blue-400 mb-4">📈 システム統計</h2>
          <div className="grid grid-cols-1 md:grid-cols-4 gap-4">
            <div className="p-6 bg-slate-800/50 backdrop-blur-sm rounded-xl border border-blue-500/30 hover:border-blue-400 transition-all">
              <p className="text-gray-400 text-sm mb-2">データベース</p>
              <p className="text-xl font-bold text-white">{dataStats.dbPath}</p>
            </div>
            <div className="p-6 bg-slate-800/50 backdrop-blur-sm rounded-xl border border-blue-500/30 hover:border-blue-400 transition-all">
              <p className="text-gray-400 text-sm mb-2">レース数</p>
              <p className="text-3xl font-bold text-cyan-400">{dataStats.totalRaces.toLocaleString()}</p>
            </div>
            <div className="p-6 bg-slate-800/50 backdrop-blur-sm rounded-xl border border-blue-500/30 hover:border-blue-400 transition-all">
              <p className="text-gray-400 text-sm mb-2">馬数</p>
              <p className="text-3xl font-bold text-cyan-400">{dataStats.totalHorses.toLocaleString()}</p>
            </div>
            <div className="p-6 bg-slate-800/50 backdrop-blur-sm rounded-xl border border-blue-500/30 hover:border-blue-400 transition-all">
              <p className="text-gray-400 text-sm mb-2">モデル数</p>
              <p className="text-3xl font-bold text-cyan-400">{dataStats.totalModels}</p>
            </div>
          </div>
        </div>

        {/* 購入実績 */}
        {bankRecord && (
          <div className="mb-6">
            <h2 className="text-2xl font-bold text-blue-400 mb-4">💰 購入実績</h2>
            <div className="grid grid-cols-1 md:grid-cols-4 gap-4">
              <div className="p-6 bg-slate-800/50 backdrop-blur-sm rounded-xl border border-green-500/30">
                <p className="text-gray-400 text-sm mb-2">現在残高</p>
                <p className="text-3xl font-bold text-green-400">¥{bankRecord.current_balance?.toLocaleString()}</p>
              </div>
              <div className="p-6 bg-slate-800/50 backdrop-blur-sm rounded-xl border border-blue-500/30">
                <p className="text-gray-400 text-sm mb-2">購入回数</p>
                <p className="text-3xl font-bold text-blue-400">{totalBets}</p>
              </div>
              <div className="p-6 bg-slate-800/50 backdrop-blur-sm rounded-xl border border-cyan-500/30">
                <p className="text-gray-400 text-sm mb-2">勝率</p>
                <p className="text-3xl font-bold text-cyan-400">{winRate}%</p>
              </div>
              <div className={`p-6 bg-slate-800/50 backdrop-blur-sm rounded-xl border ${
                totalProfitLoss >= 0 ? 'border-green-500/30' : 'border-red-500/30'
              }`}>
                <p className="text-gray-400 text-sm mb-2">損益</p>
                <p className={`text-3xl font-bold ${totalProfitLoss >= 0 ? 'text-green-400' : 'text-red-400'}`}>
                  {totalProfitLoss >= 0 ? '+' : ''}¥{totalProfitLoss.toLocaleString()}
                </p>
              </div>
            </div>
          </div>
        )}

        {/* 購入履歴 */}
        <div>
          <h2 className="text-2xl font-bold text-blue-400 mb-4">📜 最近の購入履歴</h2>
          <div className="bg-slate-800/50 backdrop-blur-sm rounded-xl border border-blue-500/30 overflow-hidden">
            {bets.length === 0 ? (
              <div className="p-8 text-center text-gray-400">
                購入履歴がありません
              </div>
            ) : (
              <div className="overflow-x-auto">
                <table className="w-full">
                  <thead className="bg-slate-700/50">
                    <tr>
                      <th className="px-6 py-4 text-left text-sm font-semibold text-blue-400">日時</th>
                      <th className="px-6 py-4 text-left text-sm font-semibold text-blue-400">レースID</th>
                      <th className="px-6 py-4 text-left text-sm font-semibold text-blue-400">馬番</th>
                      <th className="px-6 py-4 text-left text-sm font-semibold text-blue-400">券種</th>
                      <th className="px-6 py-4 text-right text-sm font-semibold text-blue-400">金額</th>
                      <th className="px-6 py-4 text-right text-sm font-semibold text-blue-400">損益</th>
                    </tr>
                  </thead>
                  <tbody className="divide-y divide-slate-700">
                    {bets.map((bet: any, index: number) => (
                      <tr key={index} className="hover:bg-slate-700/30 transition-colors">
                        <td className="px-6 py-4 text-sm text-gray-300">
                          {new Date(bet.created_at).toLocaleDateString('ja-JP')}
                        </td>
                        <td className="px-6 py-4 text-sm text-gray-300">{bet.race_id}</td>
                        <td className="px-6 py-4 text-sm text-white font-semibold">{bet.horse_no}</td>
                        <td className="px-6 py-4 text-sm text-gray-300">{bet.bet_type}</td>
                        <td className="px-6 py-4 text-sm text-right text-gray-300">
                          ¥{bet.amount?.toLocaleString()}
                        </td>
                        <td className={`px-6 py-4 text-sm text-right font-semibold ${
                          (bet.profit_loss ?? 0) >= 0 ? 'text-green-400' : 'text-red-400'
                        }`}>
                          {(bet.profit_loss ?? 0) >= 0 ? '+' : ''}¥{(bet.profit_loss ?? 0).toLocaleString()}
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            )}
          </div>
        </div>
      </div>
    </div>
  )
}
