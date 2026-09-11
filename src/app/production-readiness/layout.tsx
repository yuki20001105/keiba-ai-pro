import type { ReactNode } from 'react'
import { AdminModeRouteGuard } from '@/components/AdminModeRouteGuard'

export default function ProductionReadinessLayout({ children }: { children: ReactNode }) {
  return <AdminModeRouteGuard>{children}</AdminModeRouteGuard>
}
