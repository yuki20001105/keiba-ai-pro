'use client'
import { useRef, useCallback } from 'react'
import type { CacheEntry, RacePredictResult, FeatureData } from '@/lib/race-analysis-types'

/** 当日レースのキャッシュTTL: 30分（オッズ変動に追従するため） */
export const CACHE_TTL_MS = 30 * 60 * 1000

/**
 * race_info.date (YYYYMMDD) が今日以降かどうかを判定する。
 *   過去レース (date < today): 永続キャッシュ — オッズも予測確率も変わらない
 *   当日レース (date >= today): TTL 30分 — オッズがリアルタイムで動くため
 */
function _isRaceTodayOrFuture(raceDate: string): boolean {
  const today = new Date()
  const todayStr = `${today.getFullYear()}${String(today.getMonth() + 1).padStart(2, '0')}${String(today.getDate()).padStart(2, '0')}`
  return raceDate >= todayStr
}

/** 今日の YYYYMMDD 文字列を返す */
function _todayStr(): string {
  const today = new Date()
  return `${today.getFullYear()}${String(today.getMonth() + 1).padStart(2, '0')}${String(today.getDate()).padStart(2, '0')}`
}

/**
 * レース予測データのキャッシュ管理フック。
 *
 * レイヤー1: インメモリ Map（同セッション内の再選択で即返却）
 * レイヤー2: localStorage（ページ遷移後も利用可能）
 *
 * TTL 戦略:
 *   過去レース → 永続（削除しない）
 *   当日レース → TTL 30分（オッズ変動対応）
 *
 * キャッシュキー設計:
 *   モデル指定あり: ra-cache:{raceId}__{modelId}
 *   モデル指定なし: ra-cache:{raceId}
 *
 * fallback ポリシー:
 *   モデルB 指定時にモデルA のキャッシュを返す誤表示を防ぐため、
 *   fallback は「最新モデル自動選択」時のみ許可（allowFallback=true）。
 */
export function useRaceCache() {
  const mem = useRef<Map<string, CacheEntry>>(new Map())

  const _readLS = useCallback((key: string): CacheEntry | null => {
    try {
      const raw = localStorage.getItem(`ra-cache:${key}`)
      if (!raw) return null
      const parsed = JSON.parse(raw) as { data: RacePredictResult; cachedAt: number }

      const raceDate = parsed.data?.race_info?.date ?? ''
      const isTodayOrFuture = !raceDate || _isRaceTodayOrFuture(raceDate)

      // 過去レースは TTL 無効（永続）。当日レースのみ 30 分 TTL を適用。
      if (isTodayOrFuture && Date.now() - parsed.cachedAt > CACHE_TTL_MS) {
        localStorage.removeItem(`ra-cache:${key}`)
        return null
      }

      return { predictResult: parsed.data, featData: null, cachedAt: parsed.cachedAt }
    } catch {
      return null
    }
  }, [])

  /**
   * @param cacheKey    ra-cache キー（raceId または raceId__modelId）
   * @param allowFallback  true の時のみ raceId 単体キーへの fallback を許可
   *                       （「最新モデル自動選択」時のみ true を渡すこと）
   */
  const get = useCallback((cacheKey: string, allowFallback = false): CacheEntry | null => {
    // L1: インメモリ
    const memHit = mem.current.get(cacheKey)
    if (memHit) return memHit

    // L2: localStorage
    const lsHit = _readLS(cacheKey)
    if (lsHit) {
      mem.current.set(cacheKey, lsHit)
      return lsHit
    }

    // Fallback: モデル自動選択時のみ raceId 単体キーを参照
    if (allowFallback && cacheKey.includes('__')) {
      const raceId = cacheKey.split('__')[0]
      const fallback = _readLS(raceId)
      if (fallback) {
        mem.current.set(cacheKey, fallback)
        return fallback
      }
    }
    return null
  }, [_readLS])

  /** インメモリ + localStorage に永続化 */
  const set = useCallback((cacheKey: string, entry: CacheEntry) => {
    mem.current.set(cacheKey, entry)
    const payload = JSON.stringify({ data: entry.predictResult, cachedAt: entry.cachedAt })
    try {
      localStorage.setItem(`ra-cache:${cacheKey}`, payload)
    } catch {
      // localStorage 容量超過時の回復フロー:
      // ステップ1: 当日レースのキャッシュのみ削除（過去レースを保護）
      try {
        const today = _todayStr()
        Object.keys(localStorage)
          .filter(k => k.startsWith('ra-cache:'))
          .forEach(k => {
            try {
              const d = JSON.parse(localStorage.getItem(k) ?? '{}')
              const rd: string = d?.data?.race_info?.date ?? ''
              if (!rd || rd >= today) localStorage.removeItem(k)
            } catch { localStorage.removeItem(k) }
          })
        localStorage.setItem(`ra-cache:${cacheKey}`, payload)
      } catch {
        // ステップ2: 全てのキャッシュを削除して再試行
        try {
          Object.keys(localStorage)
            .filter(k => k.startsWith('ra-cache:'))
            .forEach(k => localStorage.removeItem(k))
          localStorage.setItem(`ra-cache:${cacheKey}`, payload)
        } catch { /* インメモリのみ保持 */ }
      }
    }
  }, [])

  const updateFeat = useCallback((cacheKey: string, featData: FeatureData) => {
    const existing = mem.current.get(cacheKey)
    if (existing) mem.current.set(cacheKey, { ...existing, featData })
  }, [])

  return { get, set, updateFeat }
}
