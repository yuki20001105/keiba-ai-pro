import { fireEvent, render, screen } from '@testing-library/react'
import { describe, expect, it, vi } from 'vitest'
import { ScrapeBatchRecoveryNotice } from '@/components/ScrapeBatchRecoveryNotice'

const JOB = '11111111-1111-4111-8111-111111111111'
const defaults = () => ({
  failureKind: 'monitoring' as const, error: 'サーバーの受付・実行状態を確認できません。',
  jobId: JOB, acceptanceVerified: false, raceScope: 'jra' as const,
  startPeriod: '2016-01', endPeriod: '2026-09', loading: false,
  canRetry: false, retryDisabled: false, canResubmit: false,
  onReconnect: vi.fn(), onRetry: vi.fn(), onResubmit: vi.fn(),
})

describe('simple batch recovery notice', () => {
  it('does not turn an unknown acceptance into an acquisition failure and shows one main action', () => {
    const props = defaults()
    render(<ScrapeBatchRecoveryNotice {...props} />)
    expect(screen.getByRole('heading')).toHaveTextContent('受付を確認できません')
    expect(screen.getByTestId('scrape-acceptance-unverified')).toHaveTextContent('取得開始は未確認')
    expect(screen.queryByText('取得失敗')).toBeNull()
    expect(screen.getAllByRole('button')).toHaveLength(1)
    fireEvent.click(screen.getByRole('button', { name: '状態を再確認' }))
    expect(props.onReconnect).toHaveBeenCalledTimes(1)
    expect(props.onResubmit).not.toHaveBeenCalled()
    expect(props.onRetry).not.toHaveBeenCalled()
  })

  it('preserves scope, period, ID and the original reason inside collapsed details', () => {
    const props = defaults()
    render(<ScrapeBatchRecoveryNotice {...props} raceScope="all" />)
    const details = screen.getByTestId('batch-recovery-details')
    expect(details).not.toHaveAttribute('open')
    expect(details).toHaveTextContent('中央・地方競馬')
    expect(details).toHaveTextContent('2016/01～2026/09')
    expect(details).toHaveTextContent(JOB)
    expect(details).toHaveTextContent(props.error)
    expect(details).toHaveTextContent('API接続と取得ジョブの確認は別')
    expect(screen.queryByTestId('resubmit-batch-button')).toBeNull()
  })

  it('exposes resubmission only when the hook has confirmed it is available', () => {
    const props = defaults()
    render(<ScrapeBatchRecoveryNotice {...props} canResubmit monitoringIssue="not_found" />)
    expect(screen.getByRole('heading')).toHaveTextContent('受付記録が見つかりません')
    expect(screen.getByTestId('batch-recovery-details')).not.toHaveAttribute('open')
    expect(screen.getByTestId('batch-recovery-details'))
      .toContainElement(screen.getByTestId('resubmit-batch-button'))
    expect(props.onResubmit).not.toHaveBeenCalled()
    fireEvent.click(screen.getByText('詳細・再送'))
    fireEvent.click(screen.getByTestId('resubmit-batch-button'))
    expect(props.onResubmit).toHaveBeenCalledTimes(1)
    expect(props.onReconnect).not.toHaveBeenCalled()
  })

  it('separates lost monitoring of an accepted job from an unconfirmed submission', () => {
    render(<ScrapeBatchRecoveryNotice {...defaults()} acceptanceVerified />)
    expect(screen.getByRole('heading')).toHaveTextContent('取得状況を確認できません')
    expect(screen.getByTestId('batch-recovery-notice')).toHaveTextContent('継続している可能性')
    expect(screen.queryByTestId('scrape-acceptance-unverified')).toBeNull()
  })

  it('explains authentication without allowing automatic resubmission', () => {
    render(<ScrapeBatchRecoveryNotice {...defaults()} failureKind="authentication" />)
    expect(screen.getByRole('heading')).toHaveTextContent('ログイン・権限の確認が必要')
    expect(screen.getByTestId('reconnect-batch-button')).toBeEnabled()
    expect(screen.queryByTestId('resubmit-batch-button')).toBeNull()
  })

  it('disables reconnect and secondary resubmission while confirmation is busy', () => {
    render(<ScrapeBatchRecoveryNotice {...defaults()} loading canResubmit />)
    expect(screen.getByTestId('reconnect-batch-button')).toBeDisabled()
    expect(screen.getByTestId('resubmit-batch-button')).toBeDisabled()
  })

  it('keeps confirmed execution retry distinct from monitoring reconnect', () => {
    const props = defaults()
    render(<ScrapeBatchRecoveryNotice {...props} failureKind="execution" canRetry />)
    expect(screen.getByRole('heading')).toHaveTextContent('取得を完了できませんでした')
    expect(screen.queryByTestId('reconnect-batch-button')).toBeNull()
    fireEvent.click(screen.getByTestId('retry-button'))
    expect(props.onRetry).toHaveBeenCalledTimes(1)
    expect(props.onReconnect).not.toHaveBeenCalled()
  })

  it('does not invent an actionable retry for an unclassified error or missing job ID', () => {
    render(<ScrapeBatchRecoveryNotice {...defaults()} failureKind={null} jobId={null} />)
    expect(screen.getByRole('heading')).toHaveTextContent('取得状況を確認してください')
    expect(screen.queryByRole('button')).toBeNull()
  })

  it('makes selecting a new request primary only after the missing-record check permits retirement', () => {
    const onRetire = vi.fn()
    const props = defaults()
    render(<ScrapeBatchRecoveryNotice {...props} monitoringIssue="not_found" canRetire onRetire={onRetire} />)
    fireEvent.click(screen.getByTestId('retire-batch-button'))
    expect(onRetire).toHaveBeenCalledTimes(1)
    expect(props.onResubmit).not.toHaveBeenCalled()
    expect(props.onReconnect).not.toHaveBeenCalled()
    expect(screen.getByTestId('batch-recovery-notice')).toHaveTextContent('保存済みデータは残り')
    expect(screen.getByTestId('batch-recovery-details')).not.toHaveAttribute('open')
  })

  it.each(['network', 'service_unavailable', 'job_mismatch', 'invalid_response'] as const)(
    'does not allow selecting a new request on %s even if a stale permission was passed', issue => {
      render(<ScrapeBatchRecoveryNotice {...defaults()} monitoringIssue={issue} canRetire onRetire={vi.fn()} />)
      expect(screen.queryByTestId('retire-batch-button')).toBeNull()
      expect(screen.getByTestId('reconnect-batch-button')).toBeEnabled()
    },
  )

  it('explains an older server without presenting unsupported retirement or hiding explicit same-request resubmission', () => {
    const props = defaults()
    render(<ScrapeBatchRecoveryNotice {...props} monitoringIssue="not_found"
      retirementSupported={false} canRetire canResubmit onRetire={vi.fn()} />)
    expect(screen.queryByTestId('retire-batch-button')).toBeNull()
    expect(screen.getByTestId('scrape-retirement-server-pending')).toHaveTextContent('サーバーへの反映待ち')
    expect(screen.getByTestId('reconnect-batch-button')).toBeEnabled()
    expect(screen.getByTestId('batch-recovery-details')).toContainElement(screen.getByTestId('resubmit-batch-button'))
    expect(screen.getByTestId('batch-recovery-details')).toHaveTextContent('期間・対象・ジョブIDは変更しません')
  })
})
