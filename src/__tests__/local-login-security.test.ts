import { describe, expect, it } from 'vitest'
import { readFileSync } from 'node:fs'

describe('local auto-login security boundary', () => {
  it('is development-only, loopback-only, token-gated, and server-side', () => {
    const source = readFileSync('src/app/api/local-login/route.ts', 'utf8')

    expect(source).toContain("process.env.APP_ENV !== 'development'")
    expect(source).toContain("/^(127\\.0\\.0\\.1|localhost)(:\\d+)?$/")
    expect(source).toContain('timingSafeEqual')
    expect(source).toContain("join(configRoot, '.env.test')")
    expect(source).toContain('Referrer-Policy')
    expect(source).not.toContain('NEXT_PUBLIC_E2E_PASSWORD')
  })
})
