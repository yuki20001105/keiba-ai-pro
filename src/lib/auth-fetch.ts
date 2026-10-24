import { supabase } from './supabase'

const AUTH_SESSION_TIMEOUT_MS = 2_000

export class AuthSessionUnavailableError extends Error {
  readonly code = 'AUTH_SESSION_UNAVAILABLE'

  constructor() {
    super('認証への接続を再確認中です。しばらくして再試行してください。')
    this.name = 'AuthSessionUnavailableError'
  }
}

async function getSessionWithTimeout() {
  let timeoutId: ReturnType<typeof setTimeout> | undefined
  try {
    const timeout = new Promise<never>((_resolve, reject) => {
      timeoutId = setTimeout(
        () => reject(new AuthSessionUnavailableError()),
        AUTH_SESSION_TIMEOUT_MS,
      )
    })
    return await Promise.race([supabase.auth.getSession(), timeout])
  } finally {
    if (timeoutId !== undefined) clearTimeout(timeoutId)
  }
}

/**
 * Supabase セッショントークンを自動付与する fetch ラッパー。
 * クライアントコンポーネントから FastAPI 経由のルートを呼ぶときに使う。
 */
export async function authFetch(
  input: RequestInfo | URL,
  init?: RequestInit,
): Promise<Response> {
  let token = ''
  try {
    const { data: { session }, error } = await getSessionWithTimeout()
    if (error) throw new AuthSessionUnavailableError()
    token = session?.access_token ?? ''
  } catch {
    // A refresh timeout is not a logout; do not turn it into a tokenless 401.
    throw new AuthSessionUnavailableError()
  }

  const headers = new Headers(init?.headers)
  if (token) headers.set('Authorization', `Bearer ${token}`)

  return fetch(input, { ...init, headers })
}
