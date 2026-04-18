'use client'

import { useAuth } from '@/contexts/AuthContext'

/** @deprecated useAuth() を直接使ってください */
export function useUserRole() {
  return useAuth()
}
