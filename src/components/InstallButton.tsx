'use client'

import { useEffect, useState } from 'react'

interface BeforeInstallPromptEvent extends Event {
  prompt(): Promise<void>
  userChoice: Promise<{ outcome: 'accepted' | 'dismissed' }>
}

export function InstallButton() {
  const [deferredPrompt, setDeferredPrompt] = useState<BeforeInstallPromptEvent | null>(null)
  const [installed, setInstalled] = useState(false)

  useEffect(() => {
    // すでにスタンドアロン（PWA）として起動中なら非表示
    if (window.matchMedia('(display-mode: standalone)').matches) {
      setInstalled(true)
      return
    }

    const handler = (e: Event) => {
      e.preventDefault()
      setDeferredPrompt(e as BeforeInstallPromptEvent)
    }
    window.addEventListener('beforeinstallprompt', handler)
    return () => window.removeEventListener('beforeinstallprompt', handler)
  }, [])

  const handleInstall = async () => {
    if (!deferredPrompt) {
      // Chrome 以外 or すでにインストール済みの場合はガイドを表示
      alert('ブラウザのアドレスバー右端にある「⊕」または「...」メニューから「アプリをインストール」を選択してください。')
      return
    }
    await deferredPrompt.prompt()
    const { outcome } = await deferredPrompt.userChoice
    if (outcome === 'accepted') setInstalled(true)
    setDeferredPrompt(null)
  }

  if (installed) {
    return (
      <span className="bg-[#1e1e1e] text-[#888] text-sm font-semibold px-8 py-3.5 rounded cursor-default">
        ✓ インストール済み
      </span>
    )
  }

  return (
    <button
      onClick={handleInstall}
      className="bg-white text-black text-sm font-semibold px-8 py-3.5 rounded hover:bg-[#eee] transition-colors"
    >
      アプリをデスクトップにインストール
    </button>
  )
}
