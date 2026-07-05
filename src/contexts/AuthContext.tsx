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
    const fetchRole = async () => {
      try {
        const { data: { user } } = await supabase.auth.getUser()
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
        setRole(profile?.role ?? 'user')
        setSubscriptionTier(profile?.subscription_tier ?? 'free')
      } catch {
        setRole('user')
        setSubscriptionTier('free')
      } finally {
        setLoading(false)
      }
    }

    fetchRole()

    const { data: { subscription } } = supabase.auth.onAuthStateChange(() => {
      fetchRole()
    })
    return () => subscription.unsubscribe()
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
