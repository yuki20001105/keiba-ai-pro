import { readFileSync } from 'node:fs'
import path from 'node:path'
import { spawnSync } from 'node:child_process'
import { describe, expect, it } from 'vitest'

describe('local launcher backend-only restart', () => {
  it.runIf(process.platform === 'win32').each([
    [{ status: 'ok' }, 'False'],
    [{ status: 'ok', recommendation_runtime: { ready: false, supported_strategies: [] } }, 'False'],
    [{ status: 'ok', recommendation_runtime: { ready: true, supported_strategies: ['value'] } }, 'False'],
    [{ status: 'ok', recommendation_runtime: { ready: true, supported_strategies: ['value', 'fixed-two-v1'] } }, 'True'],
  ])('does not reuse an API without both loaded betting strategies: %j', (health, expected) => {
    const result = spawnSync('powershell.exe', ['-NoProfile', '-NonInteractive', '-Command', `
      $tokens = $null; $errors = $null
      $ast = [System.Management.Automation.Language.Parser]::ParseFile(
        (Join-Path $PWD 'scripts\\start-local-app.ps1'), [ref]$tokens, [ref]$errors)
      $fn = $ast.Find({ param($node)
        $node -is [System.Management.Automation.Language.FunctionDefinitionAst] -and
        $node.Name -eq 'Test-PredictionRecommendationRuntimeReady'
      }, $true)
      if ($null -eq $fn) { Write-Output $false; exit 0 }
      Invoke-Expression $fn.Extent.Text
      function Get-ApiHealthPayload { $env:FIXTURE_HEALTH | ConvertFrom-Json }
      Test-PredictionRecommendationRuntimeReady
    `], { encoding: 'utf8', env: { ...process.env, FIXTURE_HEALTH: JSON.stringify(health) } })
    expect(result.status).toBe(0)
    expect(result.stdout.trim()).toBe(expected)
  })

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

  it('does not reuse a healthy frontend unless the local-login handler is ready', () => {
    const script = readFileSync(path.join(process.cwd(), 'scripts/start-local-app.ps1'), 'utf8')
    expect(script).toContain('function Test-LocalLoginRouteReady')
    expect(script).toContain('function Find-RepositoryWebProcess')
    expect(script).toContain('(Test-LocalLoginRouteReady)')
    expect(script).toContain('Restarting this repository Next.js because its local-login route is disabled or stale')
    expect(script).toContain('port owner is not a verified Next.js process from this repository. It was left untouched')
    expect(script).toContain('No existing process was stopped.')
  })
})
