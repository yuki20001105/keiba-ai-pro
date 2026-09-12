'use client'

import { useCallback, useEffect, useState } from 'react'
import Link from 'next/link'
import { Logo } from '@/components/Logo'
import { useAuth } from '@/contexts/AuthContext'
import { authFetch } from '@/lib/auth-fetch'

interface UserProfile {
  id: string
  email: string
  role: 'admin' | 'user'
  full_name: string | null
  subscription_tier: 'free' | 'premium'
  created_at: string
}

function isUserProfile(value: unknown): value is UserProfile {
  if (typeof value !== 'object' || value === null || Array.isArray(value)) return false
  const record = value as Record<string, unknown>
  return typeof record.id === 'string'
    && typeof record.email === 'string'
    && (record.role === 'admin' || record.role === 'user')
    && (record.full_name === null || typeof record.full_name === 'string')
    && (record.subscription_tier === 'free' || record.subscription_tier === 'premium')
    && typeof record.created_at === 'string'
}

function readSafeDetail(value: unknown, fallback: string): string {
  if (typeof value !== 'object' || value === null || Array.isArray(value)) return fallback
  const detail = (value as Record<string, unknown>).detail
  return typeof detail === 'string' && detail.length <= 200 ? detail : fallback
}

async function readJsonRecord(response: Response): Promise<Record<string, unknown> | null> {
  try {
    const value: unknown = await response.json()
    return typeof value === 'object' && value !== null && !Array.isArray(value)
      ? value as Record<string, unknown>
      : null
  } catch {
    return null
  }
}

export default function UserManagementPage() {
  const { refreshAuthorization } = useAuth()
  const [users, setUsers] = useState<UserProfile[]>([])
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState('')

  const loadUsers = useCallback(async () => {
    setLoading(true)
    setError('')

    try {
      const response = await authFetch('/api/admin/profiles', { method: 'GET', cache: 'no-store' })
      const payload = await readJsonRecord(response)

      if (response.status === 401 || response.status === 403) {
        await refreshAuthorization()
        throw new Error('ユーザー管理を表示する権限を確認できませんでした')
      }
      if (!response.ok) {
        throw new Error(readSafeDetail(payload, 'ユーザー一覧の取得に失敗しました'))
      }

      const profiles = payload?.profiles
      if (payload?.version !== 1 || !Array.isArray(profiles) || !profiles.every(isUserProfile)) {
        throw new Error('ユーザー一覧の応答形式を確認できません')
      }

      setUsers(profiles)
    } catch (caught) {
      setUsers([])
      setError(caught instanceof Error ? caught.message : 'ユーザー一覧の取得に失敗しました')
    } finally {
      setLoading(false)
    }
  }, [refreshAuthorization])

  useEffect(() => {
    void loadUsers()
  }, [loadUsers])

  return (
    <div className="min-h-screen bg-[#0a0a0a] text-white">
      <header className="flex items-center justify-between border-b border-[#1e1e1e] px-4 py-4 sm:px-6">
        <Logo href="/home" />
        <Link href="/home" className="text-xs text-[#888] transition-colors hover:text-white">
          ← ホーム
        </Link>
      </header>

      <main className="mx-auto max-w-6xl px-6 py-16">
        <div className="mb-8">
          <div className="mb-3 font-mono text-xs font-bold text-[#4ade80]">05</div>
          <h1 className="text-3xl font-bold">ユーザー管理</h1>
          <p className="mt-2 text-sm text-[#888]">登録ユーザーの情報を確認します。権限の変更機能は現在表示していません。</p>
        </div>

        {error && (
          <div role="alert" className="mb-6 flex flex-wrap items-center justify-between gap-3 rounded-lg border border-[#5a2424] bg-[#211010] px-4 py-3 text-sm text-[#fca5a5]">
            <span>{error}</span>
            <button
              type="button"
              onClick={() => { void loadUsers() }}
              className="rounded border border-[#7f3333] px-3 py-1.5 text-xs text-white transition-colors hover:bg-[#321717]"
            >
              再読み込み
            </button>
          </div>
        )}

        <section aria-labelledby="user-list-title" className="overflow-hidden rounded-lg border border-[#1e1e1e] bg-[#111]">
          <div className="border-b border-[#1e1e1e] px-5 py-4">
            <h2 id="user-list-title" className="font-medium text-white">登録ユーザー</h2>
          </div>

          {loading ? (
            <div role="status" className="px-5 py-10 text-center text-sm text-[#888]">ユーザー情報を読み込んでいます…</div>
          ) : users.length === 0 && !error ? (
            <div className="px-5 py-10 text-center text-sm text-[#888]">登録ユーザーはいません</div>
          ) : (
            <div className="overflow-x-auto">
              <table className="w-full min-w-[680px] text-left text-sm">
                <thead>
                  <tr className="border-b border-[#1e1e1e] text-xs text-[#666]">
                    <th className="px-5 py-3 font-medium">メールアドレス</th>
                    <th className="px-4 py-3 font-medium">名前</th>
                    <th className="px-4 py-3 font-medium">ロール</th>
                    <th className="px-4 py-3 font-medium">プラン</th>
                    <th className="px-5 py-3 font-medium">登録日</th>
                  </tr>
                </thead>
                <tbody>
                  {users.map(user => (
                    <tr key={user.id} className="border-b border-[#1e1e1e] last:border-b-0 hover:bg-[#151515]">
                      <td className="px-5 py-3 text-white">{user.email}</td>
                      <td className="px-4 py-3 text-[#aaa]">{user.full_name || '-'}</td>
                      <td className="px-4 py-3 text-[#aaa]">{user.role === 'admin' ? '管理者' : 'ユーザー'}</td>
                      <td className="px-4 py-3 text-[#888]">{user.subscription_tier === 'premium' ? 'Premium' : 'Free'}</td>
                      <td className="px-5 py-3 text-[#888]">{new Date(user.created_at).toLocaleDateString('ja-JP')}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
        </section>
      </main>
    </div>
  )
}
