'use client'

import { useRouter } from 'next/navigation'
import { useUserRole } from '@/hooks/useUserRole'
import { useEffect } from 'react'

export function AdminOnly({ children }: { children: React.ReactNode }) {
  const { isAdmin, loading } = useUserRole()
  const router = useRouter()

  useEffect(() => {
    if (!loading && !isAdmin) {
      // 一般ユーザーがアクセスした場合、ホーム画面へリダイレクト
      router.push('/home')
    }
  }, [isAdmin, loading, router])

  if (loading) {
    return (
      <div className="flex items-center justify-center min-h-screen">
        <div className="text-center">
          <div className="animate-spin rounded-full h-12 w-12 border-b-2 border-gray-900 mx-auto"></div>
          <p className="mt-4 text-gray-600">読み込み中...</p>
        </div>
      </div>
    )
  }

  if (!isAdmin) {
    return null
  }

  return <>{children}</>
}
