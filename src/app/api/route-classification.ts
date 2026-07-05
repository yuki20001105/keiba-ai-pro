export type RouteClass = 'production' | 'experimental' | 'internal' | 'deprecated' | 'unused'
export type MigrationTarget = 'keep-next-direct' | 'fastapi-proxy' | 'fastapi-owned-write' | 'undecided'
export type RiskLevel = 'low' | 'medium' | 'high'

export type NextRouteMeta = {
  route: string
  classification: RouteClass
  note: string
  usesSupabaseDirectly?: boolean
  usesScrapeServiceDirectly?: boolean
  migrationTarget?: MigrationTarget
  riskLevel?: RiskLevel
}

// Lightweight metadata for operational inventory. Keep in sync with docs/api_route_inventory.md.
export const NEXT_API_ROUTE_CLASSIFICATION: NextRouteMeta[] = [
  { route: '/api/health', classification: 'production', note: 'App heartbeat proxy', migrationTarget: 'keep-next-direct', riskLevel: 'low' },
  { route: '/api/scrape/health', classification: 'production', note: 'Dedicated scrape health contract', migrationTarget: 'keep-next-direct', riskLevel: 'low' },
  { route: '/api/scrape/status/[jobId]', classification: 'production', note: 'Scrape job polling', migrationTarget: 'keep-next-direct', riskLevel: 'low' },
  { route: '/api/scrape', classification: 'deprecated', note: 'Compatibility alias to async scrape start', migrationTarget: 'fastapi-proxy', riskLevel: 'medium' },
  {
    route: '/api/netkeiba/race',
    classification: 'experimental',
    note: 'Mixed external scrape + Supabase write path',
    usesSupabaseDirectly: true,
    usesScrapeServiceDirectly: true,
    migrationTarget: 'fastapi-owned-write',
    riskLevel: 'high',
  },
  {
    route: '/api/netkeiba/race-list',
    classification: 'experimental',
    note: 'Read-only scrape service direct call',
    usesScrapeServiceDirectly: true,
    migrationTarget: 'fastapi-proxy',
    riskLevel: 'medium',
  },
  {
    route: '/api/ocr',
    classification: 'experimental',
    note: 'OCR usage writes profiles and ocr_usage via Supabase client',
    usesSupabaseDirectly: true,
    migrationTarget: 'fastapi-owned-write',
    riskLevel: 'medium',
  },
  { route: '/api/stripe/webhook', classification: 'internal', note: 'Server-to-server callback only', migrationTarget: 'keep-next-direct', riskLevel: 'low' },
  { route: '/api/data/all', classification: 'internal', note: 'Destructive admin utility route', migrationTarget: 'keep-next-direct', riskLevel: 'high' },
  { route: '/api/features/catalog', classification: 'unused', note: 'Available route, no current UI caller', migrationTarget: 'undecided', riskLevel: 'low' },
]
