import { timingSafeEqual } from 'node:crypto'
import { readFile } from 'node:fs/promises'
import { join } from 'node:path'
import { createServerClient } from '@supabase/ssr'
import { NextRequest, NextResponse } from 'next/server'

type LocalLoginConfig = {
  E2E_EMAIL?: string
  E2E_PASSWORD?: string
  LOCAL_AUTO_LOGIN_TOKEN?: string
}

function getLoopbackOrigin(request: NextRequest): string | null {
  const host = request.headers.get('host')?.toLowerCase() ?? ''
  if (!/^(127\.0\.0\.1|localhost)(:\d+)?$/.test(host)) return null
  return `http://${host}`
}

function parseDotEnv(source: string): LocalLoginConfig {
  const values: Record<string, string> = {}
  for (const rawLine of source.split(/\r?\n/)) {
    const line = rawLine.trim()
    if (!line || line.startsWith('#')) continue
    const separator = line.indexOf('=')
    if (separator <= 0) continue
    const name = line.slice(0, separator).trim()
    let value = line.slice(separator + 1).trim()
    if ((value.startsWith('"') && value.endsWith('"'))
      || (value.startsWith("'") && value.endsWith("'"))) {
      value = value.slice(1, -1)
    }
    values[name] = value
  }
  return values
}

function secretsMatch(actual: string, expected: string): boolean {
  const actualBytes = Buffer.from(actual)
  const expectedBytes = Buffer.from(expected)
  return actualBytes.length === expectedBytes.length
    && timingSafeEqual(actualBytes, expectedBytes)
}

function fail(request: NextRequest, reason: string, status = 403) {
  if (status === 404) {
    return new NextResponse(null, { status: 404 })
  }
  const origin = getLoopbackOrigin(request) ?? 'http://127.0.0.1:3000'
  const loginUrl = new URL('/login', origin)
  loginUrl.searchParams.set('local_auto_login', reason)
  const response = NextResponse.redirect(loginUrl)
  response.headers.set('Cache-Control', 'no-store')
  response.headers.set('Referrer-Policy', 'no-referrer')
  return response
}

export async function GET(request: NextRequest) {
  if (process.env.APP_ENV !== 'development') return fail(request, 'disabled', 404)
  const loopbackOrigin = getLoopbackOrigin(request)
  if (!loopbackOrigin) {
    return fail(request, 'loopback_required')
  }

  const configRoot = process.env.LOCAL_CONFIGURATION_ROOT
  const supabaseUrl = process.env.NEXT_PUBLIC_SUPABASE_URL
  const supabaseAnonKey = process.env.NEXT_PUBLIC_SUPABASE_ANON_KEY
  if (!configRoot || !supabaseUrl || !supabaseAnonKey) return fail(request, 'configuration_missing', 503)

  let config: LocalLoginConfig
  try {
    config = parseDotEnv(await readFile(join(configRoot, '.env.test'), 'utf8'))
  } catch {
    return fail(request, 'test_credentials_missing', 503)
  }

  const suppliedToken = request.nextUrl.searchParams.get('token') ?? ''
  const expectedToken = config.LOCAL_AUTO_LOGIN_TOKEN ?? ''
  if (!suppliedToken || !expectedToken || !secretsMatch(suppliedToken, expectedToken)) {
    return fail(request, 'token_rejected')
  }
  if (!config.E2E_EMAIL || !config.E2E_PASSWORD) return fail(request, 'credentials_missing', 503)

  const homeUrl = new URL('/home', loopbackOrigin)
  const response = NextResponse.redirect(homeUrl)
  response.headers.set('Cache-Control', 'no-store')
  response.headers.set('Referrer-Policy', 'no-referrer')

  const supabase = createServerClient(supabaseUrl, supabaseAnonKey, {
    cookies: {
      getAll: () => request.cookies.getAll(),
      setAll: (cookies) => {
        cookies.forEach(({ name, value, options }) => response.cookies.set(name, value, options))
      },
    },
  })
  const { error } = await supabase.auth.signInWithPassword({
    email: config.E2E_EMAIL,
    password: config.E2E_PASSWORD,
  })
  if (error) return fail(request, 'authentication_failed', 401)
  return response
}
