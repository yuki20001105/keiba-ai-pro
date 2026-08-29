import { describe, expect, it } from 'vitest'
import { formatApiErrorDetail } from '@/lib/api-error'

describe('formatApiErrorDetail', () => {
  it('formats FastAPI validation arrays without object coercion', () => {
    expect(formatApiErrorDetail([
      { loc: ['body', 'start_date'], msg: 'Field required', type: 'missing' },
      { loc: ['body', 'end_date'], msg: 'Invalid date', type: 'value_error' },
    ])).toBe('body.start_date: Field required; body.end_date: Invalid date')
  })

  it('unwraps nested detail and bounds fallback objects', () => {
    expect(formatApiErrorDetail({ detail: { reason: 'runtime disabled' } })).toBe('runtime disabled')
    expect(formatApiErrorDetail({ code: 'invalid' })).toBe('{"code":"invalid"}')
  })
})
