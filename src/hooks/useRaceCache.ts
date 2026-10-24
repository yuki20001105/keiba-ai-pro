'use client'
import { useRef, useCallback } from 'react'
import type { CacheEntry, FeatureData } from '@/lib/race-analysis-types'
import { PREDICTION_CACHE_TTL_MS, predictionStorageKey, readPredictionCache, writePredictionCache } from '@/lib/prediction-freshness'

/** 全レースの表示キャッシュTTL。DB更新・モデル更新は別途revisionで検査する。 */
export const CACHE_TTL_MS = PREDICTION_CACHE_TTL_MS

/**
 * ユーザー別の表示キャッシュ。購入・成績記録の正本ではない。
 * includeExpired は「保存予測」と明示する読み取り専用表示だけに使う。
 * 容量超過でも他のレース・ユーザーの保存データを一括削除しない。
 */
export function useRaceCache(userId?: string | null) {
  const mem = useRef<Map<string, CacheEntry>>(new Map())

  const _readLS = useCallback((key: string): CacheEntry | null => {
    try {
      const parsed = readPredictionCache(key, userId)
      if (!parsed) return null
      return { predictResult: parsed.data, featData: null, cachedAt: parsed.cachedAt, ownerMatches: parsed.ownerMatches }
    } catch {
      return null
    }
  }, [userId])

  /**
   * @param cacheKey    ra-cache キー（raceId または raceId__modelId）
   * explicit model keys never fall back to a different/default model.
   */
  const get = useCallback((cacheKey: string, _allowFallback = false, includeExpired = false): CacheEntry | null => {
    const scopedKey = predictionStorageKey(cacheKey, userId)
    const valid = (entry: CacheEntry) => includeExpired || Number.isFinite(entry.cachedAt)
      && entry.cachedAt <= Date.now() && Date.now() - entry.cachedAt < CACHE_TTL_MS
    // L1: インメモリ
    const memHit = mem.current.get(scopedKey)
    if (memHit) return valid(memHit) ? memHit : null

    // L2: localStorage
    const lsHit = _readLS(cacheKey)
    if (lsHit) {
      mem.current.set(scopedKey, lsHit)
      return valid(lsHit) ? lsHit : null
    }

    return null
  }, [_readLS, userId])

  /** インメモリ + localStorage に永続化 */
  const set = useCallback((cacheKey: string, entry: CacheEntry) => {
    mem.current.set(predictionStorageKey(cacheKey, userId), { ...entry, ownerMatches: !!userId })
    writePredictionCache(cacheKey, entry.predictResult, entry.cachedAt, userId)
  }, [userId])

  const updateFeat = useCallback((cacheKey: string, featData: FeatureData) => {
    const key = predictionStorageKey(cacheKey, userId)
    const existing = mem.current.get(key)
    if (existing) mem.current.set(key, { ...existing, featData })
  }, [userId])

  return { get, set, updateFeat }
}
