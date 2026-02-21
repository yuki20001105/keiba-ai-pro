'use client'

import { useState, useEffect } from 'react'
import { supabase } from '@/lib/supabase'
import { useRouter } from 'next/navigation'
import Link from 'next/link'

interface User {
  id: string
  email: string
  role: 'admin' | 'user'
  full_name: string | null
  subscription_tier: string
  created_at: string
}

interface Stats {
  totalUsers: number
  adminUsers: number
  premiumUsers: number
  totalRaces: number
  totalModels: number
}

export default function AdminDashboard() {
  const router = useRouter()
  const [loading, setLoading] = useState(true)
  const [users, setUsers] = useState<User[]>([])
  const [stats, setStats] = useState<Stats>({
    totalUsers: 0,
    adminUsers: 0,
    premiumUsers: 0,
    totalRaces: 0,
    totalModels: 0
  })

  useEffect(() => {
    loadData()
  }, [])

  const loadData = async () => {
    try {
      // ユーザー一覧取得
      const { data: usersData, error: usersError } = await supabase
        .from('profiles')
        .select('*')
        .order('created_at', { ascending: false })

      if (usersError) throw usersError
      setUsers(usersData || [])

      // 統計情報
      const totalUsers = usersData?.length || 0
      const adminUsers = usersData?.filter((u: User) => u.role === 'admin').length || 0
      const premiumUsers = usersData?.filter((u: User) => u.subscription_tier === 'premium').length || 0

      // レース数を取得
      const racesResponse = await fetch('http://localhost:8000/api/data_stats')
      let totalRaces = 0
      let totalModels = 0
      if (racesResponse.ok) {
        const racesData = await racesResponse.json()
        totalRaces = racesData.total_races || 0
        totalModels = racesData.total_models || 0
      }

      setStats({
        totalUsers,
        adminUsers,
        premiumUsers,
        totalRaces,
        totalModels
      })
    } catch (error) {
      console.error('データ取得エラー:', error)
    } finally {
      setLoading(false)
    }
  }

  const handleRoleChange = async (userId: string, newRole: 'admin' | 'user') => {
    try {
      const { error } = await supabase
        .from('profiles')
        .update({ role: newRole })
        .eq('id', userId)

      if (error) throw error

      alert(`ユーザーのロールを ${newRole} に変更しました`)
      loadData() // 再読み込み
    } catch (error) {
      console.error('ロール変更エラー:', error)
      alert('ロール変更に失敗しました')
    }
  }

  if (loading) {
    return (
      <div className="min-h-screen bg-gradient-to-br from-slate-900 via-blue-900 to-slate-900 flex items-center justify-center">
        <div className="text-white text-xl">読み込み中...</div>
      </div>
    )
  }

  return (
    <div className="min-h-screen bg-gradient-to-br from-slate-900 via-blue-900 to-slate-900">
        {/* Header */}
        <header className="bg-slate-800/50 backdrop-blur-md border-b border-blue-500/20">
          <div className="container mx-auto px-6 py-4 flex justify-between items-center">
            <Link href="/home">
              <h1 className="text-3xl font-bold text-transparent bg-clip-text bg-gradient-to-r from-blue-400 to-cyan-400 cursor-pointer">
                🏇 競馬AI Pro - 管理者ダッシュボード
              </h1>
            </Link>
            <Link href="/home" className="text-blue-400 hover:text-blue-300">
              ← ホームに戻る
            </Link>
          </div>
        </header>

        <div className="container mx-auto px-6 py-8">
          {/* 統計カード */}
          <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-5 gap-4 mb-8">
            <StatCard
              title="総ユーザー数"
              value={stats.totalUsers}
              icon="👥"
              color="blue"
            />
            <StatCard
              title="管理者"
              value={stats.adminUsers}
              icon="👑"
              color="yellow"
            />
            <StatCard
              title="プレミアム会員"
              value={stats.premiumUsers}
              icon="💎"
              color="purple"
            />
            <StatCard
              title="総レース数"
              value={stats.totalRaces}
              icon="🏇"
              color="green"
            />
            <StatCard
              title="学習済みモデル"
              value={stats.totalModels}
              icon="🧠"
              color="cyan"
            />
          </div>

          {/* クイックアクション */}
          <div className="grid grid-cols-1 md:grid-cols-2 gap-4 mb-8">
            <Link href="/data-collection">
              <div className="bg-slate-800/50 backdrop-blur-sm border border-blue-500/30 rounded-xl p-6 hover:border-blue-400 transition-all cursor-pointer group">
                <div className="text-4xl mb-2">📊</div>
                <h3 className="text-xl font-bold text-white mb-2">データ収集</h3>
                <p className="text-blue-200 text-sm">ネットケイバからレース情報を取得</p>
              </div>
            </Link>

            <Link href="/train">
              <div className="bg-slate-800/50 backdrop-blur-sm border border-blue-500/30 rounded-xl p-6 hover:border-blue-400 transition-all cursor-pointer group">
                <div className="text-4xl mb-2">🧠</div>
                <h3 className="text-xl font-bold text-white mb-2">モデル学習</h3>
                <p className="text-blue-200 text-sm">AIモデルをトレーニング</p>
              </div>
            </Link>
          </div>

          {/* ユーザー管理テーブル */}
          <div className="bg-slate-800/50 backdrop-blur-sm border border-blue-500/30 rounded-xl p-6">
            <h2 className="text-2xl font-bold text-white mb-4">👥 ユーザー管理</h2>
            <div className="overflow-x-auto">
              <table className="w-full text-left">
                <thead>
                  <tr className="border-b border-blue-500/30">
                    <th className="py-3 px-4 text-blue-400">メールアドレス</th>
                    <th className="py-3 px-4 text-blue-400">名前</th>
                    <th className="py-3 px-4 text-blue-400">ロール</th>
                    <th className="py-3 px-4 text-blue-400">プラン</th>
                    <th className="py-3 px-4 text-blue-400">登録日</th>
                    <th className="py-3 px-4 text-blue-400">操作</th>
                  </tr>
                </thead>
                <tbody>
                  {users.map((user) => (
                    <tr key={user.id} className="border-b border-slate-700/50 hover:bg-slate-700/30">
                      <td className="py-3 px-4 text-white">{user.email}</td>
                      <td className="py-3 px-4 text-gray-300">{user.full_name || '-'}</td>
                      <td className="py-3 px-4">
                        {user.role === 'admin' ? (
                          <span className="bg-yellow-500/20 text-yellow-400 px-3 py-1 rounded-full text-sm">
                            👑 管理者
                          </span>
                        ) : (
                          <span className="bg-blue-500/20 text-blue-400 px-3 py-1 rounded-full text-sm">
                            👤 ユーザー
                          </span>
                        )}
                      </td>
                      <td className="py-3 px-4">
                        {user.subscription_tier === 'premium' ? (
                          <span className="bg-purple-500/20 text-purple-400 px-3 py-1 rounded-full text-sm">
                            💎 Premium
                          </span>
                        ) : (
                          <span className="bg-gray-500/20 text-gray-400 px-3 py-1 rounded-full text-sm">
                            Free
                          </span>
                        )}
                      </td>
                      <td className="py-3 px-4 text-gray-400 text-sm">
                        {new Date(user.created_at).toLocaleDateString('ja-JP')}
                      </td>
                      <td className="py-3 px-4">
                        <select
                          value={user.role}
                          onChange={(e) => handleRoleChange(user.id, e.target.value as 'admin' | 'user')}
                          className="bg-slate-700 text-white px-3 py-1 rounded border border-blue-500/30 text-sm"
                        >
                          <option value="user">ユーザー</option>
                          <option value="admin">管理者</option>
                        </select>
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          </div>
        </div>
      </div>

function StatCard({ title, value, icon, color }: { title: string; value: number; icon: string; color: string }) {
  const colorClasses = {
    blue: 'from-blue-500 to-cyan-500',
    yellow: 'from-yellow-500 to-orange-500',
    purple: 'from-purple-500 to-pink-500',
    green: 'from-green-500 to-emerald-500',
    cyan: 'from-cyan-500 to-blue-500'
  }

  return (
    <div className="bg-slate-800/50 backdrop-blur-sm border border-blue-500/30 rounded-xl p-6">
      <div className="text-4xl mb-2">{icon}</div>
      <div className="text-gray-400 text-sm mb-1">{title}</div>
      <div className={`text-3xl font-bold bg-gradient-to-r ${colorClasses[color as keyof typeof colorClasses]} bg-clip-text text-transparent`}>
        {value.toLocaleString()}
      </div>
    </div>
  )
}
