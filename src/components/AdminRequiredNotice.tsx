'use client'

import { LockedFeatureCard } from '@/components/LockedFeatureCard'

export function AdminRequiredNotice({
  title = 'この機能は Admin 専用です',
  message = '管理者権限が必要なため、権限がない場合は実行できません。',
}: {
  title?: string
  message?: string
}) {
  return <LockedFeatureCard level="admin" title={title} message={message} />
}
