import { supabase } from './supabase'

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
    const { data: { session } } = await supabase.auth.getSession()
    token = session?.access_token ?? ''
  } catch { /* セッション取得失敗は握り潰し、token なしで続行 */ }

  const headers = new Headers(init?.headers)
  if (token) headers.set('Authorization', `Bearer ${token}`)

  // INV-05準拠: FastAPIへのリクエストは最大300秒のタイムアウトを設定
  const signal = (init?.signal) ?? AbortSignal.timeout(300_000)
  return fetch(input, { ...init, headers, signal })
}
