'use client'

import { useEffect } from 'react'

type ToastType = 'success' | 'error' | 'info'

type Props = {
  message: string
  type?: ToastType
  isVisible: boolean
  onClose: () => void
  duration?: number
}

export function Toast({
  message,
  type = 'success',
  isVisible,
  onClose,
  duration = 4000,
}: Props) {
  useEffect(() => {
    if (!isVisible) return
    const t = setTimeout(onClose, duration)
    return () => clearTimeout(t)
  }, [isVisible, duration, onClose])

  if (!isVisible) return null

  const colors: Record<ToastType, string> = {
    success: 'bg-[#052e10] border-[#166534] text-[#4ade80]',
    error: 'bg-[#1a0505] border-[#7f1d1d] text-[#f87171]',
    info: 'bg-[#0a1628] border-[#1e3a5f] text-[#7dd3fc]',
  }

  return (
    <div
      className={`fixed bottom-6 right-6 z-50 flex items-center gap-3 px-4 py-3 border rounded-lg text-sm max-w-sm shadow-lg ${colors[type]}`}
    >
      <span className="flex-1">{message}</span>
      <button
        onClick={onClose}
        className="opacity-60 hover:opacity-100 text-base leading-none shrink-0"
      >
        ×
      </button>
    </div>
  )
}
