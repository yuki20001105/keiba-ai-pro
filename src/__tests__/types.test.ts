/**
 * types.ts のユーティリティ関数・定数のユニットテスト
 *
 * 実行:
 *   npm test
 */
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest'
import { todayStr, toInputDate, fromInputDate, JRA_VENUES } from '@/lib/types'

// ─────────────────────────────────────────────────────────
// todayStr
// ─────────────────────────────────────────────────────────
describe('todayStr', () => {
  afterEach(() => {
    vi.useRealTimers()
  })

  it('YYYYMMDD 形式（8文字・数字のみ）', () => {
    const s = todayStr()
    expect(s).toMatch(/^\d{8}$/)
  })

  it('固定日付で正しいフォーマットを返す', () => {
    vi.setSystemTime(new Date('2026-04-11'))
    expect(todayStr()).toBe('20260411')
  })

  it('月・日が 1 桁のとき 0 パディングされる', () => {
    vi.setSystemTime(new Date('2026-01-05'))
    expect(todayStr()).toBe('20260105')
  })

  it('12月31日も正しく処理する', () => {
    vi.setSystemTime(new Date('2025-12-31'))
    expect(todayStr()).toBe('20251231')
  })
})

// ─────────────────────────────────────────────────────────
// toInputDate
// ─────────────────────────────────────────────────────────
describe('toInputDate', () => {
  it('YYYYMMDD を YYYY-MM-DD に変換', () => {
    expect(toInputDate('20260411')).toBe('2026-04-11')
  })

  it('月・日が 0 パディングされた値も変換', () => {
    expect(toInputDate('20260105')).toBe('2026-01-05')
  })

  it('変換結果が HTML date input 形式に一致', () => {
    // <input type="date"> の value は YYYY-MM-DD
    expect(toInputDate('20251231')).toBe('2025-12-31')
  })
})

// ─────────────────────────────────────────────────────────
// fromInputDate
// ─────────────────────────────────────────────────────────
describe('fromInputDate', () => {
  it('YYYY-MM-DD を YYYYMMDD に変換', () => {
    expect(fromInputDate('2026-04-11')).toBe('20260411')
  })

  it('toInputDate の逆変換', () => {
    const original = '20260411'
    expect(fromInputDate(toInputDate(original))).toBe(original)
  })

  it('ハイフンなしの入力はそのまま返る', () => {
    expect(fromInputDate('20260411')).toBe('20260411')
  })
})

// ─────────────────────────────────────────────────────────
// JRA_VENUES
// ─────────────────────────────────────────────────────────
describe('JRA_VENUES', () => {
  it('JRA 10場が定義されている', () => {
    expect(JRA_VENUES).toHaveLength(10)
  })

  it('code は "01"〜"10" の連番', () => {
    const codes = JRA_VENUES.map(v => v.code)
    const expected = Array.from({ length: 10 }, (_, i) =>
      String(i + 1).padStart(2, '0')
    )
    expect(codes).toEqual(expected)
  })

  it('code はすべてユニーク', () => {
    const codes = JRA_VENUES.map(v => v.code)
    expect(new Set(codes).size).toBe(codes.length)
  })

  it('name はすべてユニーク', () => {
    const names = JRA_VENUES.map(v => v.name)
    expect(new Set(names).size).toBe(names.length)
  })

  it('東京は code="05"', () => {
    const tokyo = JRA_VENUES.find(v => v.name === '東京')
    expect(tokyo?.code).toBe('05')
  })

  it('中山は code="06"', () => {
    const nakayama = JRA_VENUES.find(v => v.name === '中山')
    expect(nakayama?.code).toBe('06')
  })

  it('全要素が code と name プロパティを持つ', () => {
    for (const v of JRA_VENUES) {
      expect(v).toHaveProperty('code')
      expect(v).toHaveProperty('name')
      expect(typeof v.code).toBe('string')
      expect(typeof v.name).toBe('string')
    }
  })
})
