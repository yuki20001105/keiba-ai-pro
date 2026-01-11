'use client'

import Link from 'next/link'
import InstallPWA from '@/components/InstallPWA'

export default function Home() {

export default function Home() {
  return (
    <main className="min-h-screen bg-gradient-to-br from-slate-900 via-blue-900 to-slate-900">
      {/* Header */}
      <header className="bg-slate-800/50 backdrop-blur-md border-b border-blue-500/20">
        <div className="container mx-auto px-6 py-4 flex justify-between items-center">
          <h1 className="text-3xl font-bold text-transparent bg-clip-text bg-gradient-to-r from-blue-400 to-cyan-400">
            🏇 競馬AI Pro
          </h1>
          <div className="flex items-center gap-4">
            <Link
              href="/auth/login"
              className="px-6 py-2 bg-blue-600 hover:bg-blue-500 text-white rounded-lg transition-colors font-medium"
            >
              ログイン
            </Link>
            <Link
              href="/auth/signup"
              className="px-6 py-2 bg-slate-700 hover:bg-slate-600 text-white rounded-lg transition-colors font-medium"
            >
              新規登録
            </Link>
          </div>
        </div>
      </header>

      {/* Hero Section */}
      <section className="container mx-auto px-6 py-20">
        <div className="max-w-4xl mx-auto text-center mb-16">
          <h2 className="text-6xl font-bold mb-6 text-white">
            AI競馬予測システム
          </h2>
          <p className="text-2xl text-blue-200 mb-12">
            機械学習による高精度な競馬予測・資金管理システム
          </p>
          <div className="flex gap-6 justify-center">
            <Link
              href="/auth/signup"
              className="px-8 py-4 bg-gradient-to-r from-blue-600 to-cyan-600 hover:from-blue-500 hover:to-cyan-500 text-white text-lg rounded-lg transition-all transform hover:scale-105 font-bold shadow-lg shadow-blue-500/50"
            >
              今すぐ始める →
            </Link>
            <Link
              href="/auth/login"
              className="px-8 py-4 bg-slate-700 hover:bg-slate-600 text-white text-lg rounded-lg transition-all font-medium"
            >
              ログイン
            </Link>
          </div>
        </div>
        
        {/* Features Grid */}
        <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-6 max-w-6xl mx-auto mb-20">
          {/* データ取得 */}
          <div className="bg-slate-800/50 backdrop-blur-sm border border-blue-500/30 rounded-xl p-8">
            <div className="text-5xl mb-4">📊</div>
            <h3 className="text-2xl font-bold text-white mb-2">データ取得</h3>
            <p className="text-blue-200">レース情報を自動取得</p>
          </div>

          {/* 学習 */}
          <div className="bg-slate-800/50 backdrop-blur-sm border border-blue-500/30 rounded-xl p-8">
            <div className="text-5xl mb-4">🧠</div>
            <h3 className="text-2xl font-bold text-white mb-2">モデル学習</h3>
            <p className="text-blue-200">AIモデルをトレーニング</p>
          </div>

          {/* 予測 */}
          <div className="bg-slate-800/50 backdrop-blur-sm border border-blue-500/30 rounded-xl p-8">
            <div className="text-5xl mb-4">🎯</div>
            <h3 className="text-2xl font-bold text-white mb-2">予測実行</h3>
            <p className="text-blue-200">レース結果を予測</p>
          </div>

          {/* 購入推奨 */}
          <div className="bg-slate-800/50 backdrop-blur-sm border border-blue-500/30 rounded-xl p-8">
            <div className="text-5xl mb-4">💰</div>
            <h3 className="text-2xl font-bold text-white mb-2">購入推奨</h3>
            <p className="text-blue-200">最適な馬券を提案</p>
          </div>

          {/* 履歴・統計 */}
          <div className="bg-slate-800/50 backdrop-blur-sm border border-blue-500/30 rounded-xl p-8">
            <div className="text-5xl mb-4">📈</div>
            <h3 className="text-2xl font-bold text-white mb-2">履歴・統計</h3>
            <p className="text-blue-200">購入履歴と成績</p>
          </div>

          {/* Ultimate Mode */}
          <div className="bg-slate-800/50 backdrop-blur-sm border border-purple-500/30 rounded-xl p-8">
            <div className="text-5xl mb-4">✨</div>
            <h3 className="text-2xl font-bold text-transparent bg-clip-text bg-gradient-to-r from-purple-400 to-pink-400 mb-2">Ultimate Mode</h3>
            <p className="text-blue-200">高度な予測機能</p>
          </div>
        </div>

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
