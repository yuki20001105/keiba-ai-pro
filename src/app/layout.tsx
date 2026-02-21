import '@/app/globals.css'
import type { Metadata, Viewport } from 'next'
import { Inter } from 'next/font/google'
import { UltimateModeProvider } from '@/contexts/UltimateModeContext'
import Script from 'next/script'

const inter = Inter({ subsets: ['latin'] })

export const metadata: Metadata = {
  title: '競馬AI Pro - AI予測システム',
  description: '機械学習による競馬予測・資金管理・統計分析',
  manifest: '/manifest.json',
  appleWebApp: {
    capable: true,
    statusBarStyle: 'default',
    title: '競馬AI Pro',
  },
  formatDetection: {
    telephone: false,
  },
}

export const viewport: Viewport = {
  width: 'device-width',
  initialScale: 1,
  maximumScale: 5,
  userScalable: true,
  themeColor: '#0a0a0a',
}

export default function RootLayout({
  children,
}: {
  children: React.ReactNode
}) {
  return (
    <html lang="ja">
      <head>
        <link rel="icon" href="/icon.svg" type="image/svg+xml" />
        <link rel="apple-touch-icon" href="/icon-192x192.png" />
      </head>
      <body className={inter.className}>
        <UltimateModeProvider>
          {children}
        </UltimateModeProvider>
        
        {/* Service Worker登録（本番環境のみ） */}
        <Script id="register-sw" strategy="afterInteractive">
          {`
            if ('serviceWorker' in navigator && window.location.hostname !== 'localhost') {
              window.addEventListener('load', function() {
                navigator.serviceWorker.register('/sw.js').then(
                  function(registration) {
                    console.log('Service Worker登録成功:', registration.scope);
                  },
                  function(err) {
                    console.log('Service Worker登録失敗:', err);
                  }
                );
              });
            }
          `}
        </Script>
      </body>
    </html>
  )
}
