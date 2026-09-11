'use client'

import Link from 'next/link'
import { useCallback, useEffect, useState } from 'react'
import { authFetch } from '@/lib/auth-fetch'

interface UserProfile {
  id: string
  email: string
  role: 'admin' | 'user'
  full_name: string | null
  subscription_tier: 'free' | 'premium'
  created_at: string
}

interface AdminStats {
  totalUsers: number | null
  adminUsers: number | null
  premiumUsers: number | null
  totalRaces: number | null
  totalModels: number | null
}

const EMPTY_STATS: AdminStats = {
  totalUsers: null,
  adminUsers: null,
  premiumUsers: null,
  totalRaces: null,
  totalModels: null,
}

const ADMIN_TOOLS = [
  {
    href: '/data-collection',
    code: 'A1',
    label: 'データ取得',
    description: 'ネットケイバからレース情報を取得',
  },
  {
    href: '/train',
    code: 'A2',
    label: 'モデル管理',
    description: '保存済みモデルと学習機能の状態を確認',
    badge: '学習実行は準備中',
  },
  {
    href: '/production-readiness',
    code: 'A3',
    label: '本番前チェック',
    description: 'build・health・smoke・feature flag を確認',
  },
] as const

function readSafeDetail(value: unknown, fallback: string): string {
  if (typeof value !== 'object' || value === null || Array.isArray(value)) return fallback
  const detail = (value as Record<string, unknown>).detail
  return typeof detail === 'string'
    && detail.length > 0
    && detail.length <= 200
    && !/[\u0000-\u001f\u007f]/.test(detail)
    ? detail
    : fallback
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

function readCount(value: unknown): number | null {
  return typeof value === 'number' && Number.isSafeInteger(value) && value >= 0 ? value : null
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

type AdminWorkspaceProps = {
  onAuthorizationFailure: () => void
}

export function AdminWorkspace({ onAuthorizationFailure }: AdminWorkspaceProps) {
  const [loading, setLoading] = useState(true)
  const [users, setUsers] = useState<UserProfile[]>([])
  const [stats, setStats] = useState<AdminStats>(EMPTY_STATS)
  const [roleMessage, setRoleMessage] = useState('')
  const [dataError, setDataError] = useState('')
  const [updatingUserId, setUpdatingUserId] = useState<string | null>(null)

  const loadData = useCallback(async () => {
    setLoading(true)
    setDataError('')

    try {
      const [usersResponse, dataStatsResponse] = await Promise.all([
        authFetch('/api/admin/profiles', { method: 'GET', cache: 'no-store' }),
        authFetch('/api/data-stats', { cache: 'no-store' }),
      ])

      if (usersResponse.status === 401 || usersResponse.status === 403) {
        onAuthorizationFailure()
        return
      }

      const usersPayload = await readJsonRecord(usersResponse)
      if (!usersResponse.ok) {
        throw new Error(readSafeDetail(usersPayload, 'ユーザー一覧の取得に失敗しました'))
      }

      const profiles = usersPayload?.profiles
      if (usersPayload?.version !== 1 || !Array.isArray(profiles) || !profiles.every(isUserProfile)) {
        throw new Error('ユーザー一覧の応答形式を確認できません')
      }

      const dataStats = dataStatsResponse.ok ? await readJsonRecord(dataStatsResponse) : null
      setUsers(profiles)
      setStats({
        totalUsers: profiles.length,
        adminUsers: profiles.filter(user => user.role === 'admin').length,
        premiumUsers: profiles.filter(user => user.subscription_tier === 'premium').length,
        totalRaces: readCount(dataStats?.total_races),
        totalModels: readCount(dataStats?.total_models),
      })
    } catch (error) {
      setUsers([])
      setStats(EMPTY_STATS)
      setDataError(error instanceof Error ? error.message : 'データ取得に失敗しました')
    } finally {
      setLoading(false)
    }
  }, [onAuthorizationFailure])

  useEffect(() => {
    void loadData()
  }, [loadData])

  const handleRoleChange = async (userId: string, newRole: string) => {
    if (newRole !== 'admin' && newRole !== 'user') {
      setRoleMessage('許可されていないロールです')
      return
    }

    setUpdatingUserId(userId)
    try {
      const response = await authFetch(`/api/admin/profiles/${encodeURIComponent(userId)}/role`, {
        method: 'PATCH',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ role: newRole }),
      })
      if (response.status === 401 || response.status === 403) {
        onAuthorizationFailure()
        return
      }

      const payload = await readJsonRecord(response)
      if (!response.ok) throw new Error(readSafeDetail(payload, 'ロール変更に失敗しました'))

      const profile = payload?.profile
      if (
        payload?.version !== 1
        || typeof profile !== 'object'
        || profile === null
        || Array.isArray(profile)
        || (profile as Record<string, unknown>).id !== userId
        || (profile as Record<string, unknown>).role !== newRole
      ) {
        throw new Error('ロール変更の応答形式を確認できません')
      }

      setRoleMessage(`ユーザーのロールを ${newRole === 'admin' ? '管理者' : 'ユーザー'} に変更しました`)
      await loadData()
    } catch (error) {
      setRoleMessage(error instanceof Error ? error.message : 'ロール変更に失敗しました')
    } finally {
      setUpdatingUserId(null)
      window.setTimeout(() => setRoleMessage(''), 3000)
    }
  }

  return (
    <section aria-labelledby="admin-workspace-title">
      {roleMessage && (
        <div
          role="status"
          className="fixed right-4 top-4 z-50 rounded-lg border border-[#2f513a] bg-[#111] px-4 py-2 text-sm text-white shadow-xl"
        >
          {roleMessage}
        </div>
      )}

      <div className="mb-8">
        <div className="mb-3 flex items-center gap-2">
          <span className="rounded-full border border-[#28593a] bg-[#102417] px-2.5 py-1 text-[10px] font-semibold tracking-wider text-[#4ade80]">
            管理者確認済み
          </span>
        </div>
        <h1 id="admin-workspace-title" className="text-3xl font-bold text-white">管理者モード</h1>
        <p className="mt-2 text-sm text-[#888]">運用機能とユーザー権限を、このホーム画面から管理します</p>
      </div>

      {dataError && (
        <div role="alert" className="mb-6 rounded-lg border border-[#5a2424] bg-[#211010] px-4 py-3 text-sm text-[#fca5a5]">
          {dataError}
        </div>
      )}

      <div className="mb-8 grid grid-cols-2 gap-3 md:grid-cols-5">
        <StatCard label="表示ユーザー数" value={stats.totalUsers} loading={loading} />
        <StatCard label="管理者" value={stats.adminUsers} loading={loading} />
        <StatCard label="プレミアム" value={stats.premiumUsers} loading={loading} />
        <StatCard label="レース数" value={stats.totalRaces} loading={loading} />
        <StatCard label="モデル数" value={stats.totalModels} loading={loading} />
      </div>

      <div className="mb-10">
        <p className="mb-4 text-xs uppercase tracking-wider text-[#555]">管理機能</p>
        <div className="grid gap-3 md:grid-cols-3">
          {ADMIN_TOOLS.map(tool => (
            <Link
              key={tool.href}
              href={tool.href}
              className="group rounded-lg border border-[#1e1e1e] bg-[#111] p-5 transition-all hover:border-[#333] hover:bg-[#161616]"
            >
              <div className="mb-4 flex items-center justify-between gap-3">
                <span className="font-mono text-xs font-bold text-[#4ade80]">{tool.code}</span>
                {'badge' in tool && (
                  <span className="rounded-full border border-[#493b19] bg-[#211b0d] px-2 py-0.5 text-[10px] text-[#fbbf24]">
                    {tool.badge}
                  </span>
                )}
              </div>
              <div className="flex items-center justify-between gap-4">
                <div>
                  <h2 className="font-medium text-white">{tool.label}</h2>
                  <p className="mt-1 text-xs text-[#666]">{tool.description}</p>
                </div>
                <svg className="h-4 w-4 shrink-0 text-[#444] transition-colors group-hover:text-[#888]" fill="none" viewBox="0 0 24 24" stroke="currentColor" aria-hidden="true">
                  <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M9 5l7 7-7 7" />
                </svg>
              </div>
            </Link>
          ))}
        </div>
      </div>

      <div className="overflow-hidden rounded-lg border border-[#1e1e1e] bg-[#111]">
        <div className="border-b border-[#1e1e1e] px-5 py-4">
          <h2 className="font-medium text-white">ユーザー管理</h2>
          <p className="mt-1 text-xs text-[#666]">登録ユーザーの権限とプランを確認します</p>
        </div>

        {loading ? (
          <div role="status" className="px-5 py-10 text-center text-sm text-[#888]">管理情報を読み込んでいます…</div>
        ) : users.length === 0 && !dataError ? (
          <div className="px-5 py-10 text-center text-sm text-[#888]">登録ユーザーはいません</div>
        ) : (
          <div className="overflow-x-auto">
            <table className="w-full min-w-[760px] text-left text-sm">
              <thead>
                <tr className="border-b border-[#1e1e1e] text-xs text-[#666]">
                  <th className="px-5 py-3 font-medium">メールアドレス</th>
                  <th className="px-4 py-3 font-medium">名前</th>
                  <th className="px-4 py-3 font-medium">ロール</th>
                  <th className="px-4 py-3 font-medium">プラン</th>
                  <th className="px-4 py-3 font-medium">登録日</th>
                  <th className="px-5 py-3 font-medium">操作</th>
                </tr>
              </thead>
              <tbody>
                {users.map(user => (
                  <tr key={user.id} className="border-b border-[#1e1e1e] last:border-b-0 hover:bg-[#151515]">
                    <td className="px-5 py-3 text-white">{user.email}</td>
                    <td className="px-4 py-3 text-[#aaa]">{user.full_name || '-'}</td>
                    <td className="px-4 py-3">
                      <span className={user.role === 'admin'
                        ? 'rounded-full border border-[#493b19] bg-[#211b0d] px-2.5 py-1 text-xs text-[#fbbf24]'
                        : 'rounded-full border border-[#243447] bg-[#101923] px-2.5 py-1 text-xs text-[#93c5fd]'}
                      >
                        {user.role === 'admin' ? '管理者' : 'ユーザー'}
                      </span>
                    </td>
                    <td className="px-4 py-3 text-[#888]">{user.subscription_tier === 'premium' ? 'Premium' : 'Free'}</td>
                    <td className="px-4 py-3 text-[#888]">{new Date(user.created_at).toLocaleDateString('ja-JP')}</td>
                    <td className="px-5 py-3">
                      <select
                        value={user.role}
                        onChange={event => handleRoleChange(user.id, event.target.value)}
                        disabled={updatingUserId === user.id}
                        aria-label={`${user.email} のロール`}
                        className="rounded border border-[#333] bg-[#181818] px-2 py-1 text-xs text-white outline-none focus:border-[#666] disabled:cursor-wait disabled:opacity-50"
                      >
                        <option value="user">ユーザー</option>
                        <option value="admin">管理者</option>
                      </select>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </div>
    </section>
  )
}

function StatCard({ label, value, loading }: { label: string; value: number | null; loading: boolean }) {
  return (
    <div className="rounded-lg border border-[#1e1e1e] bg-[#111] p-4">
      <div className="text-xs text-[#666]">{label}</div>
      <div className="mt-1 text-lg font-semibold text-white">
        {value === null ? (loading ? '…' : '—') : value.toLocaleString()}
      </div>
    </div>
  )
}
