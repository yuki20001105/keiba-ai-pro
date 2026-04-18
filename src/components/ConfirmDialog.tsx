'use client'

type Props = {
  isOpen: boolean
  title: string
  message: string
  onConfirm: () => void
  onCancel: () => void
  confirmLabel?: string
  danger?: boolean
}

export function ConfirmDialog({
  isOpen,
  title,
  message,
  onConfirm,
  onCancel,
  confirmLabel = '確認',
  danger = false,
}: Props) {
  if (!isOpen) return null

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center">
      <div className="absolute inset-0 bg-black/60" onClick={onCancel} />
      <div className="relative z-10 bg-[#111] border border-[#333] rounded-xl p-6 w-full max-w-sm mx-4 space-y-4 shadow-2xl">
        <h2 className="text-sm font-semibold text-white">{title}</h2>
        <p className="text-sm text-[#888] whitespace-pre-line leading-relaxed">{message}</p>
        <div className="flex justify-end gap-3 pt-1">
          <button
            onClick={onCancel}
            className="text-xs px-4 py-2 border border-[#333] rounded hover:border-[#555] text-[#888] transition-colors"
          >
            キャンセル
          </button>
          <button
            onClick={onConfirm}
            className={`text-xs px-4 py-2 rounded font-medium transition-colors ${
              danger ? 'bg-red-600 hover:bg-red-500 text-white' : 'bg-white hover:bg-[#eee] text-black'
            }`}
          >
            {confirmLabel}
          </button>
        </div>
      </div>
    </div>
  )
}
