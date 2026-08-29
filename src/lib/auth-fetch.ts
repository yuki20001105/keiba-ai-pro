import { supabase } from './supabase'

const AUTH_SESSION_TIMEOUT_MS = 2_000

async function getSessionWithTimeout() {
  let timeoutId: ReturnType<typeof setTimeout> | undefined
  try {
    const timeout = new Promise<{ data: { session: null } }>((resolve) => {
      timeoutId = setTimeout(
        () => resolve({ data: { session: null } }),
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
    const { data: { session } } = await getSessionWithTimeout()
    token = session?.access_token ?? ''
  } catch { /* セッション取得失敗は握り潰し、token なしで続行 */ }

  const headers = new Headers(init?.headers)
  if (token) headers.set('Authorization', `Bearer ${token}`)

  return fetch(input, { ...init, headers })
}
