'use client'

import { useState } from 'react'
import { useRouter } from 'next/navigation'
import { supabase } from '@/lib/supabase'
import { Logo } from '@/components/Logo'

export default function LoginPage() {
  const router = useRouter()
  const [tab, setTab] = useState<'login' | 'signup'>('login')
  const [email, setEmail] = useState('')
  const [password, setPassword] = useState('')
  const [error, setError] = useState<string | null>(null)
  const [message, setMessage] = useState<string | null>(null)
  const [loading, setLoading] = useState(false)

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault()
    setError(null)
    setMessage(null)
    setLoading(true)

    if (tab === 'login') {
      const { error } = await supabase.auth.signInWithPassword({ email, password })
      if (error) {
        setError(error.message)
      } else {
        window.location.href = '/home'
      }
    } else {
      const { error } = await supabase.auth.signUp({ email, password })
      if (error) {
        setError(error.message)
      } else {
        setMessage('確認メールを送信しました。メールを確認してください。')
      }
    }
    setLoading(false)
  }

  return (
    <main className="min-h-screen bg-[#0a0a0a] text-white flex flex-col items-center justify-center px-4">
      <div className="w-full max-w-sm">
        <div className="mb-8 flex justify-center">
          <Logo href="/" />
        </div>

        {/* タブ */}
        <div className="flex border border-[#333] rounded mb-6 overflow-hidden">
          <button
            onClick={() => { setTab('login'); setError(null); setMessage(null) }}
            className={`flex-1 py-2 text-sm font-medium transition-colors ${tab === 'login' ? 'bg-white text-black' : 'text-[#888] hover:text-white'}`}
          >
            ログイン
          </button>
          <button
            onClick={() => { setTab('signup'); setError(null); setMessage(null) }}
            className={`flex-1 py-2 text-sm font-medium transition-colors ${tab === 'signup' ? 'bg-white text-black' : 'text-[#888] hover:text-white'}`}
          >
            新規登録
          </button>
        </div>

        <form onSubmit={handleSubmit} className="flex flex-col gap-4">
          <div>
            <label className="block text-sm text-[#888] mb-1">メールアドレス</label>
            <input
              type="email"
              value={email}
              onChange={e => setEmail(e.target.value)}
              required
              className="w-full bg-[#111] border border-[#333] rounded px-3 py-2 text-sm text-white focus:outline-none focus:border-[#555]"
            />
          </div>
          <div>
            <label className="block text-sm text-[#888] mb-1">パスワード{tab === 'signup' && <span className="text-[#666]">（6文字以上）</span>}</label>
            <input
              type="password"
              value={password}
              onChange={e => setPassword(e.target.value)}
              required
              minLength={tab === 'signup' ? 6 : undefined}
              className="w-full bg-[#111] border border-[#333] rounded px-3 py-2 text-sm text-white focus:outline-none focus:border-[#555]"
            />
          </div>
          {error && <p className="text-red-400 text-sm">{error}</p>}
          {message && <p className="text-green-400 text-sm">{message}</p>}
          <button
            type="submit"
            disabled={loading}
            className="bg-white text-black text-sm font-semibold py-2.5 rounded hover:bg-[#eee] transition-colors disabled:opacity-50"
          >
            {loading ? '処理中...' : tab === 'login' ? 'ログイン' : '登録する'}
          </button>
        </form>
      </div>
    </main>
  )
}
