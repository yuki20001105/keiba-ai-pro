import { describe, expect, it } from 'vitest'
import { readFileSync } from 'node:fs'

describe('AuthProvider auth-lock guard', () => {
  it('defers role refresh outside the Supabase auth-state callback', () => {
    const source = readFileSync('src/contexts/AuthContext.tsx', 'utf8')
    const callbackStart = source.indexOf('supabase.auth.onAuthStateChange')
    const callback = source.slice(callbackStart, source.indexOf('return () =>', callbackStart))

    expect(callback).toContain('setTimeout')
    expect(callback).toContain('void fetchRole()')
    expect(callback.indexOf('setTimeout')).toBeLessThan(callback.indexOf('void fetchRole()'))
  })
})
