import { fireEvent, render, screen, waitFor } from '@testing-library/react'
import { readFileSync } from 'node:fs'
import { beforeEach, describe, expect, test, vi } from 'vitest'

const authFetchMock = vi.fn()

vi.mock('@/lib/auth-fetch', () => ({ authFetch: authFetchMock }))

const TARGET_ID = 'bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb'
const profile = {
  id: TARGET_ID,
  email: 'user@example.com',
  role: 'user',
  full_name: 'Test User',
  subscription_tier: 'free',
  created_at: '2026-07-20T00:00:00.000Z',
}

function jsonResponse(value: unknown, status = 200): Response {
  return new Response(JSON.stringify(value), {
    status,
    headers: { 'Content-Type': 'application/json' },
  })
}

describe('Integrated admin workspace server-bound profile access', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    authFetchMock.mockImplementation(async (input: RequestInfo | URL, init?: RequestInit) => {
      const url = String(input)
      if (url === '/api/admin/profiles' && init?.method === 'GET') {
        return jsonResponse({ version: 1, profiles: [profile] })
      }
      if (url === '/api/data-stats') return jsonResponse({ total_races: 12, total_models: 3 })
      if (url === `/api/admin/profiles/${TARGET_ID}/role` && init?.method === 'PATCH') {
        return jsonResponse({ version: 1, profile: { id: TARGET_ID, role: 'admin' } })
      }
      throw new Error(`unexpected request: ${url}`)
    })
  })

  test('loads and updates profiles only through authFetch Admin routes', async () => {
    const { AdminWorkspace } = await import('@/components/AdminWorkspace')
    render(<AdminWorkspace onAuthorizationFailure={vi.fn()} />)

    expect(await screen.findByText('user@example.com')).toBeInTheDocument()
    expect(screen.getByRole('link', { name: /データ取得/ })).toHaveAttribute('href', '/data-collection')
    expect(screen.getByRole('link', { name: /モデル管理/ })).toHaveAttribute('href', '/train')
    expect(screen.getByRole('link', { name: /本番前チェック/ })).toHaveAttribute('href', '/production-readiness')
    expect(authFetchMock).toHaveBeenCalledWith('/api/admin/profiles', {
      method: 'GET',
      cache: 'no-store',
    })

    fireEvent.change(screen.getByLabelText('user@example.com のロール'), { target: { value: 'admin' } })
    await waitFor(() => {
      expect(authFetchMock).toHaveBeenCalledWith(`/api/admin/profiles/${TARGET_ID}/role`, {
        method: 'PATCH',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ role: 'admin' }),
      })
    })
  })

  test('locks immediately when the server rejects the Admin role', async () => {
    authFetchMock.mockImplementationOnce(async () => jsonResponse({ detail: 'Admin role required' }, 403))
    const onAuthorizationFailure = vi.fn()
    const { AdminWorkspace } = await import('@/components/AdminWorkspace')
    render(<AdminWorkspace onAuthorizationFailure={onAuthorizationFailure} />)

    await waitFor(() => expect(onAuthorizationFailure).toHaveBeenCalledTimes(1))
    expect(screen.queryByText('user@example.com')).not.toBeInTheDocument()
  })

  test('contains no browser-side profiles select/update or service-role access', () => {
    const source = readFileSync('src/components/AdminWorkspace.tsx', 'utf8')
    expect(source).not.toMatch(/@\/lib\/supabase/)
    expect(source).not.toMatch(/supabase\s*\.\s*from\s*\(/)
    expect(source).not.toContain(".from('profiles')")
    expect(source).not.toContain('SUPABASE_SERVICE_ROLE_KEY')
    expect(source).toContain("authFetch('/api/admin/profiles'")

    const legacyRoute = readFileSync('src/app/admin/page.tsx', 'utf8')
    expect(legacyRoute).toContain("redirect('/home')")
    expect(legacyRoute).not.toContain('AdminWorkspace')
  })
})
