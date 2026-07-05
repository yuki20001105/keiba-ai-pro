export type RouteClass = 'production' | 'experimental' | 'internal' | 'deprecated' | 'unused'

export type NextRouteMeta = {
  route: string
  classification: RouteClass
  note: string
}

// Lightweight metadata for operational inventory. Keep in sync with docs/api_route_inventory.md.
export const NEXT_API_ROUTE_CLASSIFICATION: NextRouteMeta[] = [
  { route: '/api/health', classification: 'production', note: 'App heartbeat proxy' },
  { route: '/api/scrape/health', classification: 'production', note: 'Dedicated scrape health contract' },
  { route: '/api/scrape/status/[jobId]', classification: 'production', note: 'Scrape job polling' },
  { route: '/api/scrape', classification: 'deprecated', note: 'Compatibility alias to async scrape start' },
  { route: '/api/netkeiba/race', classification: 'experimental', note: 'Mixed external scrape + Supabase write path' },
  { route: '/api/stripe/webhook', classification: 'internal', note: 'Server-to-server callback only' },
  { route: '/api/data/all', classification: 'internal', note: 'Destructive admin utility route' },
  { route: '/api/features/catalog', classification: 'unused', note: 'Available route, no current UI caller' },
]
