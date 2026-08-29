import { fireEvent, render, screen, waitFor } from '@testing-library/react'
import { beforeEach, describe, expect, test, vi } from 'vitest'

const authFetchMock = vi.fn()

vi.mock('@/lib/auth-fetch', () => ({ authFetch: authFetchMock }))
vi.mock('@/contexts/AuthContext', () => ({
  useAuth: () => ({ isPremium: true, loading: false }),
}))

function jsonResponse(value: unknown, status = 200): Response {
  return new Response(JSON.stringify(value), {
    status,
    headers: { 'Content-Type': 'application/json' },
  })
}

const summary = {
  version: '1.0',
  hash: 'a'.repeat(64),
  future_fields_count: 1,
  scraped_fields_count: 1,
  engineered_total: 1,
  engineered_enabled: 1,
  engineered_disabled: 0,
  unnecessary_columns_count: 0,
  by_stage: {},
}

const catalog = {
  version: '1.0',
  hash: 'b'.repeat(64),
  data: {
    future_fields: ['finish_position'],
    scraped_fields: { race: [{ name: 'race_date', description: 'race date' }] },
    engineered_features: [
      {
        name: 'days_since_last_race',
        stage: 'days_from_history',
        type: 'numeric',
        enabled: true,
        description: 'elapsed days',
      },
    ],
    unnecessary_columns: [],
  },
}

describe('Feature catalog operator visibility', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    authFetchMock.mockImplementation(async (input: RequestInfo | URL) => {
      if (String(input) === '/api/features/summary') return jsonResponse(summary)
      if (String(input) === '/api/features/catalog') return jsonResponse(catalog)
      throw new Error(`unexpected request: ${String(input)}`)
    })
  })

  test('renders future-field boundary and engineered feature provenance from the authenticated API', async () => {
    const { default: FeatureLabPage } = await import('@/app/feature-lab/page')
    render(<FeatureLabPage />)

    await waitFor(() => expect(authFetchMock).toHaveBeenCalledWith('/api/features/summary', {
      signal: expect.any(AbortSignal),
    }))
    fireEvent.click(screen.getByRole('button', { name: '特徴量カタログ' }))

    expect(await screen.findByTestId('feature-catalog-panel')).toBeInTheDocument()
    expect(authFetchMock).toHaveBeenCalledWith('/api/features/catalog', {
      signal: expect.any(AbortSignal),
    })
    expect(screen.getByText('finish_position')).toBeInTheDocument()
    expect(screen.getByText('days_since_last_race')).toBeInTheDocument()
    expect(screen.getByText('days_from_history')).toBeInTheDocument()
    expect(screen.getByText('INV-01: 学習・推論の入力には使用しません。')).toBeInTheDocument()
  })

  test('shows API contract errors without inventing catalog content', async () => {
    authFetchMock.mockImplementation(async (input: RequestInfo | URL) => {
      if (String(input) === '/api/features/summary') return jsonResponse(summary)
      return jsonResponse({ detail: 'catalog unavailable' }, 503)
    })
    const { default: FeatureLabPage } = await import('@/app/feature-lab/page')
    render(<FeatureLabPage />)

    await waitFor(() => expect(authFetchMock).toHaveBeenCalledWith('/api/features/summary', {
      signal: expect.any(AbortSignal),
    }))
    fireEvent.click(screen.getByRole('button', { name: '特徴量カタログ' }))

    await waitFor(() => expect(screen.getByText('catalog unavailable')).toBeInTheDocument())
    expect(screen.queryByTestId('feature-catalog-panel')).not.toBeInTheDocument()
  })

  test('rejects malformed catalog payloads before rendering arrays', async () => {
    authFetchMock.mockImplementation(async (input: RequestInfo | URL) => {
      if (String(input) === '/api/features/summary') return jsonResponse(summary)
      return jsonResponse({ version: '1.0', hash: 'not-a-contract', data: { engineered_features: 'forged' } })
    })
    const { default: FeatureLabPage } = await import('@/app/feature-lab/page')
    render(<FeatureLabPage />)

    await waitFor(() => expect(authFetchMock).toHaveBeenCalledWith('/api/features/summary', {
      signal: expect.any(AbortSignal),
    }))
    fireEvent.click(screen.getByRole('button', { name: '特徴量カタログ' }))

    expect(await screen.findByText('特徴量カタログの応答形式が不正です。')).toBeInTheDocument()
    expect(screen.queryByTestId('feature-catalog-panel')).not.toBeInTheDocument()
  })
})
