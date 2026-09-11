'use client'

import { useCallback, useEffect, useRef, useState, type FormEvent } from 'react'
import Link from 'next/link'
import { useRouter } from 'next/navigation'
import { useAuth } from '@/contexts/AuthContext'
import { Logo } from '@/components/Logo'
import { AdminWorkspace } from '@/components/AdminWorkspace'
import { supabase } from '@/lib/supabase'
import { authFetch } from '@/lib/auth-fetch'

const FLOW_STEPS = [
  { href: '/predict-batch', label: '予測実行', desc: 'レースを選び、予測・購入推奨を確認', step: '01' },
  { href: '/dashboard', label: '成績確認', desc: '購入履歴と損益・回収率を分析', step: '02' },
]

function readUnlockExpiry(value: unknown): number | null {
  if (typeof value !== 'object' || value === null || Array.isArray(value)) return null
  const record = value as Record<string, unknown>
  if (record.version !== 1 || record.unlocked !== true || typeof record.expires_at !== 'string') return null
  const expiresAt = Date.parse(record.expires_at)
  return Number.isFinite(expiresAt) && expiresAt > Date.now() ? expiresAt : null
}

export default function HomePage() {
  const router = useRouter()
  const { userId, isAdmin, loading: authLoading, refreshAuthorization } = useAuth()
  const [adminMode, setAdminMode] = useState(false)
  const [adminModeExpiresAt, setAdminModeExpiresAt] = useState<number | null>(null)
  const [adminModeUserId, setAdminModeUserId] = useState<string | null>(null)
  const [adminDialogOpen, setAdminDialogOpen] = useState(false)
  const [adminPassword, setAdminPassword] = useState('')
  const [adminError, setAdminError] = useState('')
  const [adminVerifying, setAdminVerifying] = useState(false)
  const [adminNotice, setAdminNotice] = useState('')
  const adminTriggerRef = useRef<HTMLButtonElement>(null)
  const adminDialogRef = useRef<HTMLDivElement>(null)
  const [status, setStatus] = useState<{
    apiOnline: boolean | null
    totalRaces: number | null
    totalModels: number | null
  }>({ apiOnline: null, totalRaces: null, totalModels: null })

  const lockAdminMode = useCallback((notice = '') => {
    setAdminMode(false)
    setAdminModeExpiresAt(null)
    setAdminModeUserId(null)
    setAdminDialogOpen(false)
    setAdminPassword('')
    setAdminError('')
    setAdminNotice(notice)
    void fetch('/api/admin/unlock', {
      method: 'DELETE',
      cache: 'no-store',
      credentials: 'same-origin',
    }).catch(() => undefined)
  }, [])

  const handleAdminAuthorizationFailure = useCallback(() => {
    lockAdminMode('管理者権限を再確認できなかったため、管理者モードを終了しました。')
    void refreshAuthorization()
  }, [lockAdminMode, refreshAuthorization])

  useEffect(() => {
    const controller = new AbortController()
    const timeout = setTimeout(() => controller.abort(), 5000)

    const loadStatus = async () => {
      Promise.all([
        fetch('/api/health', { signal: controller.signal }).then(r => r.ok).catch(() => false),
        authFetch('/api/data-stats', { signal: controller.signal }).then(r => r.ok ? r.json() : null).catch(() => null),
      ]).then(([online, stats]) => {
        clearTimeout(timeout)
        setStatus({
          apiOnline: online as boolean,
          totalRaces: stats?.total_races ?? null,
          totalModels: stats?.total_models ?? null,
        })
      })
    }

    void loadStatus()

    return () => { clearTimeout(timeout); controller.abort() }
  }, [])

  useEffect(() => {
    if (!authLoading && !isAdmin) {
      lockAdminMode()
    }
  }, [authLoading, isAdmin, lockAdminMode])

  useEffect(() => {
    if (authLoading || !isAdmin) return
    const timer = window.setInterval(() => {
      void refreshAuthorization()
    }, 30_000)
    return () => window.clearInterval(timer)
  }, [authLoading, isAdmin, refreshAuthorization])

  useEffect(() => {
    if (authLoading || !isAdmin || !userId) return

    let active = true
    const restoreAdminMode = async () => {
      try {
        const response = await authFetch('/api/admin/unlock', {
          method: 'GET',
          cache: 'no-store',
        })
        const payload: unknown = await response.json().catch(() => null)
        const expiresAt = response.ok ? readUnlockExpiry(payload) : null
        if (!active || expiresAt === null) return
        setAdminModeExpiresAt(expiresAt)
        setAdminModeUserId(userId)
        setAdminMode(true)
      } catch {
        // A missing, expired, or unreachable grant leaves the Home page locked.
      }
    }

    void restoreAdminMode()
    return () => { active = false }
  }, [authLoading, isAdmin, userId])

  useEffect(() => {
    if (adminMode && adminModeUserId !== userId) {
      lockAdminMode('ログイン状態が変わったため、管理者モードを終了しました。')
    }
  }, [adminMode, adminModeUserId, lockAdminMode, userId])

  useEffect(() => {
    if (!adminMode || adminModeExpiresAt === null) return

    const delay = Math.max(0, adminModeExpiresAt - Date.now())
    const timer = window.setTimeout(() => {
      lockAdminMode('管理者モードの有効期限が切れました。')
    }, delay)

    return () => window.clearTimeout(timer)
  }, [adminMode, adminModeExpiresAt, lockAdminMode])

  useEffect(() => {
    if (!adminNotice) return
    const timer = window.setTimeout(() => setAdminNotice(''), 5000)
    return () => window.clearTimeout(timer)
  }, [adminNotice])

  useEffect(() => {
    if (!adminDialogOpen) return

    const handleDialogKeyDown = (event: KeyboardEvent) => {
      if (event.key === 'Escape' && !adminVerifying) {
        setAdminDialogOpen(false)
        setAdminPassword('')
        setAdminError('')
        window.requestAnimationFrame(() => adminTriggerRef.current?.focus())
        return
      }

      if (event.key === 'Tab') {
        const focusable = Array.from(adminDialogRef.current?.querySelectorAll<HTMLElement>(
          'button:not([disabled]), input:not([disabled]), [href], [tabindex]:not([tabindex="-1"])',
        ) ?? [])
        if (focusable.length === 0) return
        const first = focusable[0]
        const last = focusable[focusable.length - 1]
        if (event.shiftKey && document.activeElement === first) {
          event.preventDefault()
          last.focus()
        } else if (!event.shiftKey && document.activeElement === last) {
          event.preventDefault()
          first.focus()
        }
      }
    }
    window.addEventListener('keydown', handleDialogKeyDown)
    return () => window.removeEventListener('keydown', handleDialogKeyDown)
  }, [adminDialogOpen, adminVerifying])

  const openAdminDialog = () => {
    setAdminPassword('')
    setAdminError('')
    setAdminNotice('')
    setAdminDialogOpen(true)
  }

  const closeAdminDialog = () => {
    if (adminVerifying) return
    setAdminDialogOpen(false)
    setAdminPassword('')
    setAdminError('')
    window.requestAnimationFrame(() => adminTriggerRef.current?.focus())
  }

  const unlockAdminMode = async (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault()
    if (!isAdmin || adminVerifying) return

    setAdminError('')
    setAdminVerifying(true)
    try {
      const { data: currentData, error: currentError } = await supabase.auth.getUser()
      const currentUser = currentData.user
      if (currentError || !currentUser?.id || !currentUser.email) {
        throw new Error('current-user-unavailable')
      }

      const { data: verifiedData, error: verifiedError } = await supabase.auth.signInWithPassword({
        email: currentUser.email,
        password: adminPassword,
      })
      if (verifiedError || verifiedData.user?.id !== currentUser.id) {
        throw new Error('password-verification-failed')
      }

      const unlockResponse = await authFetch('/api/admin/unlock', {
        method: 'POST',
        cache: 'no-store',
      })
      if (unlockResponse.status === 401 || unlockResponse.status === 403) {
        void refreshAuthorization()
      }
      const unlockPayload: unknown = await unlockResponse.json().catch(() => null)
      if (
        !unlockResponse.ok
        || typeof unlockPayload !== 'object'
        || unlockPayload === null
        || Array.isArray(unlockPayload)
        || (unlockPayload as Record<string, unknown>).version !== 1
        || (unlockPayload as Record<string, unknown>).unlocked !== true
        || typeof (unlockPayload as Record<string, unknown>).expires_at !== 'string'
      ) {
        throw new Error('admin-unlock-rejected')
      }

      const expiresAt = readUnlockExpiry(unlockPayload)
      if (expiresAt === null) {
        throw new Error('admin-unlock-expired')
      }

      setAdminPassword('')
      setAdminDialogOpen(false)
      setAdminModeExpiresAt(expiresAt)
      setAdminModeUserId(currentUser.id)
      setAdminMode(true)
      window.requestAnimationFrame(() => adminTriggerRef.current?.focus())
    } catch {
      setAdminPassword('')
      setAdminError('パスワードを確認できませんでした。もう一度入力してください。')
    } finally {
      setAdminVerifying(false)
    }
  }

  return (
    <div className="min-h-screen bg-[#0a0a0a] text-white">
      {/* Header */}
      <header className="flex flex-wrap items-center justify-between gap-3 border-b border-[#1e1e1e] px-4 py-4 sm:px-6">
        <Logo href="/home" />
        <div className="flex flex-wrap items-center justify-end gap-x-3 gap-y-2">
          {isAdmin && (
            <button
              ref={adminTriggerRef}
              type="button"
              onClick={adminMode ? () => lockAdminMode() : openAdminDialog}
              className={adminMode
                ? 'rounded-full border border-[#28593a] bg-[#102417] px-3 py-1.5 text-xs text-[#4ade80] transition-colors hover:border-[#3c7a51]'
                : 'text-xs text-[#888] transition-colors hover:text-white'}
            >
              {adminMode ? 'ユーザー画面に戻る' : '管理者モード'}
            </button>
          )}
          <button
            onClick={async () => {
              lockAdminMode()
              await supabase.auth.signOut()
              router.replace('/login')
            }}
            className="text-xs text-[#888] hover:text-white transition-colors"
          >
            ログアウト
          </button>
        </div>
      </header>

      {adminNotice && (
        <div
          role="status"
          aria-live="polite"
          className="fixed right-4 top-20 z-40 max-w-sm rounded-lg border border-[#333] bg-[#111] px-4 py-3 text-sm text-[#ddd] shadow-xl"
        >
          {adminNotice}
        </div>
      )}

      {/* Main */}
      <main className={`${adminMode ? 'max-w-6xl' : 'max-w-2xl'} mx-auto px-6 py-16`}>
        {adminMode && isAdmin && adminModeUserId === userId ? (
          <AdminWorkspace onAuthorizationFailure={handleAdminAuthorizationFailure} />
        ) : (
          <>
        <h1 className="text-3xl font-bold mb-2">AI競馬予測</h1>
        <p className="text-[#888] mb-8 text-sm">機械学習による競馬予測・資金管理システム</p>

        {/* System status */}
        <div className="grid grid-cols-3 gap-3 mb-10">
          <div className="bg-[#111] border border-[#1e1e1e] rounded-lg p-4 flex flex-col gap-1">
            <div className="text-xs text-[#666]">API</div>
            <div className="flex items-center gap-1.5">
              {status.apiOnline === null
                ? <span className="text-[#444] text-sm">確認中…</span>
                : status.apiOnline
                  ? <><span className="w-2 h-2 rounded-full bg-green-500 shrink-0" /><span className="text-sm text-green-400">オンライン</span></>
                  : <><span className="w-2 h-2 rounded-full bg-red-500 shrink-0" /><span className="text-sm text-red-400">オフライン</span></>
              }
            </div>
          </div>
          <div className="bg-[#111] border border-[#1e1e1e] rounded-lg p-4 flex flex-col gap-1">
            <div className="text-xs text-[#666]">レース数</div>
            <div className="text-sm text-white font-medium">
              {status.totalRaces === null ? '…' : status.totalRaces.toLocaleString()}
            </div>
          </div>
          <div className="bg-[#111] border border-[#1e1e1e] rounded-lg p-4 flex flex-col gap-1">
            <div className="text-xs text-[#666]">モデル数</div>
            <div className="text-sm text-white font-medium">
              {status.totalModels === null ? '…' : status.totalModels}
            </div>
          </div>
        </div>

        {/* ── 2ステップ フロー ── */}
        <div className="mb-2">
          <p className="text-xs text-[#555] mb-4 tracking-wider uppercase">基本的な使い方 — 2ステップ</p>
          <div className="space-y-2">
            {FLOW_STEPS.map((item, idx) => (
              <div key={item.href}>
                <Link href={item.href}>
                  <div className="group flex items-center justify-between p-4 bg-[#111] border border-[#1e1e1e] rounded-lg hover:border-[#333] hover:bg-[#161616] transition-all cursor-pointer">
                    <div className="flex items-center gap-4">
                      <span className="text-xs text-[#4ade80] font-mono font-bold w-5 shrink-0">{item.step}</span>
                      <div>
                        <div className="font-medium text-white">{item.label}</div>
                        <div className="text-xs text-[#666] mt-0.5">{item.desc}</div>
                      </div>
                    </div>
                    <svg className="w-4 h-4 text-[#444] group-hover:text-[#888] transition-colors shrink-0" fill="none" viewBox="0 0 24 24" stroke="currentColor">
                      <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M9 5l7 7-7 7" />
                    </svg>
                  </div>
                </Link>
                {idx < FLOW_STEPS.length - 1 && (
                  <div className="flex items-center justify-start pl-[22px] py-0.5">
                    <svg className="w-3 h-3 text-[#333]" fill="none" viewBox="0 0 24 24" stroke="currentColor">
                      <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M19 9l-7 7-7-7" />
                    </svg>
                  </div>
                )}
              </div>
            ))}
          </div>
        </div>

        <div className="mt-6 pt-6 border-t border-[#1e1e1e]">
          <Link
            href="/predict-batch"
            className="flex items-center justify-center gap-2 w-full py-3 bg-white text-black text-sm font-medium rounded-lg hover:bg-[#eee] transition-colors"
          >
            予測を始める
            <svg className="w-4 h-4" fill="none" viewBox="0 0 24 24" stroke="currentColor">
              <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M9 5l7 7-7 7" />
            </svg>
          </Link>
        </div>
          </>
        )}
      </main>

      {adminDialogOpen && isAdmin && (
        <div
          className="fixed inset-0 z-50 flex items-center justify-center bg-black/75 px-4 backdrop-blur-sm"
          onMouseDown={event => {
            if (event.target === event.currentTarget) closeAdminDialog()
          }}
        >
          <div
            ref={adminDialogRef}
            role="dialog"
            aria-modal="true"
            aria-labelledby="admin-auth-title"
            aria-describedby="admin-auth-description"
            className="w-full max-w-sm rounded-xl border border-[#2a2a2a] bg-[#111] p-6 shadow-2xl"
          >
            <div className="mb-5">
              <div className="mb-3 flex h-9 w-9 items-center justify-center rounded-full border border-[#28593a] bg-[#102417] text-sm font-bold text-[#4ade80]">
                A
              </div>
              <h2 id="admin-auth-title" className="text-lg font-semibold text-white">管理者パスワードの確認</h2>
              <p id="admin-auth-description" className="mt-2 text-sm leading-6 text-[#888]">
                管理機能を表示するため、現在のアカウントのパスワードを入力してください。
              </p>
            </div>

            <form onSubmit={unlockAdminMode}>
              <label htmlFor="admin-password" className="mb-1.5 block text-xs text-[#888]">管理者パスワード</label>
              <input
                id="admin-password"
                type="password"
                autoComplete="current-password"
                autoFocus
                required
                maxLength={256}
                value={adminPassword}
                onChange={event => setAdminPassword(event.target.value)}
                disabled={adminVerifying}
                className="w-full rounded-lg border border-[#333] bg-[#0a0a0a] px-3 py-2.5 text-sm text-white outline-none transition-colors focus:border-[#666] disabled:opacity-50"
              />

              {adminError && <p role="alert" className="mt-3 text-sm text-[#f87171]">{adminError}</p>}

              <div className="mt-6 flex gap-3">
                <button
                  type="button"
                  onClick={closeAdminDialog}
                  disabled={adminVerifying}
                  className="flex-1 rounded-lg border border-[#333] px-4 py-2.5 text-sm text-[#aaa] transition-colors hover:border-[#555] hover:text-white disabled:opacity-50"
                >
                  キャンセル
                </button>
                <button
                  type="submit"
                  disabled={adminVerifying || adminPassword.length === 0}
                  className="flex-1 rounded-lg bg-white px-4 py-2.5 text-sm font-medium text-black transition-colors hover:bg-[#eee] disabled:cursor-not-allowed disabled:opacity-50"
                >
                  {adminVerifying ? '確認中…' : '確認して切り替える'}
                </button>
              </div>
            </form>
          </div>
        </div>
      )}
    </div>
  )
}
