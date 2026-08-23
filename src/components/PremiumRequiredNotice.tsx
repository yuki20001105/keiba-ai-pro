'use client'

import { LockedFeatureCard } from '@/components/LockedFeatureCard'

export function PremiumRequiredNotice({
  title = 'この機能は Premium 専用です',
  message = 'Premium プランで利用可能な機能です。権限がない場合は API 実行を行いません。',
}: {
  title?: string
  message?: string
}) {
  return <LockedFeatureCard level="premium" title={title} message={message} />
}
