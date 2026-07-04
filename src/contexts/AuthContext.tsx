'use client'

import { createContext, useContext, useState, useEffect, ReactNode } from 'react'
import { supabase } from '@/lib/supabase'

interface AuthContextType {
  role: 'admin' | 'user' | null
  isAdmin: boolean
  loading: boolean
}

const AuthContext = createContext<AuthContextType>({ role: null, isAdmin: false, loading: true })

export function AuthProvider({ children }: { children: ReactNode }) {
  const [role, setRole] = useState<'admin' | 'user' | null>(null)
  const [loading, setLoading] = useState(true)

  useEffect(() => {
    // ローカル開発バイパス: Supabase 停止中でも admin として動作
    if (process.env.NEXT_PUBLIC_DEV_BYPASS_AUTH === 'true') {
      setRole('admin')
      setLoading(false)
      return
    }

    const fetchRole = async () => {
      try {
        const { data: { user } } = await supabase.auth.getUser()
        if (!user) { setRole(null); return }
        const { data: profile } = await supabase
          .from('profiles')
          .select('role')
          .eq('id', user.id)
          .single()
        setRole(profile?.role ?? 'user')
      } catch {
        setRole('user')
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
    <AuthContext.Provider value={{ role, isAdmin: role === 'admin', loading }}>
      {children}
    </AuthContext.Provider>
  )
}

export function useAuth() {
  return useContext(AuthContext)
}
