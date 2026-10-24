'use client'

import { useCallback, useEffect, useState, type FormEvent, type ReactNode } from 'react'
import Link from 'next/link'
import { useRouter } from 'next/navigation'
import { Logo } from '@/components/Logo'
import { useAuth } from '@/contexts/AuthContext'
import { authFetch } from '@/lib/auth-fetch'
import { supabase } from '@/lib/supabase'

type Grant = { mode: 'local-session' | 'step-up'; expiresAt: number | null }
type GrantCheck = { state: 'allowed'; grant: Grant } | { state: 'denied' } | { state: 'unavailable' }

function readGrant(value: unknown): Grant | null {
  if (typeof value !== 'object' || value === null || Array.isArray(value)) return null
  const record = value as Record<string, unknown>
  if (record.version === 2 && record.unlocked === true && record.mode === 'local-session' && record.expires_at === null) {
    return { mode: 'local-session', expiresAt: null }
  }
  if (record.version !== 1 || record.unlocked !== true || typeof record.expires_at !== 'string') return null
  const expiresAt = Date.parse(record.expires_at)
  return Number.isFinite(expiresAt) && expiresAt > Date.now() ? { mode: 'step-up', expiresAt } : null
}

export function AdminActionRouteGuard({ children }: { children: ReactNode }) {
  const router = useRouter()
  const { userId, isAdmin, loading, authorizationUnavailable, refreshAuthorization } = useAuth()
  const [allowedUserId, setAllowedUserId] = useState<string | null>(null)
  const [grant, setGrant] = useState<Grant | null>(null)
  const [temporarilyUnavailable, setTemporarilyUnavailable] = useState(false)
  const [checking, setChecking] = useState(true)
  const [password, setPassword] = useState('')
  const [error, setError] = useState('')
  const [verifying, setVerifying] = useState(false)

  const clearGrant = useCallback((message = '') => {
    setAllowedUserId(null)
    setGrant(null)
    setTemporarilyUnavailable(false)
    setPassword('')
    setError(message)
  }, [])

  const readCurrentGrant = useCallback(async (): Promise<GrantCheck> => {
    if (!isAdmin || !userId) return { state: 'denied' }
    try {
      const response = await authFetch('/api/admin/unlock', {
        method: 'GET',
        cache: 'no-store',
        signal: AbortSignal.timeout(10_000),
      })
      const payload: unknown = await response.json().catch(() => null)
      if (response.status === 401 || response.status === 403) return { state: 'denied' }
      if (!response.ok) return { state: 'unavailable' }
      const verifiedGrant = readGrant(payload)
      return verifiedGrant ? { state: 'allowed', grant: verifiedGrant } : { state: 'unavailable' }
    } catch {
      return { state: 'unavailable' }
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
    void readCurrentGrant().then(result => {
      if (!active) return
      if (result.state !== 'allowed') {
        clearGrant(result.state === 'unavailable' ? '接続を確認しています。少し待って再読み込みしてください。' : '')
      } else {
        setAllowedUserId(userId)
        setGrant(result.grant)
        setTemporarilyUnavailable(false)
        setError('')
      }
      setChecking(false)
    })

    return () => { active = false }
  }, [clearGrant, isAdmin, loading, readCurrentGrant, router, userId])

  useEffect(() => {
    if (allowedUserId !== userId || grant?.expiresAt == null) return
    const timer = window.setTimeout(() => {
      clearGrant('管理機能の確認期限が切れました。もう一度パスワードを入力してください。')
    }, Math.max(0, grant.expiresAt - Date.now()))
    return () => window.clearTimeout(timer)
  }, [allowedUserId, clearGrant, grant, userId])

  useEffect(() => {
    if (!userId || allowedUserId !== userId) return
    let active = true
    let inFlight = false
    const pollTimer = window.setInterval(() => {
      if (inFlight) return
      inFlight = true
      void readCurrentGrant().then(result => {
        if (!active) return
        if (result.state === 'denied') {
          clearGrant('管理機能の認証を再確認できませんでした。')
          void refreshAuthorization()
          return
        }
        if (result.state === 'unavailable') {
          // Keep the job/progress component mounted, but block new interactions.
          setTemporarilyUnavailable(true)
          return
        }
        setTemporarilyUnavailable(false)
        setGrant(result.grant)
        if (authorizationUnavailable) void refreshAuthorization()
      }).finally(() => { inFlight = false })
    }, 30_000)
    return () => { active = false; window.clearInterval(pollTimer) }
  }, [allowedUserId, authorizationUnavailable, clearGrant, readCurrentGrant, refreshAuthorization, userId])

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
      const verifiedGrant = response.ok ? readGrant(payload) : null
      if (verifiedGrant === null) {
        if (response.status === 401 || response.status === 403) void refreshAuthorization()
        throw new Error('admin-action-unlock-rejected')
      }

      setAllowedUserId(currentUser.id)
      setGrant(verifiedGrant)
      setTemporarilyUnavailable(false)
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

  if (allowedUserId === userId && grant !== null) {
    const paused = temporarilyUnavailable || authorizationUnavailable
    return (
      <div>
        {paused && <p role="status" className="bg-[#1e1e1e] px-4 py-2 text-sm text-[#ccc]">認証の接続を再確認中です。操作は接続回復後に再開できます。</p>}
        <div inert={paused}>{children}</div>
      </div>
    )
  }

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
