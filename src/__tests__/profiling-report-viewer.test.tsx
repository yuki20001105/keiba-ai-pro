import { render, screen, waitFor } from '@testing-library/react'
import { readFileSync } from 'node:fs'
import { beforeEach, describe, expect, test, vi } from 'vitest'

const authFetchMock = vi.fn()
let jobId = 'abcdef1234'

vi.mock('@/lib/auth-fetch', () => ({ authFetch: authFetchMock }))
vi.mock('next/navigation', () => ({ useParams: () => ({ job_id: jobId }) }))
vi.mock('@/components/AdminOnly', () => ({
  AdminOnly: ({ children }: { children: React.ReactNode }) => <>{children}</>,
}))

describe('Admin profiling report viewer', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    jobId = 'abcdef1234'
  })

  test('loads HTML through authFetch and confines it to a sandboxed iframe', async () => {
    authFetchMock.mockResolvedValue(
      new Response('<html><body>profile marker</body></html>', {
        status: 200,
        headers: { 'Content-Type': 'text/html; charset=utf-8' },
      }),
    )
    const { default: ProfilingReportPage } = await import(
      '@/app/data-collection/profiling/[job_id]/page'
    )
    render(<ProfilingReportPage />)

    const frame = await screen.findByTitle('特徴量プロファイリング abcdef1234')
    expect(authFetchMock).toHaveBeenCalledWith('/api/profiling/html/abcdef1234', {
      cache: 'no-store',
      signal: expect.any(AbortSignal),
    })
    expect(frame).toHaveAttribute('sandbox', 'allow-scripts')
    expect(frame).not.toHaveAttribute('sandbox', expect.stringContaining('allow-same-origin'))
    expect(frame).toHaveAttribute('referrerpolicy', 'no-referrer')
    expect(frame).toHaveAttribute('srcdoc', expect.stringContaining('profile marker'))
    expect(frame).toHaveAttribute('srcdoc', expect.stringContaining("connect-src 'none'"))
    expect(frame).toHaveAttribute('srcdoc', expect.stringContaining("frame-src 'none'"))
    expect(screen.queryByText('profile marker')).not.toBeInTheDocument()
  })

  test('shows bounded authorization detail without rendering a report', async () => {
    authFetchMock.mockResolvedValue(
      new Response(JSON.stringify({ detail: 'Admin role required' }), {
        status: 403,
        headers: { 'Content-Type': 'application/json' },
      }),
    )
    const { default: ProfilingReportPage } = await import(
      '@/app/data-collection/profiling/[job_id]/page'
    )
    render(<ProfilingReportPage />)

    expect(await screen.findByRole('alert')).toHaveTextContent('Admin role required')
    expect(screen.queryByTitle(/特徴量プロファイリング/)).not.toBeInTheDocument()
  })

  test('rejects malformed job IDs before making a request', async () => {
    jobId = '../secrets'
    const { default: ProfilingReportPage } = await import(
      '@/app/data-collection/profiling/[job_id]/page'
    )
    render(<ProfilingReportPage />)

    expect(await screen.findByRole('alert')).toHaveTextContent('Job IDの形式が不正')
    await waitFor(() => expect(authFetchMock).not.toHaveBeenCalled())
  })

  test('source never injects report HTML into the parent DOM', () => {
    const source = readFileSync('src/app/data-collection/profiling/[job_id]/page.tsx', 'utf8')
    expect(source).not.toContain('dangerouslySetInnerHTML')
    expect(source).toContain('sandbox="allow-scripts"')
    expect(source).not.toContain('allow-same-origin')
    expect(source).toContain("connect-src 'none'")
    expect(source).toContain('authFetch(`/api/profiling/html/${jobId}`')
  })

  test('keeps the optional profiling workflow off the essential data-collection screen', () => {
    const source = readFileSync('src/app/data-collection/page.tsx', 'utf8')
    expect(source).not.toContain("authFetch('/api/profiling'")
    expect(source).not.toContain('href={`/data-collection/profiling/${profilingJobId}`}')
    expect(source).not.toContain('href={`/api/profiling/html/${profilingJobId}`}')
    expect(source).not.toContain('特徴量プロファイリングレポート（オプション）')
  })
})
