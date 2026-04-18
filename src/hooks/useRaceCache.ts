'use client'
import { useRef, useCallback } from 'react'
import type { CacheEntry, RacePredictResult, FeatureData } from '@/lib/race-analysis-types'

const CACHE_TTL_MS = 5 * 60 * 1000

/**
 * レース予測データのキャッシュ管理フック。
 * - レイヤー1: インメモリ Map（同セッション内の再選択で即返却）
 * - レイヤー2: localStorage（予測実行ページで書き込まれたデータ、TTL 5分）
 */
export function useRaceCache() {
  const mem = useRef<Map<string, CacheEntry>>(new Map())

  const get = useCallback((raceId: string): CacheEntry | null => {
    const memHit = mem.current.get(raceId)
    if (memHit) return memHit

    try {
      const raw = localStorage.getItem(`ra-cache:${raceId}`)
      if (!raw) return null
      const parsed = JSON.parse(raw) as { data: RacePredictResult; cachedAt: number }
      if (Date.now() - parsed.cachedAt > CACHE_TTL_MS) return null
      const entry: CacheEntry = { predictResult: parsed.data, featData: null, cachedAt: parsed.cachedAt }
      mem.current.set(raceId, entry)
      return entry
    } catch {
      return null
    }
  }, [])

  const set = useCallback((raceId: string, entry: CacheEntry) => {
    mem.current.set(raceId, entry)
  }, [])

  const updateFeat = useCallback((raceId: string, featData: FeatureData) => {
    const existing = mem.current.get(raceId)
    if (existing) mem.current.set(raceId, { ...existing, featData })
  }, [])

  return { get, set, updateFeat }
}
