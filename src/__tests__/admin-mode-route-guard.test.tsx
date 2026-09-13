import { act, fireEvent, render, screen, waitFor } from '@testing-library/react'
import { afterEach, beforeEach, describe, expect, test, vi } from 'vitest'
import { useEffect } from 'react'

const mocks = vi.hoisted(() => ({
  authState: {
    userId: 'admin-user-id' as string | null,
    isAdmin: true,
    loading: false,
    authorizationUnavailable: false,
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
    mocks.authState.authorizationUnavailable = false
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
  afterEach(() => vi.useRealTimers())

  test('renders protected content when a valid short-lived grant already exists', async () => {
    mocks.authFetch.mockResolvedValue(response(validGrant()))
    const { AdminActionRouteGuard } = await import('@/components/AdminActionRouteGuard')
    render(<AdminActionRouteGuard><div>管理ツール本体</div></AdminActionRouteGuard>)

    expect(await screen.findByText('管理ツール本体')).toBeInTheDocument()
    expect(mocks.authFetch).toHaveBeenCalledWith('/api/admin/unlock', {
      method: 'GET',
      cache: 'no-store',
      signal: expect.any(AbortSignal),
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

  test('local session remains open beyond fifteen minutes without a password', async () => {
    vi.useFakeTimers()
    mocks.authFetch.mockImplementation(async () => response({ version: 2, unlocked: true, mode: 'local-session', expires_at: null }))
    const { AdminActionRouteGuard } = await import('@/components/AdminActionRouteGuard')
    await act(async () => { render(<AdminActionRouteGuard><div>管理ツール本体</div></AdminActionRouteGuard>) })
    expect(screen.getByText('管理ツール本体')).toBeInTheDocument()
    await act(async () => { await vi.advanceTimersByTimeAsync(16 * 60 * 1000) })
    expect(screen.getByText('管理ツール本体')).toBeInTheDocument()
    expect(mocks.signInWithPassword).not.toHaveBeenCalled()
    expect(mocks.authFetch.mock.calls.length).toBeGreaterThan(30)
  })

  test('remote step-up still expires after its server-issued deadline', async () => {
    vi.useFakeTimers()
    const grant = validGrant()
    mocks.authFetch.mockImplementation(async () => response(grant))
    const { AdminActionRouteGuard } = await import('@/components/AdminActionRouteGuard')
    await act(async () => { render(<AdminActionRouteGuard><div>管理ツール本体</div></AdminActionRouteGuard>) })
    await act(async () => { await vi.advanceTimersByTimeAsync(10 * 60 * 1000 + 1) })
    expect(screen.queryByText('管理ツール本体')).not.toBeInTheDocument()
    expect(screen.getByLabelText('パスワード')).toBeInTheDocument()
  })

  test('a temporary status failure keeps the same child mounted but makes it inert until recovery', async () => {
    vi.useFakeTimers()
    const mounted = vi.fn()
    const unmounted = vi.fn()
    function Tool() {
      useEffect(() => { mounted(); return unmounted }, [])
      return <button>取得開始</button>
    }
    mocks.authFetch.mockImplementation(async () => response({ version: 2, unlocked: true, mode: 'local-session', expires_at: null }))
    const { AdminActionRouteGuard } = await import('@/components/AdminActionRouteGuard')
    await act(async () => { render(<AdminActionRouteGuard><Tool /></AdminActionRouteGuard>) })
    mocks.authFetch.mockResolvedValueOnce(response({ detail: 'unavailable' }, 503))
    await act(async () => { await vi.advanceTimersByTimeAsync(30_000) })
    expect(mounted).toHaveBeenCalledOnce()
    expect(unmounted).not.toHaveBeenCalled()
    expect(screen.getByRole('button', { name: '取得開始' }).parentElement).toHaveAttribute('inert')
    expect(screen.getByRole('status')).toHaveTextContent('接続を再確認中')
    await act(async () => { await vi.advanceTimersByTimeAsync(30_000) })
    expect(screen.getByRole('button', { name: '取得開始' }).parentElement).not.toHaveAttribute('inert')
    expect(unmounted).not.toHaveBeenCalled()
  })

  test.each([401, 403])('confirmed denial %s unmounts the protected content', async status => {
    vi.useFakeTimers()
    mocks.authFetch.mockImplementation(async () => response({ version: 2, unlocked: true, mode: 'local-session', expires_at: null }))
    const { AdminActionRouteGuard } = await import('@/components/AdminActionRouteGuard')
    await act(async () => { render(<AdminActionRouteGuard><div>管理ツール本体</div></AdminActionRouteGuard>) })
    mocks.authFetch.mockResolvedValueOnce(response({ detail: 'denied' }, status))
    await act(async () => { await vi.advanceTimersByTimeAsync(30_000) })
    expect(screen.queryByText('管理ツール本体')).not.toBeInTheDocument()
    expect(mocks.authState.refreshAuthorization).toHaveBeenCalledOnce()
  })

  test('logout clears the local screen immediately', async () => {
    mocks.authFetch.mockImplementation(async () => response({ version: 2, unlocked: true, mode: 'local-session', expires_at: null }))
    const { AdminActionRouteGuard } = await import('@/components/AdminActionRouteGuard')
    const { rerender } = render(<AdminActionRouteGuard><div>管理ツール本体</div></AdminActionRouteGuard>)
    expect(await screen.findByText('管理ツール本体')).toBeInTheDocument()
    mocks.authState.userId = null
    mocks.authState.isAdmin = false
    rerender(<AdminActionRouteGuard><div>管理ツール本体</div></AdminActionRouteGuard>)
    expect(screen.queryByText('管理ツール本体')).not.toBeInTheDocument()
  })
})
