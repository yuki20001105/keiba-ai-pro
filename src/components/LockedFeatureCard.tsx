'use client'

import Link from 'next/link'

type LockedFeatureCardProps = {
  level: 'premium' | 'admin'
  title: string
  message: string
  actionHref?: string
  actionLabel?: string
}

export function LockedFeatureCard({
  level,
  title,
  message,
  actionHref = '/home',
  actionLabel = 'ホームへ戻る',
}: LockedFeatureCardProps) {
  const badge = level === 'premium' ? 'Premium 専用' : 'Admin 専用'
  const badgeClass = level === 'premium'
    ? 'bg-yellow-500/20 text-yellow-400 border-yellow-600/40'
    : 'bg-cyan-500/20 text-cyan-400 border-cyan-600/40'

  return (
    <div className="bg-[#111] border border-[#2a2a2a] rounded-lg p-5">
      <div className="flex items-center gap-2 mb-2">
        <span className={`text-[10px] px-2 py-0.5 rounded border ${badgeClass}`}>{badge}</span>
      </div>
      <h3 className="text-sm font-semibold text-white mb-1">{title}</h3>
      <p className="text-xs text-[#777] mb-4">{message}</p>
      <Link
        href={actionHref}
        className="inline-flex items-center text-xs px-3 py-1.5 rounded border border-[#333] text-[#aaa] hover:text-white hover:border-[#555] transition-colors"
      >
        {actionLabel}
      </Link>
    </div>
  )
}
