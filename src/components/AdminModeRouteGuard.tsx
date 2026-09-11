'use client'

import { useEffect, useState, type ReactNode } from 'react'
import { useRouter } from 'next/navigation'
import { Logo } from '@/components/Logo'
import { useAuth } from '@/contexts/AuthContext'
import { authFetch } from '@/lib/auth-fetch'

function readExpiry(value: unknown): number | null {
  if (typeof value !== 'object' || value === null || Array.isArray(value)) return null
  const record = value as Record<string, unknown>
  if (record.version !== 1 || record.unlocked !== true || typeof record.expires_at !== 'string') return null
  const expiresAt = Date.parse(record.expires_at)
  return Number.isFinite(expiresAt) && expiresAt > Date.now() ? expiresAt : null
}

export function AdminModeRouteGuard({ children }: { children: ReactNode }) {
  const router = useRouter()
  const { userId, isAdmin, loading, refreshAuthorization } = useAuth()
  const [allowedUserId, setAllowedUserId] = useState<string | null>(null)

  useEffect(() => {
    if (loading) return
    if (!isAdmin || !userId) {
      setAllowedUserId(null)
      router.replace('/home')
      return
    }

    let active = true
    let expiryTimer: number | null = null

    const deny = () => {
      if (!active) return
      setAllowedUserId(null)
      void refreshAuthorization()
      router.replace('/home')
    }

    const verify = async () => {
      try {
        const response = await authFetch('/api/admin/unlock', {
          method: 'GET',
          cache: 'no-store',
        })
        const payload: unknown = await response.json().catch(() => null)
        const expiresAt = response.ok ? readExpiry(payload) : null
        if (!active || expiresAt === null) {
          deny()
          return
        }

        setAllowedUserId(userId)
        if (expiryTimer !== null) window.clearTimeout(expiryTimer)
        expiryTimer = window.setTimeout(deny, Math.max(0, expiresAt - Date.now()))
      } catch {
        deny()
      }
    }

    void verify()
    const pollTimer = window.setInterval(() => { void verify() }, 30_000)
    return () => {
      active = false
      window.clearInterval(pollTimer)
      if (expiryTimer !== null) window.clearTimeout(expiryTimer)
    }
  }, [isAdmin, loading, refreshAuthorization, router, userId])

  if (loading || !isAdmin || !userId || allowedUserId !== userId) {
    return (
      <div className="min-h-screen bg-[#0a0a0a] text-white">
        <header className="border-b border-[#1e1e1e] px-4 py-4 sm:px-6">
          <Logo href="/home" />
        </header>
        <main className="mx-auto max-w-2xl px-6 py-16">
          <p role="status" aria-live="polite" className="text-sm text-[#888]">
            管理者モードを確認しています…
          </p>
        </main>
      </div>
    )
  }

  return children
}
