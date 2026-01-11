'use client'

import { useEffect, useState } from 'react'
import { supabase } from '@/lib/supabase'
import { User } from '@supabase/supabase-js'
import Link from 'next/link'
import { useRouter } from 'next/navigation'
import { useUltimateMode } from '@/contexts/UltimateModeContext'
import InstallPWA from '@/components/InstallPWA'

export default function Home() {
  const [user, setUser] = useState<User | null>(null)
  const [loading, setLoading] = useState(true)
  const router = useRouter()
  const { ultimateMode, setUltimateMode } = useUltimateMode()

  useEffect(() => {
    if (!supabase) {
      console.error('Supabase client not initialized')
      setLoading(false)
      return
    }

    const checkAuth = async () => {
      const { data: { user } } = await supabase.auth.getUser()
      
      if (!user) {
        // 未認証の場合はログインページへリダイレクト
        router.push('/auth/login')
        return
      }
      
      setUser(user)
      setLoading(false)
    }
    
    checkAuth()

    // 認証状態の変更を監視
    const { data: { subscription } } = supabase.auth.onAuthStateChange((_event: any, session: any) => {
      if (!session?.user) {
        router.push('/auth/login')
      } else {
        setUser(session.user)
        setLoading(false)
      }
    })

    return () => subscription.unsubscribe()
  }, [router])

  // ログアウト処理
  const handleLogout = async () => {
    if (supabase) {
      await supabase.auth.signOut()
    }
    router.push('/auth/login')
  }

  if (loading) {
    return (
      <div className="min-h-screen flex items-center justify-center bg-gradient-to-br from-slate-900 via-blue-900 to-slate-900">
        <div className="text-xl text-white">認証確認中...</div>
      </div>
    )
  }

  return (
    <main className="min-h-screen bg-gradient-to-br from-slate-900 via-blue-900 to-slate-900">
      {/* Header */}
      <header className="bg-slate-800/50 backdrop-blur-md border-b border-blue-500/20">
        <div className="container mx-auto px-6 py-4 flex justify-between items-center">
          <h1 className="text-3xl font-bold text-transparent bg-clip-text bg-gradient-to-r from-blue-400 to-cyan-400">
            🏇 競馬AI Pro
          </h1>
          <div className="flex items-center gap-6">
            {/* Ultimate Mode Toggle */}
            <div className="flex items-center gap-3 bg-slate-700/50 px-4 py-2 rounded-lg border border-blue-500/30">
              <span className={`text-sm font-medium ${ultimateMode ? 'text-gray-400' : 'text-blue-300'}`}>
                Standard
              </span>
              <button
                onClick={() => setUltimateMode(!ultimateMode)}
                className={`relative inline-flex h-6 w-11 items-center rounded-full transition-colors ${
                  ultimateMode ? 'bg-gradient-to-r from-purple-500 to-pink-500' : 'bg-slate-600'
                }`}
              >
                <span
                  className={`inline-block h-4 w-4 transform rounded-full bg-white transition-transform ${
                    ultimateMode ? 'translate-x-6' : 'translate-x-1'
                  }`}
                />
              </button>
              <span className={`text-sm font-medium ${ultimateMode ? 'text-transparent bg-clip-text bg-gradient-to-r from-purple-400 to-pink-400' : 'text-gray-400'}`}>
                Ultimate ✨
              </span>
            </div>
            <span className="text-blue-300 text-sm">{user?.email}</span>
            <button
              onClick={handleLogout}
              className="px-4 py-2 bg-slate-700 hover:bg-slate-600 text-white rounded-lg transition-colors text-sm"
            >
              ログアウト
            </button>
          </div>
        </div>
      </header>

      {/* Hero Section */}
      <section className="container mx-auto px-6 py-16">
        <div className="max-w-4xl mx-auto text-center mb-16">
          <h2 className="text-5xl font-bold mb-6 text-white">
            AI競馬予測システム
          </h2>
          <p className="text-xl text-blue-200 mb-8">
            機械学習による競馬予測・資金管理システム
          </p>
        </div>
        
        {/* Main Navigation */}
        <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-6 max-w-6xl mx-auto">
          {/* データ取得 */}
          <Link href="/data-collection" className="group">
            <div className="bg-slate-800/50 backdrop-blur-sm border border-blue-500/30 rounded-xl p-8 hover:border-blue-400 transition-all hover:shadow-lg hover:shadow-blue-500/20">
              <div className="text-5xl mb-4">📊</div>
              <h3 className="text-2xl font-bold text-white mb-2">データ取得</h3>
              <p className="text-blue-200">レース情報を自動取得</p>
            </div>
          </Link>

          {/* 学習 */}
          <Link href="/train" className="group">
            <div className="bg-slate-800/50 backdrop-blur-sm border border-blue-500/30 rounded-xl p-8 hover:border-blue-400 transition-all hover:shadow-lg hover:shadow-blue-500/20">
              <div className="text-5xl mb-4">🧠</div>
              <h3 className="text-2xl font-bold text-white mb-2">モデル学習</h3>
              <p className="text-blue-200">AIモデルをトレーニング</p>
            </div>
          </Link>

          {/* 予測 */}
          <Link href="/predict-batch" className="group">
            <div className="bg-slate-800/50 backdrop-blur-sm border border-blue-500/30 rounded-xl p-8 hover:border-blue-400 transition-all hover:shadow-lg hover:shadow-blue-500/20">
              <div className="text-5xl mb-4">🎯</div>
              <h3 className="text-2xl font-bold text-white mb-2">予測実行</h3>
              <p className="text-blue-200">レース結果を予測</p>
            </div>
          </Link>

          {/* 購入推奨 */}
          <Link href="/predict-batch" className="group">
            <div className="bg-slate-800/50 backdrop-blur-sm border border-blue-500/30 rounded-xl p-8 hover:border-blue-400 transition-all hover:shadow-lg hover:shadow-blue-500/20">
              <div className="text-5xl mb-4">💰</div>
              <h3 className="text-2xl font-bold text-white mb-2">購入推奨</h3>
              <p className="text-blue-200">最適な馬券を提案</p>
            </div>
          </Link>

          {/* 履歴・統計 */}
          <Link href="/dashboard" className="group">
            <div className="bg-slate-800/50 backdrop-blur-sm border border-blue-500/30 rounded-xl p-8 hover:border-blue-400 transition-all hover:shadow-lg hover:shadow-blue-500/20">
              <div className="text-5xl mb-4">📈</div>
              <h3 className="text-2xl font-bold text-white mb-2">履歴・統計</h3>
              <p className="text-blue-200">購入履歴と成績</p>
            </div>
          </Link>
        </div>
      </section>

      {/* Features Section */}
      <section className="container mx-auto px-6 py-16">
        <h2 className="text-3xl font-bold text-center mb-12 text-white">
          システムの特徴
        </h2>
        
        <div className="grid grid-cols-1 md:grid-cols-2 gap-6 max-w-4xl mx-auto">
          <div className="bg-slate-800/30 border border-blue-500/20 rounded-xl p-6">
            <div className="text-4xl mb-3">🎯</div>
            <h3 className="text-xl font-bold text-white mb-2">高精度AI予測</h3>
            <p className="text-blue-200 text-sm">RandomForest・LightGBMによる機械学習モデル</p>
          </div>

          <div className="bg-slate-800/30 border border-blue-500/20 rounded-xl p-6">
            <div className="text-4xl mb-3">💰</div>
            <h3 className="text-xl font-bold text-white mb-2">資金管理システム</h3>
            <p className="text-blue-200 text-sm">ケリー基準による最適賭け金計算</p>
          </div>

          <div className="bg-slate-800/30 border border-blue-500/20 rounded-xl p-6">
            <div className="text-4xl mb-3">📊</div>
            <h3 className="text-xl font-bold text-white mb-2">自動データ収集</h3>
            <p className="text-blue-200 text-sm">netkeiba.comから最新レース情報を取得</p>
          </div>

          <div className="bg-slate-800/30 border border-blue-500/20 rounded-xl p-6">
            <div className="text-4xl mb-3">📈</div>
            <h3 className="text-xl font-bold text-white mb-2">詳細統計分析</h3>
            <p className="text-blue-200 text-sm">回収率・的中率の自動追跡</p>
          </div>
        </div>
      </section>

      {/* Footer */}
      <footer className="bg-slate-950 border-t border-blue-500/20 py-8 mt-16">
        <div className="container mx-auto px-6 text-center">
          <p className="text-blue-300 text-sm">
            © 2026 競馬AI Pro. All rights reserved.
          </p>
        </div>
      </footer>

      {/* PWAインストールプロンプト */}
      <InstallPWA />
    </main>
  )
}
