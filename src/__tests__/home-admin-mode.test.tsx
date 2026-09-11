import { fireEvent, render, screen, waitFor } from '@testing-library/react'
import { afterEach, beforeEach, describe, expect, test, vi } from 'vitest'

const mocks = vi.hoisted(() => ({
  authState: {
    userId: 'admin-user-id' as string | null,
    isAdmin: false,
    loading: false,
    refreshAuthorization: vi.fn(),
  },
  getUser: vi.fn(),
  signInWithPassword: vi.fn(),
  signOut: vi.fn(),
  authFetch: vi.fn(),
  workspaceMount: vi.fn(),
  routerReplace: vi.fn(),
}))

vi.mock('@/contexts/AuthContext', () => ({
  useAuth: () => mocks.authState,
}))

vi.mock('@/lib/supabase', () => ({
  supabase: {
    auth: {
      getUser: mocks.getUser,
      signInWithPassword: mocks.signInWithPassword,
      signOut: mocks.signOut,
    },
  },
}))

vi.mock('@/lib/auth-fetch', () => ({ authFetch: mocks.authFetch }))
vi.mock('next/navigation', () => ({ useRouter: () => ({ replace: mocks.routerReplace }) }))
vi.mock('@/components/Logo', () => ({ Logo: () => <div>競馬AI Pro</div> }))
vi.mock('@/components/AdminWorkspace', () => ({
  AdminWorkspace: () => {
    mocks.workspaceMount()
    return <div>統合管理ワークスペース</div>
  },
}))

function jsonResponse(value: unknown, status = 200): Response {
  return new Response(JSON.stringify(value), {
    status,
    headers: { 'Content-Type': 'application/json' },
  })
}

describe('Home Admin mode switch', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    mocks.authState.isAdmin = false
    mocks.authState.loading = false
    mocks.authState.userId = 'admin-user-id'
    mocks.getUser.mockResolvedValue({
      data: { user: { id: 'admin-user-id', email: 'admin@example.com' } },
      error: null,
    })
    mocks.signInWithPassword.mockResolvedValue({
      data: { user: { id: 'admin-user-id', email: 'admin@example.com' }, session: {} },
      error: null,
    })
    mocks.signOut.mockResolvedValue({ error: null })
    mocks.authFetch.mockImplementation(async (input: RequestInfo | URL, init?: RequestInit) => {
      const url = String(input)
      if (url === '/api/data-stats') return jsonResponse({ total_races: 12, total_models: 3 })
      if (url === '/api/admin/unlock' && init?.method === 'GET') {
        return jsonResponse({ detail: 'Admin mode is locked' }, 403)
      }
      if (url === '/api/admin/unlock' && init?.method === 'POST') {
        return jsonResponse({
          version: 1,
          unlocked: true,
          expires_at: new Date(Date.now() + 10 * 60 * 1000).toISOString(),
        })
      }
      throw new Error(`unexpected request: ${url}`)
    })
    vi.stubGlobal('fetch', vi.fn(async () => jsonResponse({ status: 'ok' })))
  })

  afterEach(() => {
    vi.unstubAllGlobals()
  })

  test('does not expose the Admin switch or workspace to a standard user', async () => {
    const { default: HomePage } = await import('@/app/home/page')
    render(<HomePage />)

    expect(screen.queryByRole('button', { name: '管理者モード' })).not.toBeInTheDocument()
    expect(screen.queryByText('統合管理ワークスペース')).not.toBeInTheDocument()
    expect(mocks.workspaceMount).not.toHaveBeenCalled()
  })

  test('keeps an Admin locked until the password dialog is completed', async () => {
    mocks.authState.isAdmin = true
    const { default: HomePage } = await import('@/app/home/page')
    render(<HomePage />)

    expect(screen.getByText('AI競馬予測')).toBeInTheDocument()
    expect(screen.queryByText('統合管理ワークスペース')).not.toBeInTheDocument()

    fireEvent.click(screen.getByRole('button', { name: '管理者モード' }))
    expect(screen.getByRole('dialog', { name: '管理者パスワードの確認' })).toBeInTheDocument()
    expect(screen.getByLabelText('管理者パスワード')).toHaveAttribute('type', 'password')
    expect(mocks.workspaceMount).not.toHaveBeenCalled()
  })

  test('keeps the workspace hidden and clears the password after a failed check', async () => {
    mocks.authState.isAdmin = true
    mocks.signInWithPassword.mockResolvedValue({
      data: { user: null, session: null },
      error: { message: 'Invalid login credentials' },
    })
    const { default: HomePage } = await import('@/app/home/page')
    render(<HomePage />)

    fireEvent.click(screen.getByRole('button', { name: '管理者モード' }))
    const password = screen.getByLabelText('管理者パスワード')
    fireEvent.change(password, { target: { value: 'wrong-password' } })
    fireEvent.click(screen.getByRole('button', { name: '確認して切り替える' }))

    expect(await screen.findByRole('alert')).toHaveTextContent('パスワードを確認できませんでした')
    expect(password).toHaveValue('')
    expect(screen.queryByText('統合管理ワークスペース')).not.toBeInTheDocument()
    expect(mocks.authFetch.mock.calls.some(([input, init]) => (
      String(input) === '/api/admin/unlock' && (init as RequestInit | undefined)?.method === 'POST'
    ))).toBe(false)
  })

  test('switches views on the same page only after password and server verification', async () => {
    mocks.authState.isAdmin = true
    const { default: HomePage } = await import('@/app/home/page')
    render(<HomePage />)

    fireEvent.click(screen.getByRole('button', { name: '管理者モード' }))
    fireEvent.change(screen.getByLabelText('管理者パスワード'), { target: { value: 'verified-password' } })
    fireEvent.click(screen.getByRole('button', { name: '確認して切り替える' }))

    expect(await screen.findByText('統合管理ワークスペース')).toBeInTheDocument()
    expect(screen.queryByText('AI競馬予測')).not.toBeInTheDocument()
    expect(mocks.signInWithPassword).toHaveBeenCalledWith({
      email: 'admin@example.com',
      password: 'verified-password',
    })
    expect(mocks.authFetch).toHaveBeenCalledWith('/api/admin/unlock', {
      method: 'POST',
      cache: 'no-store',
    })

    fireEvent.click(screen.getByRole('button', { name: 'ユーザー画面に戻る' }))
    await waitFor(() => expect(screen.getByText('AI競馬予測')).toBeInTheDocument())
    expect(screen.queryByText('統合管理ワークスペース')).not.toBeInTheDocument()
  })

  test('fails closed when the server refuses or returns an expired unlock', async () => {
    mocks.authState.isAdmin = true
    mocks.authFetch.mockImplementation(async (input: RequestInfo | URL, init?: RequestInit) => {
      const url = String(input)
      if (url === '/api/data-stats') return jsonResponse({ total_races: 12, total_models: 3 })
      if (url === '/api/admin/unlock' && init?.method === 'GET') return jsonResponse({}, 403)
      if (url === '/api/admin/unlock' && init?.method === 'POST') {
        return jsonResponse({
          version: 1,
          unlocked: true,
          expires_at: new Date(Date.now() - 1).toISOString(),
        })
      }
      throw new Error(`unexpected request: ${url}`)
    })
    const { default: HomePage } = await import('@/app/home/page')
    render(<HomePage />)

    fireEvent.click(screen.getByRole('button', { name: '管理者モード' }))
    fireEvent.change(screen.getByLabelText('管理者パスワード'), { target: { value: 'verified-password' } })
    fireEvent.click(screen.getByRole('button', { name: '確認して切り替える' }))

    expect(await screen.findByRole('alert')).toHaveTextContent('パスワードを確認できませんでした')
    expect(screen.queryByText('統合管理ワークスペース')).not.toBeInTheDocument()
  })

  test('locks an open workspace when the authenticated identity changes', async () => {
    mocks.authState.isAdmin = true
    const { default: HomePage } = await import('@/app/home/page')
    const { rerender } = render(<HomePage />)

    fireEvent.click(screen.getByRole('button', { name: '管理者モード' }))
    fireEvent.change(screen.getByLabelText('管理者パスワード'), { target: { value: 'verified-password' } })
    fireEvent.click(screen.getByRole('button', { name: '確認して切り替える' }))
    expect(await screen.findByText('統合管理ワークスペース')).toBeInTheDocument()

    mocks.authState.userId = 'different-admin-id'
    rerender(<HomePage />)

    expect(screen.queryByText('統合管理ワークスペース')).not.toBeInTheDocument()
    expect(await screen.findByText('AI競馬予測')).toBeInTheDocument()
    expect(screen.getByRole('status')).toHaveTextContent('ログイン状態が変わったため')
  })
})
