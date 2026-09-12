import { fireEvent, render, screen, waitFor } from '@testing-library/react'
import { beforeEach, describe, expect, test, vi } from 'vitest'

const mocks = vi.hoisted(() => ({
  authState: {
    userId: 'test-user-id' as string | null,
    isAdmin: false,
    loading: false,
    refreshAuthorization: vi.fn(),
  },
  signOut: vi.fn(),
  routerReplace: vi.fn(),
}))

vi.mock('@/contexts/AuthContext', () => ({
  useAuth: () => mocks.authState,
}))

vi.mock('@/lib/supabase', () => ({
  supabase: {
    auth: {
      signOut: mocks.signOut,
    },
  },
}))

vi.mock('next/navigation', () => ({ useRouter: () => ({ replace: mocks.routerReplace }) }))
vi.mock('@/components/Logo', () => ({ Logo: () => <div>競馬AI Pro</div> }))

describe('Home unified five-step menu', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    mocks.authState.userId = 'test-user-id'
    mocks.authState.isAdmin = false
    mocks.authState.loading = false
    mocks.signOut.mockResolvedValue({ error: null })
  })

  test('shows only prediction and performance to a standard user without a mode switch', async () => {
    const { default: HomePage } = await import('@/app/home/page')
    render(<HomePage />)

    expect(screen.getByText('利用できる機能 — 2件')).toBeInTheDocument()
    expect(screen.getByRole('link', { name: /予測実行/ })).toHaveAttribute('href', '/predict-batch')
    expect(screen.getByRole('link', { name: /成績確認/ })).toHaveAttribute('href', '/dashboard')
    expect(screen.queryByRole('link', { name: /データ取得/ })).not.toBeInTheDocument()
    expect(screen.queryByRole('link', { name: /モデル作成/ })).not.toBeInTheDocument()
    expect(screen.queryByRole('link', { name: /ユーザー管理/ })).not.toBeInTheDocument()
    expect(screen.queryByRole('button', { name: '管理者モード' })).not.toBeInTheDocument()
    expect(screen.queryByRole('dialog')).not.toBeInTheDocument()
  })

  test('does not flash role-specific links while authorization is loading', async () => {
    mocks.authState.loading = true
    mocks.authState.isAdmin = false
    const { default: HomePage } = await import('@/app/home/page')
    render(<HomePage />)

    expect(screen.getByRole('status')).toHaveTextContent('利用できる機能を確認しています…')
    expect(screen.queryByRole('link')).not.toBeInTheDocument()
  })

  test('shows the exact five ordered functions to an Admin without password switching', async () => {
    mocks.authState.isAdmin = true
    const { default: HomePage } = await import('@/app/home/page')
    render(<HomePage />)

    expect(screen.getByText('基本的な使い方 — 5ステップ')).toBeInTheDocument()
    const links = screen.getAllByRole('link')
    expect(links).toHaveLength(5)
    expect(links.map(link => link.getAttribute('href'))).toEqual([
      '/data-collection',
      '/train',
      '/predict-batch',
      '/dashboard',
      '/user-management',
    ])
    expect(links.map(link => link.textContent)).toEqual(expect.arrayContaining([
      expect.stringContaining('01データ取得'),
      expect.stringContaining('02モデル作成'),
      expect.stringContaining('03予測実行'),
      expect.stringContaining('04成績確認'),
      expect.stringContaining('05ユーザー管理'),
    ]))
    expect(screen.queryByRole('button', { name: '管理者モード' })).not.toBeInTheDocument()
    expect(screen.queryByLabelText('管理者パスワード')).not.toBeInTheDocument()
    expect(screen.queryByText('本番前チェック')).not.toBeInTheDocument()
  })

  test('removes Admin-only links immediately when the current role changes', async () => {
    mocks.authState.isAdmin = true
    const { default: HomePage } = await import('@/app/home/page')
    const view = render(<HomePage />)
    expect(screen.getByRole('link', { name: /データ取得/ })).toBeInTheDocument()

    mocks.authState.isAdmin = false
    view.rerender(<HomePage />)

    expect(screen.queryByRole('link', { name: /データ取得/ })).not.toBeInTheDocument()
    expect(screen.queryByRole('link', { name: /ユーザー管理/ })).not.toBeInTheDocument()
  })

  test('logs out from the shared header', async () => {
    const { default: HomePage } = await import('@/app/home/page')
    render(<HomePage />)

    fireEvent.click(screen.getByRole('button', { name: 'ログアウト' }))
    await waitFor(() => expect(mocks.signOut).toHaveBeenCalledTimes(1))
    expect(mocks.routerReplace).toHaveBeenCalledWith('/login')
  })
})
