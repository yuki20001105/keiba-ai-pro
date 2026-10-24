import { act, render, screen } from '@testing-library/react'
import { afterEach, beforeEach, describe, expect, test, vi } from 'vitest'
import { AuthProvider, useAuth } from '@/contexts/AuthContext'

const mocks = vi.hoisted(() => ({
  getUser: vi.fn(),
  single: vi.fn(),
  callback: null as null | ((event: string, session: unknown) => void),
  unsubscribe: vi.fn(),
}))
vi.mock('@/lib/supabase', () => ({
  supabase: {
    auth: {
      getUser: mocks.getUser,
      onAuthStateChange: (callback: (event: string, session: unknown) => void) => {
        mocks.callback = callback
        return { data: { subscription: { unsubscribe: mocks.unsubscribe } } }
      },
    },
    from: () => ({ select: () => ({ eq: () => ({ single: mocks.single }) }) }),
  },
}))

function State() {
  const auth = useAuth()
  return <div data-testid="auth">{JSON.stringify({ userId: auth.userId, admin: auth.isAdmin, unavailable: auth.authorizationUnavailable })}</div>
}
function readState() { return JSON.parse(screen.getByTestId('auth').textContent || '{}') }

describe('AuthProvider long-running session', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    vi.useFakeTimers()
    mocks.getUser.mockResolvedValue({ data: { user: { id: 'admin-1' } }, error: null })
    mocks.single.mockResolvedValue({ data: { role: 'admin', subscription_tier: 'premium' }, error: null })
  })
  afterEach(() => vi.useRealTimers())

  async function mount() {
    await act(async () => { render(<AuthProvider><State /></AuthProvider>) })
    expect(readState()).toEqual({ userId: 'admin-1', admin: true, unavailable: false })
  }
  async function refresh() {
    await act(async () => {
      mocks.callback?.('TOKEN_REFRESHED', { user: { id: 'admin-1' } })
      await vi.advanceTimersByTimeAsync(0)
    })
  }

  test('keeps the known screen identity only in memory during an auth service outage', async () => {
    await mount()
    mocks.getUser.mockResolvedValueOnce({ data: { user: null }, error: { status: 503 } })
    await refresh()
    expect(readState()).toEqual({ userId: 'admin-1', admin: true, unavailable: true })
    await refresh()
    expect(readState()).toEqual({ userId: 'admin-1', admin: true, unavailable: false })
  })

  test('a profile outage is not a role revocation, but a successful changed profile is', async () => {
    await mount()
    mocks.single.mockResolvedValueOnce({ data: null, error: { message: 'offline' } })
    await refresh()
    expect(readState()).toEqual({ userId: 'admin-1', admin: true, unavailable: true })
    mocks.single.mockResolvedValueOnce({ data: { role: 'user' }, error: null })
    await refresh()
    expect(readState()).toEqual({ userId: 'admin-1', admin: false, unavailable: false })
  })

  test('SIGNED_OUT clears immediately and an in-flight old refresh cannot restore the user', async () => {
    await mount()
    let resolveOld!: (value: unknown) => void
    mocks.getUser.mockReturnValueOnce(new Promise(resolve => { resolveOld = resolve }))
    await refresh()
    act(() => { mocks.callback?.('SIGNED_OUT', null) })
    expect(readState()).toEqual({ userId: null, admin: false, unavailable: false })
    await act(async () => { resolveOld({ data: { user: { id: 'admin-1' } }, error: null }) })
    expect(readState()).toEqual({ userId: null, admin: false, unavailable: false })
  })

  test('a repeated same-account SIGNED_IN does not clear a long-running screen', async () => {
    await mount()
    act(() => { mocks.callback?.('SIGNED_IN', { user: { id: 'admin-1' } }) })
    expect(readState()).toEqual({ userId: 'admin-1', admin: true, unavailable: false })
  })

  test('a different account cannot inherit the old admin role when its profile lookup fails', async () => {
    await mount()
    mocks.getUser.mockResolvedValueOnce({ data: { user: { id: 'user-2' } }, error: null })
    mocks.single.mockResolvedValueOnce({ data: null, error: { message: 'offline' } })
    await act(async () => {
      mocks.callback?.('SIGNED_IN', { user: { id: 'user-2' } })
      await vi.advanceTimersByTimeAsync(0)
    })
    expect(readState()).toEqual({ userId: null, admin: false, unavailable: true })
  })

  test.each([401, 403])('confirmed authentication rejection %s clears the admin identity', async status => {
    await mount()
    mocks.getUser.mockResolvedValueOnce({ data: { user: null }, error: { status } })
    await refresh()
    expect(readState()).toEqual({ userId: null, admin: false, unavailable: false })
  })
})
