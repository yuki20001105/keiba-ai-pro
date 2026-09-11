/** @type {import('next').NextConfig} */
const standaloneBuild =
  process.env.NEXT_STANDALONE_BUILD === '1' && process.env.VERCEL !== '1'

const nextConfig = {
  // Docker opts into standalone output explicitly. Local and CI builds avoid
  // copying local dotenv files and repository-wide process traces into it;
  // Vercel packages server functions itself.
  ...(standaloneBuild ? { output: 'standalone' } : {}),
  // Python runtimes and local data are provisioned beside the Next.js
  // artifact. They must never be copied into `.next/standalone` by broad
  // dynamic-process tracing.
  outputFileTracingExcludes: {
    '*': [
      '**/python-api/.venv/**',
      '**/keiba/data/**',
      '**/notebooks/data/**',
      '**/notebooks/reports/**',
      '**/reports/generated/**',
      '**/cache/**',
      '**/*.db-wal',
      '**/*.db-shm',
      '**/*.log',
    ],
  },
  reactStrictMode: true,
  allowedDevOrigins: ['127.0.0.1', 'localhost', '10.132.114.4'],
  
  experimental: {
    serverActions: {
      bodySizeLimit: '10mb',
    },
  },
  
  images: {
    remotePatterns: [
      {
        protocol: 'https',
        hostname: 'lh3.googleusercontent.com',
      },
      {
        protocol: 'http',
        hostname: 'localhost',
      },
    ],
    formats: ['image/avif', 'image/webp'],
  },

  // 本番ビルド最適化
  productionBrowserSourceMaps: false,
  
  // 環境変数
  env: {
    NEXT_PUBLIC_API_URL: process.env.NEXT_PUBLIC_API_URL || 'http://localhost:8000',
    NEXT_PUBLIC_SUPABASE_URL: process.env.NEXT_PUBLIC_SUPABASE_URL,
    NEXT_PUBLIC_SUPABASE_ANON_KEY: process.env.NEXT_PUBLIC_SUPABASE_ANON_KEY,
  },

  // PWA用ヘッダー
  async headers() {
    return [
      {
        source: '/manifest.json',
        headers: [
          {
            key: 'Content-Type',
            value: 'application/manifest+json',
          },
        ],
      },
      {
        source: '/sw.js',
        headers: [
          {
            key: 'Content-Type',
            value: 'application/javascript',
          },
          {
            key: 'Service-Worker-Allowed',
            value: '/',
          },
        ],
      },
    ]
  },
}

module.exports = nextConfig
