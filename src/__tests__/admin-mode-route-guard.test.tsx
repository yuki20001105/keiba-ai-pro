import { fireEvent, render, screen, waitFor } from '@testing-library/react'
import { beforeEach, describe, expect, test, vi } from 'vitest'

const mocks = vi.hoisted(() => ({
  authState: {
    userId: 'admin-user-id' as string | null,
    isAdmin: true,
    loading: false,
    refreshAuthorization: vi.fn(),
  },
  authFetch: vi.fn(),
  getUser: vi.fn(),
  signInWithPassword: vi.fn(),
  replace: vi.fn(),
  router: null as unknown as { replace: ReturnType<typeof vi.fn> },
}))

mocks.router = { replace: mocks.replace }

vi.mock('@/contexts/AuthContext', () => ({ useAuth: () => mocks.authState }))
vi.mock('@/lib/auth-fetch', () => ({ authFetch: mocks.authFetch }))
vi.mock('@/lib/supabase', () => ({
  supabase: {
    auth: {
      getUser: mocks.getUser,
      signInWithPassword: mocks.signInWithPassword,
    },
  },
}))
vi.mock('next/navigation', () => ({ useRouter: () => mocks.router }))
vi.mock('@/components/Logo', () => ({ Logo: () => <div>競馬AI Pro</div> }))

function response(value: unknown, status = 200): Response {
  return new Response(JSON.stringify(value), {
    status,
    headers: { 'Content-Type': 'application/json' },
  })
}

function validGrant() {
  return {
    version: 1,
    unlocked: true,
    expires_at: new Date(Date.now() + 10 * 60 * 1000).toISOString(),
  }
}

describe('AdminActionRouteGuard', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    mocks.authState.userId = 'admin-user-id'
    mocks.authState.isAdmin = true
    mocks.authState.loading = false
    mocks.getUser.mockResolvedValue({
      data: { user: { id: 'admin-user-id', email: 'admin@example.com' } },
      error: null,
    })
    mocks.signInWithPassword.mockResolvedValue({
      data: { user: { id: 'admin-user-id', email: 'admin@example.com' }, session: {} },
      error: null,
    })
    mocks.authFetch.mockImplementation(async (_input: RequestInfo | URL, init?: RequestInit) => {
      if (init?.method === 'POST') return response(validGrant())
      return response({ detail: 'Admin action is locked' }, 403)
    })
  })

  test('renders protected content when a valid short-lived grant already exists', async () => {
    mocks.authFetch.mockResolvedValue(response(validGrant()))
    const { AdminActionRouteGuard } = await import('@/components/AdminActionRouteGuard')
    render(<AdminActionRouteGuard><div>管理ツール本体</div></AdminActionRouteGuard>)

    expect(await screen.findByText('管理ツール本体')).toBeInTheDocument()
    expect(mocks.authFetch).toHaveBeenCalledWith('/api/admin/unlock', {
      method: 'GET',
      cache: 'no-store',
    })
  })

  test('asks for a password at the Admin action boundary without a Home mode switch', async () => {
    const { AdminActionRouteGuard } = await import('@/components/AdminActionRouteGuard')
    render(<AdminActionRouteGuard><div>管理ツール本体</div></AdminActionRouteGuard>)

    expect(await screen.findByRole('heading', { name: '管理機能を開く' })).toBeInTheDocument()
    expect(screen.getByLabelText('パスワード')).toHaveAttribute('type', 'password')
    expect(screen.queryByText('管理ツール本体')).not.toBeInTheDocument()
  })

  test('opens the requested Admin action after password and server verification', async () => {
    const { AdminActionRouteGuard } = await import('@/components/AdminActionRouteGuard')
    render(<AdminActionRouteGuard><div>管理ツール本体</div></AdminActionRouteGuard>)
    const password = await screen.findByLabelText('パスワード')

    fireEvent.change(password, { target: { value: 'verified-password' } })
    fireEvent.click(screen.getByRole('button', { name: '確認して開く' }))

    expect(await screen.findByText('管理ツール本体')).toBeInTheDocument()
    expect(mocks.signInWithPassword).toHaveBeenCalledWith({
      email: 'admin@example.com',
      password: 'verified-password',
    })
    expect(mocks.authFetch).toHaveBeenCalledWith('/api/admin/unlock', {
      method: 'POST',
      cache: 'no-store',
    })
  })

  test('keeps protected content unmounted after a failed password check', async () => {
    mocks.signInWithPassword.mockResolvedValue({
      data: { user: null, session: null },
      error: { message: 'Invalid login credentials' },
    })
    const { AdminActionRouteGuard } = await import('@/components/AdminActionRouteGuard')
    render(<AdminActionRouteGuard><div>管理ツール本体</div></AdminActionRouteGuard>)
    const password = await screen.findByLabelText('パスワード')

    fireEvent.change(password, { target: { value: 'wrong-password' } })
    fireEvent.click(screen.getByRole('button', { name: '確認して開く' }))

    expect(await screen.findByRole('alert')).toHaveTextContent('パスワードを確認できませんでした')
    expect(password).toHaveValue('')
    expect(screen.queryByText('管理ツール本体')).not.toBeInTheDocument()
  })

  test('redirects a standard user without mounting or checking the Admin grant', async () => {
    mocks.authState.isAdmin = false
    mocks.authState.userId = 'standard-user-id'
    const { AdminActionRouteGuard } = await import('@/components/AdminActionRouteGuard')
    render(<AdminActionRouteGuard><div>管理ツール本体</div></AdminActionRouteGuard>)

    await waitFor(() => expect(mocks.replace).toHaveBeenCalledWith('/home'))
    expect(mocks.authFetch).not.toHaveBeenCalled()
    expect(screen.queryByText('管理ツール本体')).not.toBeInTheDocument()
  })
})
