import { isLoopbackRequest, isLoopbackUrl } from '@/lib/legacy-local-policy'

const LOCAL_ENVIRONMENTS = new Set(['local', 'development', 'test'])
const DEPLOYMENT_MARKERS = ['VERCEL', 'VERCEL_ENV', 'NETLIFY', 'CONTEXT', 'RENDER', 'RAILWAY_ENVIRONMENT', 'FLY_APP_NAME']

/** This policy never authenticates a user: apply it only after current JWT and role verification. */
export function usesLocalAdminSession(request: Request): boolean {
  if (process.env.LOCAL_ADMIN_SESSION_ENABLED !== 'true') return false
  if (!LOCAL_ENVIRONMENTS.has((process.env.APP_ENV || '').trim().toLowerCase())) return false
  if (DEPLOYMENT_MARKERS.some(name => Boolean(process.env[name]))) return false
  if (!isLoopbackUrl(process.env.ML_API_URL || '') || !isLoopbackUrl(process.env.SCRAPE_API_URL || '')) return false
  if (!isLoopbackRequest(request)) return false

  // A public reverse proxy must not inherit the loopback upstream's local policy.
  if (request.headers.has('forwarded')) return false
  const requestUrl = new URL(request.url)
  const host = request.headers.get('host')?.trim().toLowerCase() ?? requestUrl.host.toLowerCase()
  try {
    // Next may normalize 127.0.0.1 to localhost in request.url. Accept only
    // those already-verified loopback aliases, never a different public port.
    if (!isLoopbackUrl(`${requestUrl.protocol}//${host}`)) return false
    if (new URL(`${requestUrl.protocol}//${host}`).port !== requestUrl.port) return false
  } catch {
    return false
  }
  const forwardedHost = request.headers.get('x-forwarded-host')
  if (forwardedHost !== null) {
    // Next itself supplies this header from Host for a direct local request.
    if (forwardedHost.trim().toLowerCase() !== host) return false
  }
  return true
}
