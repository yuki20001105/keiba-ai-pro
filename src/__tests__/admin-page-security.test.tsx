import { render, screen } from '@testing-library/react'
import { readFileSync } from 'node:fs'
import { beforeEach, describe, expect, test, vi } from 'vitest'

const mocks = vi.hoisted(() => ({
  authFetch: vi.fn(),
  refreshAuthorization: vi.fn(),
}))

vi.mock('@/lib/auth-fetch', () => ({ authFetch: mocks.authFetch }))
vi.mock('@/contexts/AuthContext', () => ({
  useAuth: () => ({ refreshAuthorization: mocks.refreshAuthorization }),
}))
vi.mock('@/components/Logo', () => ({ Logo: () => <div>競馬AI Pro</div> }))

const profile = {
  id: 'bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb',
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

describe('Read-only user management', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    mocks.authFetch.mockImplementation(async (input: RequestInfo | URL, init?: RequestInit) => {
      if (String(input) === '/api/admin/profiles' && init?.method === 'GET') {
        return jsonResponse({ version: 1, profiles: [profile] })
      }
      throw new Error(`unexpected request: ${String(input)}`)
    })
  })

  test('loads the safe profile projection without a role-changing control', async () => {
    const { default: UserManagementPage } = await import('@/app/user-management/page')
    render(<UserManagementPage />)

    expect(await screen.findByText('user@example.com')).toBeInTheDocument()
    expect(screen.getByRole('heading', { name: 'ユーザー管理' })).toBeInTheDocument()
    expect(screen.getByText('Test User')).toBeInTheDocument()
    expect(screen.queryByRole('combobox')).not.toBeInTheDocument()
    expect(screen.queryByText('操作')).not.toBeInTheDocument()
    expect(mocks.authFetch).toHaveBeenCalledTimes(1)
    expect(mocks.authFetch).toHaveBeenCalledWith('/api/admin/profiles', {
      method: 'GET',
      cache: 'no-store',
    })
  })

  test('refreshes authorization and fails closed when the server rejects the Admin role', async () => {
    mocks.authFetch.mockResolvedValueOnce(jsonResponse({ detail: 'Admin role required' }, 403))
    const { default: UserManagementPage } = await import('@/app/user-management/page')
    render(<UserManagementPage />)

    expect(await screen.findByRole('alert')).toHaveTextContent('ユーザー管理を表示する権限を確認できませんでした')
    expect(mocks.refreshAuthorization).toHaveBeenCalledTimes(1)
    expect(screen.queryByText('user@example.com')).not.toBeInTheDocument()
  })

  test('contains no browser-side profile update or service-role access', () => {
    const source = readFileSync('src/app/user-management/page.tsx', 'utf8')
    expect(source).not.toMatch(/@\/lib\/supabase/)
    expect(source).not.toMatch(/supabase\s*\.\s*from\s*\(/)
    expect(source).not.toContain(".from('profiles')")
    expect(source).not.toContain('SUPABASE_SERVICE_ROLE_KEY')
    expect(source).toContain("authFetch('/api/admin/profiles'")
    expect(source).not.toContain('/role')
    expect(source).not.toContain('<select')

    const legacyRoute = readFileSync('src/app/admin/page.tsx', 'utf8')
    expect(legacyRoute).toContain("redirect('/home')")
  })
})
