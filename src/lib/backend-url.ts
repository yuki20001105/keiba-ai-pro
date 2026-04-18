/**
 * FastAPI バックエンド URL 定数
 *
 * ML_API_URL         : 汎用エンドポイント。クラウドデプロイ時はリモート URL に変更可能。
 * SCRAPE_API_URL     : スクレイピング専用（FastAPI 同一プロセス）。
 * SCRAPE_SERVICE_URL : 外部スクレイピングマイクロサービス（別プロセス、port 8001）。
 *
 * 環境変数の優先順位:
 *   ML_API_URL         → NEXT_PUBLIC_API_URL → http://localhost:8000
 *   SCRAPE_API_URL                           → http://localhost:8000
 *   SCRAPE_SERVICE_URL                       → http://localhost:8001
 */

export const ML_API_URL =
  process.env.ML_API_URL ||
  process.env.NEXT_PUBLIC_API_URL ||
  'http://localhost:8000'

export const SCRAPE_API_URL =
  process.env.SCRAPE_API_URL || 'http://localhost:8000'

export const SCRAPE_SERVICE_URL =
  process.env.SCRAPE_SERVICE_URL || 'http://localhost:8001'
