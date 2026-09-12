'use client'

import { useCallback, useEffect, useState, type FormEvent, type ReactNode } from 'react'
import Link from 'next/link'
import { useRouter } from 'next/navigation'
import { Logo } from '@/components/Logo'
import { useAuth } from '@/contexts/AuthContext'
import { authFetch } from '@/lib/auth-fetch'
import { supabase } from '@/lib/supabase'

function readExpiry(value: unknown): number | null {
  if (typeof value !== 'object' || value === null || Array.isArray(value)) return null
  const record = value as Record<string, unknown>
  if (record.version !== 1 || record.unlocked !== true || typeof record.expires_at !== 'string') return null
  const expiresAt = Date.parse(record.expires_at)
  return Number.isFinite(expiresAt) && expiresAt > Date.now() ? expiresAt : null
}

export function AdminActionRouteGuard({ children }: { children: ReactNode }) {
  const router = useRouter()
  const { userId, isAdmin, loading, refreshAuthorization } = useAuth()
  const [allowedUserId, setAllowedUserId] = useState<string | null>(null)
  const [expiresAt, setExpiresAt] = useState<number | null>(null)
  const [checking, setChecking] = useState(true)
  const [password, setPassword] = useState('')
  const [error, setError] = useState('')
  const [verifying, setVerifying] = useState(false)

  const clearGrant = useCallback((message = '') => {
    setAllowedUserId(null)
    setExpiresAt(null)
    setPassword('')
    setError(message)
  }, [])

  const readCurrentGrant = useCallback(async () => {
    if (!isAdmin || !userId) return null
    try {
      const response = await authFetch('/api/admin/unlock', {
        method: 'GET',
        cache: 'no-store',
      })
      const payload: unknown = await response.json().catch(() => null)
      return response.ok ? readExpiry(payload) : null
    } catch {
      return null
    }
  }, [isAdmin, userId])

  useEffect(() => {
    if (loading) return
    if (!isAdmin || !userId) {
      setChecking(false)
      clearGrant()
      router.replace('/home')
      return
    }

    let active = true
    setChecking(true)
    void readCurrentGrant().then(expiry => {
      if (!active) return
      if (expiry === null) {
        clearGrant()
      } else {
        setAllowedUserId(userId)
        setExpiresAt(expiry)
        setError('')
      }
      setChecking(false)
    })

    return () => { active = false }
  }, [clearGrant, isAdmin, loading, readCurrentGrant, router, userId])

  useEffect(() => {
    if (allowedUserId !== userId || expiresAt === null) return
    const timer = window.setTimeout(() => {
      clearGrant('管理機能の確認期限が切れました。もう一度パスワードを入力してください。')
    }, Math.max(0, expiresAt - Date.now()))
    return () => window.clearTimeout(timer)
  }, [allowedUserId, clearGrant, expiresAt, userId])

  useEffect(() => {
    if (allowedUserId !== userId) return
    const pollTimer = window.setInterval(() => {
      void readCurrentGrant().then(expiry => {
        if (expiry === null) {
          clearGrant('管理機能の認証を再確認できませんでした。')
          void refreshAuthorization()
          return
        }
        setExpiresAt(expiry)
      })
    }, 30_000)
    return () => window.clearInterval(pollTimer)
  }, [allowedUserId, clearGrant, readCurrentGrant, refreshAuthorization, userId])

  const verifyPassword = async (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault()
    if (!isAdmin || !userId || verifying || password.length === 0) return

    setError('')
    setVerifying(true)
    try {
      const { data: currentData, error: currentError } = await supabase.auth.getUser()
      const currentUser = currentData.user
      if (currentError || !currentUser?.id || currentUser.id !== userId || !currentUser.email) {
        throw new Error('current-user-unavailable')
      }

      const { data: verifiedData, error: verifiedError } = await supabase.auth.signInWithPassword({
        email: currentUser.email,
        password,
      })
      if (verifiedError || verifiedData.user?.id !== currentUser.id) {
        throw new Error('password-verification-failed')
      }

      const response = await authFetch('/api/admin/unlock', {
        method: 'POST',
        cache: 'no-store',
      })
      const payload: unknown = await response.json().catch(() => null)
      const expiry = response.ok ? readExpiry(payload) : null
      if (expiry === null) {
        if (response.status === 401 || response.status === 403) void refreshAuthorization()
        throw new Error('admin-action-unlock-rejected')
      }

      setAllowedUserId(currentUser.id)
      setExpiresAt(expiry)
      setPassword('')
      setError('')
    } catch {
      setPassword('')
      setError('パスワードを確認できませんでした。もう一度入力してください。')
    } finally {
      setVerifying(false)
    }
  }

  if (loading || checking || !isAdmin || !userId) {
    return (
      <div className="min-h-screen bg-[#0a0a0a] text-white">
        <header className="border-b border-[#1e1e1e] px-4 py-4 sm:px-6">
          <Logo href="/home" />
        </header>
        <main className="mx-auto max-w-2xl px-6 py-16">
          <p role="status" aria-live="polite" className="text-sm text-[#888]">管理者権限を確認しています…</p>
        </main>
      </div>
    )
  }

  if (allowedUserId === userId && expiresAt !== null) return children

  return (
    <div className="min-h-screen bg-[#0a0a0a] text-white">
      <header className="flex items-center justify-between border-b border-[#1e1e1e] px-4 py-4 sm:px-6">
        <Logo href="/home" />
        <Link href="/home" className="text-xs text-[#888] transition-colors hover:text-white">← ホーム</Link>
      </header>
      <main className="mx-auto max-w-md px-6 py-16">
        <div className="rounded-xl border border-[#242424] bg-[#111] p-6">
          <h1 className="text-xl font-semibold">管理機能を開く</h1>
          <p className="mt-2 text-sm leading-6 text-[#888]">
            この機能を安全に操作するため、現在のアカウントのパスワードを入力してください。
          </p>

          <form onSubmit={verifyPassword} className="mt-6">
            <label htmlFor="admin-action-password" className="mb-1.5 block text-xs text-[#888]">パスワード</label>
            <input
              id="admin-action-password"
              type="password"
              autoComplete="current-password"
              autoFocus
              required
              maxLength={256}
              value={password}
              onChange={event => setPassword(event.target.value)}
              disabled={verifying}
              className="w-full rounded-lg border border-[#333] bg-[#0a0a0a] px-3 py-2.5 text-sm text-white outline-none transition-colors focus:border-[#666] disabled:opacity-50"
            />

            {error && <p role="alert" className="mt-3 text-sm text-[#f87171]">{error}</p>}

            <button
              type="submit"
              disabled={verifying || password.length === 0}
              className="mt-6 w-full rounded-lg bg-white px-4 py-2.5 text-sm font-medium text-black transition-colors hover:bg-[#eee] disabled:cursor-not-allowed disabled:opacity-50"
            >
              {verifying ? '確認中…' : '確認して開く'}
            </button>
          </form>
        </div>
      </main>
    </div>
  )
}
