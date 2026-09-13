import { readFileSync } from 'node:fs'
import path from 'node:path'
import { describe, expect, it } from 'vitest'

describe('local launcher backend-only restart', () => {
  it('waits for FastAPI before checking the existing frontend health proxy', () => {
    const script = readFileSync(path.join(process.cwd(), 'scripts/start-local-app.ps1'), 'utf8')
    const apiReady = script.indexOf("Wait-Endpoint -Name 'FastAPI'")
    const frontendCheck = script.indexOf('Assert-PortAvailableOrHealthy -Port 3000')
    const frontendReady = script.indexOf("Wait-Endpoint -Name 'Next.js'")

    expect(apiReady).toBeGreaterThanOrEqual(0)
    expect(frontendCheck).toBeGreaterThan(apiReady)
    expect(frontendReady).toBeGreaterThan(frontendCheck)
  })

  it('opts into session-based local admin screens only alongside loopback services', () => {
    const script = readFileSync(path.join(process.cwd(), 'scripts/start-local-app.ps1'), 'utf8')
    expect(script).toContain("$env:APP_ENV = 'development'")
    expect(script).toContain("$env:LOCAL_ADMIN_SESSION_ENABLED = 'true'")
    expect(script).toContain("$env:API_HOST = '127.0.0.1'")
    expect(script).toContain("$env:ML_API_URL = 'http://127.0.0.1:8000'")
    expect(script).toContain("$env:SCRAPE_API_URL = 'http://127.0.0.1:8000'")
  })
})
