'use client'

import { useEffect, useState } from 'react'

/** Expire displayed offers while the page stays open, without making a new prediction. */
export function useBettingClock(enabled = true): number {
  const [now, setNow] = useState(() => Date.now())
  useEffect(() => {
    if (!enabled) return
    const update = () => setNow(Date.now())
    update()
    const timer = window.setInterval(update, 1000)
    window.addEventListener('focus', update)
    document.addEventListener('visibilitychange', update)
    return () => {
      window.clearInterval(timer)
      window.removeEventListener('focus', update)
      document.removeEventListener('visibilitychange', update)
    }
  }, [enabled])
  return now
}
