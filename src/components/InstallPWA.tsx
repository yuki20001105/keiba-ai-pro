'use client'

import { useState, useEffect } from 'react'

export default function InstallPWA() {
  const [deferredPrompt, setDeferredPrompt] = useState<any>(null)
  const [showInstall, setShowInstall] = useState(false)

  useEffect(() => {
    const handler = (e: Event) => {
      e.preventDefault()
      setDeferredPrompt(e)
      setShowInstall(true)
    }

    window.addEventListener('beforeinstallprompt', handler)

    return () => {
      window.removeEventListener('beforeinstallprompt', handler)
    }
  }, [])

  const handleInstall = async () => {
    if (!deferredPrompt) return

    deferredPrompt.prompt()
    const { outcome } = await deferredPrompt.userChoice
    
    if (outcome === 'accepted') {
      console.log('PWAインストール完了')
    }
    
    setDeferredPrompt(null)
    setShowInstall(false)
  }

  if (!showInstall) return null

  return (
    <div className="fixed bottom-4 left-4 right-4 md:left-auto md:right-4 md:w-96 bg-gradient-to-r from-blue-600 to-cyan-600 text-white p-4 rounded-lg shadow-2xl z-50 animate-slide-up">
      <div className="flex items-start gap-3">
        <div className="text-3xl">📱</div>
        <div className="flex-1">
          <h3 className="font-bold text-lg mb-1">アプリをインストール</h3>
          <p className="text-sm text-blue-100 mb-3">
            ホーム画面に追加して、アプリのように使えます
          </p>
          <div className="flex gap-2">
            <button
              onClick={handleInstall}
              className="flex-1 bg-white text-blue-600 font-semibold py-2 px-4 rounded-lg hover:bg-blue-50 transition"
            >
              インストール
            </button>
            <button
              onClick={() => setShowInstall(false)}
              className="px-4 py-2 text-white hover:bg-white/20 rounded-lg transition"
            >
              後で
            </button>
          </div>
        </div>
      </div>
    </div>
  )
}
