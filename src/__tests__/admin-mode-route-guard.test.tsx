import { render, screen, waitFor } from '@testing-library/react'
import { beforeEach, describe, expect, test, vi } from 'vitest'

const mocks = vi.hoisted(() => ({
  authState: {
    userId: 'admin-user-id' as string | null,
    isAdmin: true,
    loading: false,
    refreshAuthorization: vi.fn(),
  },
  authFetch: vi.fn(),
  replace: vi.fn(),
  router: null as unknown as { replace: ReturnType<typeof vi.fn> },
}))
mocks.router = { replace: mocks.replace }

vi.mock('@/contexts/AuthContext', () => ({ useAuth: () => mocks.authState }))
vi.mock('@/lib/auth-fetch', () => ({ authFetch: mocks.authFetch }))
vi.mock('next/navigation', () => ({ useRouter: () => mocks.router }))
vi.mock('@/components/Logo', () => ({ Logo: () => <div>競馬AI Pro</div> }))

function response(value: unknown, status = 200): Response {
  return new Response(JSON.stringify(value), {
    status,
    headers: { 'Content-Type': 'application/json' },
  })
}

describe('AdminModeRouteGuard', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    mocks.authState.userId = 'admin-user-id'
    mocks.authState.isAdmin = true
    mocks.authState.loading = false
    mocks.authFetch.mockResolvedValue(response({
      version: 1,
      unlocked: true,
      expires_at: new Date(Date.now() + 10 * 60 * 1000).toISOString(),
    }))
  })

  test('renders the protected page only after a valid Admin-mode grant', async () => {
    const { AdminModeRouteGuard } = await import('@/components/AdminModeRouteGuard')
    render(<AdminModeRouteGuard><div>管理ツール本体</div></AdminModeRouteGuard>)

    expect(screen.queryByText('管理ツール本体')).not.toBeInTheDocument()
    expect(await screen.findByText('管理ツール本体')).toBeInTheDocument()
    expect(mocks.authFetch).toHaveBeenCalledWith('/api/admin/unlock', {
      method: 'GET',
      cache: 'no-store',
    })
  })

  test('redirects a standard user without mounting protected content', async () => {
    mocks.authState.isAdmin = false
    mocks.authState.userId = 'standard-user-id'
    const { AdminModeRouteGuard } = await import('@/components/AdminModeRouteGuard')
    render(<AdminModeRouteGuard><div>管理ツール本体</div></AdminModeRouteGuard>)

    await waitFor(() => expect(mocks.replace).toHaveBeenCalledWith('/home'))
    expect(mocks.authFetch).not.toHaveBeenCalled()
    expect(screen.queryByText('管理ツール本体')).not.toBeInTheDocument()
  })

  test('redirects an Admin when the short-lived grant is missing', async () => {
    mocks.authFetch.mockResolvedValue(response({ detail: 'Admin mode is locked' }, 403))
    const { AdminModeRouteGuard } = await import('@/components/AdminModeRouteGuard')
    render(<AdminModeRouteGuard><div>管理ツール本体</div></AdminModeRouteGuard>)

    await waitFor(() => expect(mocks.replace).toHaveBeenCalledWith('/home'))
    expect(mocks.authState.refreshAuthorization).toHaveBeenCalled()
    expect(screen.queryByText('管理ツール本体')).not.toBeInTheDocument()
  })

  test('unmounts protected content immediately when the signed-in identity changes', async () => {
    const { AdminModeRouteGuard } = await import('@/components/AdminModeRouteGuard')
    const view = render(<AdminModeRouteGuard><div>管理ツール本体</div></AdminModeRouteGuard>)

    expect(await screen.findByText('管理ツール本体')).toBeInTheDocument()

    mocks.authState.userId = 'different-admin-user-id'
    view.rerender(<AdminModeRouteGuard><div>管理ツール本体</div></AdminModeRouteGuard>)

    expect(screen.queryByText('管理ツール本体')).not.toBeInTheDocument()
    expect(screen.getByRole('status')).toHaveTextContent('管理者モードを確認しています…')
  })
})
