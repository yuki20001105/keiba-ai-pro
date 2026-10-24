import type { ReactNode } from 'react'
import { AdminActionRouteGuard } from '@/components/AdminActionRouteGuard'

export default function ProductionReadinessLayout({ children }: { children: ReactNode }) {
  return <AdminActionRouteGuard>{children}</AdminActionRouteGuard>
}
