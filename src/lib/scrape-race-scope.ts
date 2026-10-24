export type ScrapeRaceScope = 'jra' | 'all'

export function isScrapeRaceScope(value: unknown): value is ScrapeRaceScope {
  return value === 'jra' || value === 'all'
}

export function scrapeRaceScopeLabel(scope: ScrapeRaceScope): string {
  return scope === 'jra' ? '中央競馬のみ' : '中央・地方競馬'
}

/** A healthy old server can silently ignore race_scope; require explicit support. */
export function supportsJraScrape(value: unknown): boolean {
  if (typeof value !== 'object' || value === null || Array.isArray(value)) return false
  const capabilities = (value as Record<string, unknown>).scrape_capabilities
  if (typeof capabilities !== 'object' || capabilities === null || Array.isArray(capabilities)) return false
  const fields = capabilities as Record<string, unknown>
  return Array.isArray(fields.race_scopes) && fields.race_scopes.includes('jra')
    && fields.jra_month_reuse === true
}
