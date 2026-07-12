import { createClient, type SupabaseClient, type User } from '@supabase/supabase-js'

type AuthzRole = 'admin' | 'user'
type AuthzTier = 'free' | 'premium'

export type VerifiedAuthContext = {
  user: User
  token: string
  profile: {
    role: AuthzRole
    subscription_tier: AuthzTier
    stripe_customer_id?: string | null
    stripe_subscription_id?: string | null
    ocr_monthly_limit?: number
    ocr_used_this_month?: number
    ocr_reset_date?: string | null
    email?: string | null
  }
}

export type AuthzResult =
  | { ok: true; context: VerifiedAuthContext }
  | { ok: false; status: 401 | 403 | 503; detail: string }

type VerifyOptions = {
  requireAdmin?: boolean
  requirePremiumOrAdmin?: boolean
}

function getSupabaseBaseConfig() {
  const supabaseUrl = process.env.NEXT_PUBLIC_SUPABASE_URL || process.env.SUPABASE_URL || ''
  const anonKey = process.env.NEXT_PUBLIC_SUPABASE_ANON_KEY || ''
  return { supabaseUrl, anonKey }
}

function getBearerToken(request: Request): string {
  const authHeader = request.headers.get('Authorization') || ''
  if (!authHeader.startsWith('Bearer ')) return ''
  return authHeader.slice('Bearer '.length).trim()
}

export function createSupabaseServiceClient(): SupabaseClient | null {
  const { supabaseUrl, anonKey } = getSupabaseBaseConfig()
  const serviceRoleKey = process.env.SUPABASE_SERVICE_ROLE_KEY || process.env.SUPABASE_SERVICE_KEY || ''
  if (!supabaseUrl || !serviceRoleKey || !anonKey) return null
  return createClient(supabaseUrl, serviceRoleKey)
}

function createSupabaseAuthClient(token: string): SupabaseClient | null {
  const { supabaseUrl, anonKey } = getSupabaseBaseConfig()
  if (!supabaseUrl || !anonKey || !token) return null
  return createClient(supabaseUrl, anonKey, {
    global: {
      headers: {
        Authorization: `Bearer ${token}`,
      },
    },
  })
}

export async function verifyRequestAuth(request: Request, options: VerifyOptions = {}): Promise<AuthzResult> {
  const token = getBearerToken(request)
  if (!token) {
    return { ok: false, status: 401, detail: 'Authentication required' }
  }

  const authClient = createSupabaseAuthClient(token)
  const serviceClient = createSupabaseServiceClient()
  if (!authClient) {
    return { ok: false, status: 503, detail: 'Supabase auth configuration missing' }
  }
  if (!serviceClient) {
    return { ok: false, status: 503, detail: 'Supabase service role configuration missing' }
  }

  const { data: userData, error: userError } = await authClient.auth.getUser()
  if (userError || !userData.user) {
    return { ok: false, status: 401, detail: 'Authentication required' }
  }

  const { data: profile, error: profileError } = await serviceClient
    .from('profiles')
    .select('role, subscription_tier, stripe_customer_id, stripe_subscription_id, ocr_monthly_limit, ocr_used_this_month, ocr_reset_date, email')
    .eq('id', userData.user.id)
    .maybeSingle()

  if (profileError) {
    return { ok: false, status: 503, detail: 'Authorization backend unavailable' }
  }
  if (!profile) {
    return { ok: false, status: 403, detail: 'Access denied' }
  }

  const roleRaw = String((profile as Record<string, unknown>).role || 'user').toLowerCase()
  const tierRaw = String((profile as Record<string, unknown>).subscription_tier || 'free').toLowerCase()
  const role: AuthzRole = roleRaw === 'admin' ? 'admin' : 'user'
  const tier: AuthzTier = tierRaw === 'premium' ? 'premium' : 'free'
  const isAdmin = role === 'admin'
  const isPremiumOrAdmin = isAdmin || tier === 'premium'

  if (options.requireAdmin && !isAdmin) {
    return { ok: false, status: 403, detail: 'Admin role required' }
  }
  if (options.requirePremiumOrAdmin && !isPremiumOrAdmin) {
    return { ok: false, status: 403, detail: 'Premium or admin role required' }
  }

  return {
    ok: true,
    context: {
      user: userData.user,
      token,
      profile: {
        role,
        subscription_tier: tier,
        stripe_customer_id: (profile as Record<string, unknown>).stripe_customer_id as string | null,
        stripe_subscription_id: (profile as Record<string, unknown>).stripe_subscription_id as string | null,
        ocr_monthly_limit: Number((profile as Record<string, unknown>).ocr_monthly_limit ?? 0),
        ocr_used_this_month: Number((profile as Record<string, unknown>).ocr_used_this_month ?? 0),
        ocr_reset_date: ((profile as Record<string, unknown>).ocr_reset_date as string | null) ?? null,
        email: ((profile as Record<string, unknown>).email as string | null) ?? null,
      },
    },
  }
}