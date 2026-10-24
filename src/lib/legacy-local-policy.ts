const LOCAL_ENVIRONMENTS = new Set(['local', 'development', 'dev', 'test', 'ci'])
const LOOPBACK_HOSTS = new Set(['localhost', '127.0.0.1', '::1', '[::1]'])

export function explicitLocalOptInEnabled(
  flagName: string,
  acceptedValues: ReadonlySet<string> = new Set(['true']),
): boolean {
  const environment = (process.env.APP_ENV || '').trim().toLowerCase()
  const value = (process.env[flagName] || '').trim().toLowerCase()
  return LOCAL_ENVIRONMENTS.has(environment) && acceptedValues.has(value)
}

export function isLoopbackRequest(request: Request): boolean {
  try {
    const urlHost = new URL(request.url).hostname.toLowerCase()
    if (!LOOPBACK_HOSTS.has(urlHost)) return false

    const header = request.headers.get('host')?.trim().toLowerCase()
    if (!header) return true
    if (LOOPBACK_HOSTS.has(header)) return true
    const headerHost = header.startsWith('[')
      ? header.slice(0, header.indexOf(']') + 1)
      : header.split(':', 1)[0]
    return LOOPBACK_HOSTS.has(headerHost)
  } catch {
    return false
  }
}

export function isLoopbackUrl(value: string): boolean {
  try {
    const url = new URL(value)
    return (url.protocol === 'http:' || url.protocol === 'https:')
      && url.username === ''
      && url.password === ''
      && LOOPBACK_HOSTS.has(url.hostname.toLowerCase())
      && (url.pathname === '' || url.pathname === '/')
      && url.search === ''
      && url.hash === ''
  } catch {
    return false
  }
}
