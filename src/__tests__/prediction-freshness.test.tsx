import { act, cleanup, renderHook } from '@testing-library/react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { useRaceCache, CACHE_TTL_MS } from '@/hooks/useRaceCache'
import type { RacePredictResult } from '@/lib/race-analysis-types'
import { predictionCacheStatus, predictionStorageKey, readPredictionCache, savedPredictionForDisplay, writePredictionCache } from '@/lib/prediction-freshness'
import { freshPrediction } from './helpers/prediction-freshness'

const NOW = Date.parse('2026-10-12T10:00:00Z')
const prediction = { success: true, model_id: 'current', freshness: freshPrediction,
  race_info: { date: '20160101' }, predictions: [], performance_snapshot_id: 'sealed-old',
  reference_expectation: { schema: 'calibrated-win-reference-v1' }, recommendation: {
    decision: 'buy', action: '購入候補', reason: '購入理由', input_verified: true, purchase_count: 1, unit_price: 100,
    total_cost: 100, expected_return: 200, tickets: [{ stake: 100 }],
  } } as unknown as RacePredictResult

beforeEach(() => { localStorage.clear(); vi.useFakeTimers(); vi.setSystemTime(NOW) })
afterEach(() => { cleanup(); vi.useRealTimers() })

describe('prediction cache freshness and ownership', () => {
  it('needs exact DB, model, owner and age evidence to regard a saved result as current', () => {
    expect(predictionCacheStatus(prediction, freshPrediction, NOW, true)).toBe('current')
  })
  it.each(['data_revision', 'model_runtime_revision', 'resolved_model_id'] as const)('rejects changed %s', field => {
    expect(predictionCacheStatus(prediction, { ...freshPrediction, [field]: 'changed' }, NOW, true)).toBe('stale')
  })
  it.each([null, undefined, {}])('does not consider legacy/unknown metadata current: %o', freshness => {
    expect(predictionCacheStatus({ ...prediction, freshness } as RacePredictResult, freshPrediction, NOW, true)).toBe('unverified')
  })
  it('does not confer authority while server verification is offline', () => {
    expect(predictionCacheStatus(prediction, null, NOW, true)).toBe('unverified')
  })
  it('does not confer authority across owners', () => {
    expect(predictionCacheStatus(prediction, freshPrediction, NOW, false)).toBe('unverified')
  })
  it('does not regard conservative file-metadata fallback as verified current data', () => {
    expect(predictionCacheStatus(prediction, { ...freshPrediction, source_tracking: false, revision_kind: 'sqlite-file-version' }, NOW, true)).toBe('unverified')
    expect(predictionCacheStatus({ ...prediction, freshness: { ...freshPrediction, source_tracking: false } }, freshPrediction, NOW, true)).toBe('unverified')
  })
  it.each([NOW - CACHE_TTL_MS, NOW + 1, Number.NaN])('expires even a historical race at boundary %o', cachedAt => {
    expect(predictionCacheStatus(prediction, freshPrediction, cachedAt, true)).toBe('stale')
  })
  it('rejects data altered during prediction', () => {
    expect(predictionCacheStatus({ ...prediction, freshness: { ...freshPrediction, data_changed_during_prediction: true } }, freshPrediction, NOW, true)).toBe('stale')
  })
  it('preserves saved rankings but strips stale EV, spending and record authority without mutating the archive', () => {
    const before = JSON.stringify(prediction)
    const display = savedPredictionForDisplay(prediction, 'stale')
    expect(display).toMatchObject({ performance_snapshot_id: null, reference_expectation: null,
      recommendation: { decision: 'skip', input_verified: false, tickets: [], total_cost: 0 } })
    expect(JSON.stringify(prediction)).toBe(before)
  })
  it('never uses even a current restored snapshot for a new actual purchase recording', () => {
    expect(savedPredictionForDisplay(prediction, 'current').performance_snapshot_id).toBeNull()
  })
  it('partitions display caches by owner and does not retrieve another owner', () => {
    writePredictionCache('race', prediction, NOW, 'owner-a')
    expect(readPredictionCache('race', 'owner-b')).toBeNull()
    expect(readPredictionCache('race', 'owner-a')?.ownerMatches).toBe(true)
  })
  it('discards cross-owner legacy entries instead of displaying their snapshot', () => {
    localStorage.setItem('ra-cache:race', JSON.stringify({ data: prediction, cachedAt: NOW, owner_user_id: 'owner-a' }))
    expect(readPredictionCache('race', 'owner-b')).toBeNull()
  })
  it('keeps owner-unknown legacy predictions display-only', () => {
    localStorage.setItem('ra-cache:race', JSON.stringify({ data: prediction, cachedAt: NOW }))
    expect(readPredictionCache('race', 'owner-a')?.ownerMatches).toBe(false)
  })
  it('applies TTL to both memory and localStorage without deleting previous results', () => {
    const { result } = renderHook(() => useRaceCache('owner-a'))
    act(() => { result.current.set('race', { predictResult: prediction, cachedAt: NOW, featData: null }) })
    expect(result.current.get('race')).not.toBeNull()
    act(() => { vi.advanceTimersByTime(CACHE_TTL_MS) })
    expect(result.current.get('race')).toBeNull()
    expect(result.current.get('race', false, true)).not.toBeNull()
    expect(localStorage.getItem(predictionStorageKey('race', 'owner-a'))).not.toBeNull()
    const second = renderHook(() => useRaceCache('owner-a'))
    expect(second.result.current.get('race')).toBeNull()
  })
  it('never falls back from an explicit model key to another model/default prediction', () => {
    writePredictionCache('race', prediction, NOW, 'owner-a')
    const { result } = renderHook(() => useRaceCache('owner-a'))
    expect(result.current.get('race__different', true)).toBeNull()
  })
  it('does not clear unrelated cached predictions/records on quota failures', () => {
    localStorage.setItem('ra-cache:other', 'keep')
    localStorage.setItem('paper-record', 'sealed')
    const { result } = renderHook(() => useRaceCache('owner-a'))
    const spy = vi.spyOn(Storage.prototype, 'setItem').mockImplementation(() => { throw new Error('QuotaExceeded') })
    act(() => { result.current.set('race', { predictResult: prediction, cachedAt: NOW, featData: null }) })
    expect(result.current.get('race')).not.toBeNull()
    expect(localStorage.getItem('ra-cache:other')).toBe('keep')
    expect(localStorage.getItem('paper-record')).toBe('sealed')
    spy.mockRestore()
  })
})
