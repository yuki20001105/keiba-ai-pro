import { describe, expect, it } from 'vitest'

import { resolveBackendUrls } from '@/lib/backend-url'

describe('resolveBackendUrls', () => {
  it('uses explicit endpoints when all are configured', () => {
    expect(resolveBackendUrls({
      ML_API_URL: 'https://ml.example.test',
      NEXT_PUBLIC_API_URL: 'https://public.example.test',
      SCRAPE_API_URL: 'https://scrape.example.test',
      SCRAPE_SERVICE_URL: 'https://browser.example.test',
    })).toEqual({
      mlApiUrl: 'https://ml.example.test',
      scrapeApiUrl: 'https://scrape.example.test',
      scrapeServiceUrl: 'https://browser.example.test',
    })
  })

  it('routes scraping to the hosted ML API when no dedicated URL is set', () => {
    expect(resolveBackendUrls({
      ML_API_URL: 'https://keiba-ai-api.onrender.com',
    })).toMatchObject({
      mlApiUrl: 'https://keiba-ai-api.onrender.com',
      scrapeApiUrl: 'https://keiba-ai-api.onrender.com',
    })
  })

  it('uses the public API URL for both server-side proxies on Vercel', () => {
    expect(resolveBackendUrls({
      NEXT_PUBLIC_API_URL: 'https://keiba-ai-api.onrender.com',
    })).toMatchObject({
      mlApiUrl: 'https://keiba-ai-api.onrender.com',
      scrapeApiUrl: 'https://keiba-ai-api.onrender.com',
    })
  })

  it('uses local defaults for one-click local startup', () => {
    expect(resolveBackendUrls({})).toEqual({
      mlApiUrl: 'http://localhost:8000',
      scrapeApiUrl: 'http://localhost:8000',
      scrapeServiceUrl: 'http://localhost:8001',
    })
  })
})
