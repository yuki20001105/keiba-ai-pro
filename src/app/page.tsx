import Link from 'next/link'
import { Logo } from '@/components/Logo'

export default function LandingPage() {
  return (
    <main className="min-h-screen bg-[#0a0a0a] text-white flex flex-col">
      {/* Header */}
      <header className="border-b border-[#1e1e1e] px-6 py-4 flex items-center justify-between">
        <Logo href="/home" />
        <Link
          href="/home"
          className="text-xs text-[#888] hover:text-white transition-colors"
        >
          アプリへ
        </Link>
      </header>

      {/* Hero */}
      <section className="flex-1 flex flex-col items-center justify-center text-center px-6 py-24">
        <p className="text-xs text-[#666] tracking-widest uppercase mb-6">Machine Learning · Horse Racing</p>
        <h1 className="text-4xl sm:text-5xl font-bold tracking-tight mb-5 leading-tight">
          AIが、競馬予測を<br />もっと精度高く。
        </h1>
        <p className="text-[#888] text-base sm:text-lg max-w-xl mb-10 leading-relaxed">
          データ収集からモデル学習・予測実行まで一括で行える、
          機械学習ベースの競馬予測システムです。
        </p>
        <Link
          href="/home"
          className="bg-white text-black text-sm font-semibold px-8 py-3.5 rounded hover:bg-[#eee] transition-colors"
        >
          予測を始める
        </Link>
      </section>

      {/* Features */}
      <section id="features" className="border-t border-[#1e1e1e] px-6 py-16">
        <div className="max-w-3xl mx-auto">
          <h2 className="text-xs text-[#666] tracking-widest uppercase text-center mb-10">Features</h2>
          <div className="grid sm:grid-cols-3 gap-4">
            <div className="bg-[#111] border border-[#1e1e1e] rounded-lg p-5">
              <p className="text-xs text-[#666] mb-2">01</p>
              <h3 className="text-sm font-semibold mb-2">データ収集</h3>
              <p className="text-xs text-[#888] leading-relaxed">
                netkeibaからレース情報・オッズ・払戻を自動取得。大量データを高速に蓄積。
              </p>
            </div>
            <div className="bg-[#111] border border-[#1e1e1e] rounded-lg p-5">
              <p className="text-xs text-[#666] mb-2">02</p>
              <h3 className="text-sm font-semibold mb-2">AIモデル学習</h3>
              <p className="text-xs text-[#888] leading-relaxed">
                LightGBM + Optunaで自動最適化。約90の特徴量から高精度な勝率予測モデルを構築。
              </p>
            </div>
            <div className="bg-[#111] border border-[#1e1e1e] rounded-lg p-5">
              <p className="text-xs text-[#666] mb-2">03</p>
              <h3 className="text-sm font-semibold mb-2">予測・資金管理</h3>
              <p className="text-xs text-[#888] leading-relaxed">
                ケリー基準による賭け金推奨・馬券種別判定まで一括実行。
              </p>
            </div>
          </div>
        </div>
      </section>

      {/* CTA Banner */}
      <section className="border-t border-[#1e1e1e] px-6 py-16 text-center">
        <p className="text-[#888] text-sm mb-6">準備ができたら、すぐに使い始められます。</p>
        <Link
          href="/home"
          className="bg-white text-black text-sm font-semibold px-8 py-3.5 rounded hover:bg-[#eee] transition-colors"
        >
          アプリを開く
        </Link>
      </section>

      {/* Footer */}
      <footer className="border-t border-[#1e1e1e] px-6 py-6 text-center">
        <p className="text-xs text-[#444]">© 2026 競馬AI Pro. All rights reserved.</p>
      </footer>
    </main>
  )
}
