'use client'

import { createContext, useCallback, useContext, useEffect, useRef, useState, ReactNode } from 'react'
import { supabase } from '@/lib/supabase'

interface AuthContextType {
  userId: string | null
  role: 'admin' | 'user' | null
  subscriptionTier: 'free' | 'premium' | null
  isAdmin: boolean
  isPremium: boolean
  loading: boolean
  authorizationUnavailable: boolean
  refreshAuthorization: () => Promise<void>
}

const AuthContext = createContext<AuthContextType>({
  userId: null,
  role: null,
  subscriptionTier: null,
  isAdmin: false,
  isPremium: false,
  loading: true,
  authorizationUnavailable: false,
  refreshAuthorization: async () => undefined,
})

export function AuthProvider({ children }: { children: ReactNode }) {
  const [userId, setUserId] = useState<string | null>(null)
  const [role, setRole] = useState<'admin' | 'user' | null>(null)
  const [subscriptionTier, setSubscriptionTier] = useState<'free' | 'premium' | null>(null)
  const [loading, setLoading] = useState(true)
  const [authorizationUnavailable, setAuthorizationUnavailable] = useState(false)
  const refreshAuthorizationRef = useRef<() => Promise<void>>(async () => undefined)
  const refreshAuthorization = useCallback(() => refreshAuthorizationRef.current(), [])

  useEffect(() => {
    let active = true
    let revision = 0
    let verifiedUserId: string | null = null
    let authRefreshTimer: ReturnType<typeof setTimeout> | null = null

    const clearAuthorization = () => {
      verifiedUserId = null
      setUserId(null)
      setRole(null)
      setSubscriptionTier(null)
      setAuthorizationUnavailable(false)
    }

    const fetchRole = async () => {
      const requestRevision = ++revision
      try {
        const { data: { user }, error: userError } = await supabase.auth.getUser()
        if (!active || requestRevision !== revision) return
        if (userError) {
          if (userError.status === 401 || userError.status === 403) clearAuthorization()
          else setAuthorizationUnavailable(true)
          return
        }
        if (!user) {
          clearAuthorization()
          return
        }
        if (verifiedUserId !== null && user.id !== verifiedUserId) clearAuthorization()
        const { data: profile, error: profileError } = await supabase
          .from('profiles')
          .select('role, subscription_tier')
          .eq('id', user.id)
          .single()
        if (!active || requestRevision !== revision) return
        if (profileError) {
          setAuthorizationUnavailable(true)
          return
        }
        setUserId(user.id)
        verifiedUserId = user.id
        setRole(profile?.role === 'admin' ? 'admin' : 'user')
        setSubscriptionTier(profile?.subscription_tier === 'premium' ? 'premium' : 'free')
        setAuthorizationUnavailable(false)
      } catch {
        if (!active || requestRevision !== revision) return
        setAuthorizationUnavailable(true)
      } finally {
        if (active && requestRevision === revision) setLoading(false)
      }
    }

    refreshAuthorizationRef.current = fetchRole
    void fetchRole()

    const { data: { subscription } } = supabase.auth.onAuthStateChange((event, session) => {
      // Supabase invokes this callback while it holds the auth lock. Calling
      // another auth method synchronously here can deadlock getSession().
      if (authRefreshTimer !== null) clearTimeout(authRefreshTimer)
      if (event === 'SIGNED_OUT') {
        ++revision
        clearAuthorization()
        setLoading(false)
        return
      }
      // A changed account must not retain the previous account's display permissions.
      if (event === 'SIGNED_IN' && session?.user?.id && session.user.id !== verifiedUserId) {
        ++revision
        clearAuthorization()
        setLoading(true)
      }
      authRefreshTimer = setTimeout(() => {
        authRefreshTimer = null
        if (active) void fetchRole()
      }, 0)
    })
    return () => {
      active = false
      if (authRefreshTimer !== null) clearTimeout(authRefreshTimer)
      refreshAuthorizationRef.current = async () => undefined
      subscription.unsubscribe()
    }
  }, [])

  return (
    <AuthContext.Provider
      value={{
        userId,
        role,
        subscriptionTier,
        isAdmin: role === 'admin',
        isPremium: role === 'admin' || subscriptionTier === 'premium',
        loading,
        authorizationUnavailable,
        refreshAuthorization,
      }}
    >
      {children}
    </AuthContext.Provider>
  )
}

export function useAuth() {
  return useContext(AuthContext)
}
