'use client'

import { useEffect, useState } from 'react'
import { supabase } from '@/lib/supabase'
import type { Profile } from '@/lib/supabase'

export function useUserRole() {
  const [role, setRole] = useState<'admin' | 'user' | null>(null)
  const [loading, setLoading] = useState(true)

  useEffect(() => {
    const fetchUserRole = async () => {
      try {
        const { data: { user } } = await supabase.auth.getUser()
        
        if (!user) {
          setRole(null)
          setLoading(false)
          return
        }

        const { data: profile } = await supabase
          .from('profiles')
          .select('role')
          .eq('id', user.id)
          .single()

        setRole(profile?.role || 'user')
      } catch (error) {
        console.error('Error fetching user role:', error)
        setRole('user') // デフォルトは一般ユーザー
      } finally {
        setLoading(false)
      }
    }

    fetchUserRole()
  }, [])

  return { role, isAdmin: role === 'admin', loading }
}
