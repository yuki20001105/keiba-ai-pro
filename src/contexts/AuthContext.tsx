'use client'

import { createContext, useContext, useState, useEffect, ReactNode } from 'react'
import { supabase } from '@/lib/supabase'

interface AuthContextType {
  role: 'admin' | 'user' | null
  subscriptionTier: 'free' | 'premium' | null
  isAdmin: boolean
  isPremium: boolean
  loading: boolean
}

const AuthContext = createContext<AuthContextType>({
  role: null,
  subscriptionTier: null,
  isAdmin: false,
  isPremium: false,
  loading: true,
})

export function AuthProvider({ children }: { children: ReactNode }) {
  const [role, setRole] = useState<'admin' | 'user' | null>(null)
  const [subscriptionTier, setSubscriptionTier] = useState<'free' | 'premium' | null>(null)
  const [loading, setLoading] = useState(true)

  useEffect(() => {
    let active = true
    let authRefreshTimer: ReturnType<typeof setTimeout> | null = null

    const fetchRole = async () => {
      try {
        const { data: { user } } = await supabase.auth.getUser()
        if (!active) return
        if (!user) {
          setRole(null)
          setSubscriptionTier(null)
          return
        }
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
        setRole('user')
        setSubscriptionTier('free')
      } finally {
        if (active) setLoading(false)
      }
    }

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
      subscription.unsubscribe()
    }
  }, [])

  return (
    <AuthContext.Provider
      value={{
        role,
        subscriptionTier,
        isAdmin: role === 'admin',
        isPremium: role === 'admin' || subscriptionTier === 'premium',
        loading,
      }}
    >
      {children}
    </AuthContext.Provider>
  )
}

export function useAuth() {
  return useContext(AuthContext)
}
