const LOCAL_ENVIRONMENTS = new Set(['local', 'development', 'dev', 'test', 'ci'])

export function explicitLocalOptInEnabled(
  flagName: string,
  acceptedValues: ReadonlySet<string> = new Set(['true']),
): boolean {
  const environment = (process.env.APP_ENV || '').trim().toLowerCase()
  const value = (process.env[flagName] || '').trim().toLowerCase()
  return LOCAL_ENVIRONMENTS.has(environment) && acceptedValues.has(value)
}
