'use client'

import { useState } from 'react'
import { supabase } from '@/lib/supabase'

export default function SignUpPage() {
  const [email, setEmail] = useState('')
  const [password, setPassword] = useState('')
  const [fullName, setFullName] = useState('')
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState('')
  const [success, setSuccess] = useState(false)

  const handleSignUp = async (e: React.FormEvent) => {
    e.preventDefault()
    setLoading(true)
    setError('')

    if (!supabase) {
      setError('Supabase設定が不足しています')
      setLoading(false)
      return
    }

    const { error } = await supabase.auth.signUp({
      email,
      password,
      options: {
        data: {
          full_name: fullName,
        },
      },
    })

    if (error) {
      setError(error.message)
      setLoading(false)
    } else {
      setSuccess(true)
      setTimeout(() => {
        window.location.href = '/'
      }, 2000)
    }
  }

  return (
    <div className="min-h-screen flex items-center justify-center bg-gradient-to-br from-slate-900 via-blue-900 to-slate-900">
      <div className="bg-slate-800/50 backdrop-blur-md border border-blue-500/30 p-8 rounded-xl shadow-2xl w-full max-w-md">
        <h1 className="text-3xl font-bold text-center mb-6 text-transparent bg-clip-text bg-gradient-to-r from-blue-400 to-cyan-400">
          🏇 競馬AI Pro
        </h1>
        <h2 className="text-xl font-semibold text-center mb-6 text-white">新規登録</h2>

        {error && (
          <div className="bg-red-900/50 border border-red-500/50 text-red-200 px-4 py-3 rounded mb-4">
            {error}
          </div>
        )}

        {success && (
          <div className="bg-green-900/50 border border-green-500/50 text-green-200 px-4 py-3 rounded mb-4">
            登録成功！ホーム画面に移動します...
          </div>
        )}

        <form onSubmit={handleSignUp}>
          <div className="mb-4">
            <label className="block text-blue-200 mb-2">お名前</label>
            <input
              type="text"
              value={fullName}
              onChange={(e) => setFullName(e.target.value)}
              className="w-full px-4 py-2 bg-slate-700/50 border border-blue-500/30 rounded-lg focus:outline-none focus:ring-2 focus:ring-blue-500 text-white placeholder-slate-400"
              placeholder="山田 太郎"
              required
            />
          </div>

          <div className="mb-4">
            <label className="block text-blue-200 mb-2">メールアドレス</label>
            <input
              type="email"
              value={email}
              onChange={(e) => setEmail(e.target.value)}
              className="w-full px-4 py-2 bg-slate-700/50 border border-blue-500/30 rounded-lg focus:outline-none focus:ring-2 focus:ring-blue-500 text-white placeholder-slate-400"
              placeholder="your@email.com"
              required
            />
          </div>

          <div className="mb-6">
            <label className="block text-blue-200 mb-2">パスワード（8文字以上）</label>
            <input
              type="password"
              value={password}
              onChange={(e) => setPassword(e.target.value)}
              className="w-full px-4 py-2 bg-slate-700/50 border border-blue-500/30 rounded-lg focus:outline-none focus:ring-2 focus:ring-blue-500 text-white placeholder-slate-400"
              placeholder="••••••••"
              minLength={8}
              required
            />
          </div>

          <button
            type="submit"
            disabled={loading || success}
            className="w-full bg-gradient-to-r from-blue-600 to-cyan-600 text-white py-3 rounded-lg font-semibold hover:from-blue-700 hover:to-cyan-700 transition disabled:opacity-50 shadow-lg"
          >
            {loading ? '処理中...' : '登録する'}
          </button>
        </form>

        <div className="mt-6 text-center">
          <a href="/auth/login" className="text-blue-400 hover:text-blue-300 transition">
            すでにアカウントをお持ちの方はログイン
          </a>
        </div>
      </div>
    </div>
  )
}
