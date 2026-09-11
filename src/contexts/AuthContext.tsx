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
  refreshAuthorization: () => Promise<void>
}

const AuthContext = createContext<AuthContextType>({
  userId: null,
  role: null,
  subscriptionTier: null,
  isAdmin: false,
  isPremium: false,
  loading: true,
  refreshAuthorization: async () => undefined,
})

export function AuthProvider({ children }: { children: ReactNode }) {
  const [userId, setUserId] = useState<string | null>(null)
  const [role, setRole] = useState<'admin' | 'user' | null>(null)
  const [subscriptionTier, setSubscriptionTier] = useState<'free' | 'premium' | null>(null)
  const [loading, setLoading] = useState(true)
  const refreshAuthorizationRef = useRef<() => Promise<void>>(async () => undefined)
  const refreshAuthorization = useCallback(() => refreshAuthorizationRef.current(), [])

  useEffect(() => {
    let active = true
    let authRefreshTimer: ReturnType<typeof setTimeout> | null = null

    const fetchRole = async () => {
      try {
        const { data: { user } } = await supabase.auth.getUser()
        if (!active) return
        if (!user) {
          setUserId(null)
          setRole(null)
          setSubscriptionTier(null)
          return
        }
        setUserId(user.id)
        const { data: profile } = await supabase
          .from('profiles')
          .select('role, subscription_tier')
          .eq('id', user.id)
          .single()
        if (!active) return
        setRole(profile?.role ?? 'user')
        setSubscriptionTier(profile?.subscription_tier ?? 'free')
      } catch {
        if (!active) return
        setUserId(null)
        setRole('user')
        setSubscriptionTier('free')
      } finally {
        if (active) setLoading(false)
      }
    }

    refreshAuthorizationRef.current = fetchRole
    void fetchRole()

    const { data: { subscription } } = supabase.auth.onAuthStateChange(() => {
      // Supabase invokes this callback while it holds the auth lock. Calling
      // another auth method synchronously here can deadlock getSession().
      if (authRefreshTimer !== null) clearTimeout(authRefreshTimer)
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
