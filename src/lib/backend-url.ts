/**
 * FastAPI バックエンド URL 定数
 *
 * ML_API_URL         : 汎用エンドポイント。クラウドデプロイ時はリモート URL に変更可能。
 * SCRAPE_API_URL     : スクレイピング専用（FastAPI 同一プロセス）。
 * SCRAPE_SERVICE_URL : 外部スクレイピングマイクロサービス（別プロセス、port 8001）。
 *
 * 環境変数の優先順位:
 *   ML_API_URL         -> NEXT_PUBLIC_API_URL -> http://localhost:8000
 *   SCRAPE_API_URL     -> ML_API_URL -> NEXT_PUBLIC_API_URL -> http://localhost:8000
 *   SCRAPE_SERVICE_URL                       → http://localhost:8001
 */

interface BackendEnvironment {
  [key: string]: string | undefined
  ML_API_URL?: string
  NEXT_PUBLIC_API_URL?: string
  SCRAPE_API_URL?: string
  SCRAPE_SERVICE_URL?: string
}

export function resolveBackendUrls(env: BackendEnvironment = process.env) {
  const mlApiUrl =
    env.ML_API_URL ||
    env.NEXT_PUBLIC_API_URL ||
    'http://localhost:8000'

  return {
    mlApiUrl,
    // Scraping endpoints live in the same FastAPI process unless a dedicated
    // upstream is explicitly configured. This keeps Vercel from falling back
    // to its own unreachable localhost when only NEXT_PUBLIC_API_URL is set.
    scrapeApiUrl: env.SCRAPE_API_URL || mlApiUrl,
    scrapeServiceUrl: env.SCRAPE_SERVICE_URL || 'http://localhost:8001',
  }
}

const backendUrls = resolveBackendUrls()

export const ML_API_URL = backendUrls.mlApiUrl
export const SCRAPE_API_URL = backendUrls.scrapeApiUrl
export const SCRAPE_SERVICE_URL = backendUrls.scrapeServiceUrl
