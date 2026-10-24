'use client'

import type { BatchFailureKind, BatchMonitoringIssue } from '@/hooks/useBatchScrape'
import { scrapeRaceScopeLabel, type ScrapeRaceScope } from '@/lib/scrape-race-scope'

type Props = {
  failureKind: BatchFailureKind
  monitoringIssue?: BatchMonitoringIssue | null
  error: string | null
  jobId: string | null
  acceptanceVerified: boolean
  raceScope: ScrapeRaceScope | null
  startPeriod: string
  endPeriod: string
  loading: boolean
  canRetry: boolean
  retryDisabled: boolean
  canResubmit: boolean
  canRetire?: boolean
  retirementSupported?: boolean
  onReconnect: () => void
  onRetry: () => void
  onResubmit: () => void
  onRetire?: () => void
}

/** A lost response is not proof that the server failed or never accepted a job. */
export function ScrapeBatchRecoveryNotice(props: Props) {
  const reconnectable = Boolean(props.jobId)
    && (props.failureKind === 'monitoring' || props.failureKind === 'authentication')
  const retryable = props.canRetry
    && (props.failureKind === 'execution' || props.failureKind === 'start_rejected')
  const canSelectAgain = props.canRetire === true && props.monitoringIssue === 'not_found'
    && props.retirementSupported !== false && typeof props.onRetire === 'function'
  const title = props.failureKind === 'monitoring'
    ? props.monitoringIssue === 'not_found' ? '受付記録が見つかりません'
      : props.acceptanceVerified ? '取得状況を確認できません' : '受付を確認できません'
    : props.failureKind === 'authentication'
      ? 'ログイン・権限の確認が必要です'
      : props.failureKind === 'execution' ? '取得を完了できませんでした'
        : props.failureKind === 'start_rejected' ? '取得を開始できませんでした'
          : props.failureKind === 'validation' ? '入力を確認してください'
            : props.failureKind === 'busy' ? '別の取得が実行中です'
              : props.failureKind === 'client_stop' ? '画面の監視を停止しました'
                : '取得状況を確認してください'

  return (
    <div className="space-y-3" role="alert" data-testid="batch-recovery-notice">
      <div className="space-y-1">
        <h3 className="text-sm font-medium text-[#fde68a]">{title}</h3>
        {reconnectable ? (
          <p className="text-xs leading-relaxed text-[#aaa]"
            data-testid={!props.acceptanceVerified && props.failureKind === 'monitoring'
              ? 'scrape-acceptance-unverified' : undefined}>
            {props.failureKind === 'authentication'
              ? 'ログイン状態を確認し、保存したジョブの状態を再確認してください。'
              : props.monitoringIssue === 'not_found'
                ? canSelectAgain
                  ? '前回の依頼を整理して対象を選び直せます。保存済みデータは残り、新しい取得は自動で開始しません。'
                  : '保存したジョブIDの受付記録を確認できません。新しい取得は自動で開始しません。'
                : props.acceptanceVerified
                ? 'サーバーで取得が継続している可能性があります。まず状態を再確認してください。'
                : '取得開始は未確認です。二重取得を防ぐため、まず状態を再確認してください。'}
          </p>
        ) : retryable ? (
          <p className="text-xs text-[#aaa]">保存済みデータを残して、同じ期間の補完を再実行できます。</p>
        ) : null}
        {props.monitoringIssue === 'not_found' && props.retirementSupported === false && (
          <p className="text-xs text-[#aaa]" data-testid="scrape-retirement-server-pending">
            安全な依頼整理はサーバーへの反映待ちです。まず状態を再確認してください。
          </p>
        )}
      </div>

      {canSelectAgain ? (
        <div className="flex flex-wrap gap-2">
          <button type="button" data-testid="retire-batch-button"
            onClick={props.onRetire} disabled={props.loading}
            className="rounded bg-white px-4 py-2 text-xs font-medium text-black disabled:opacity-50">
            {props.loading ? '確認中...' : '前回の依頼を整理して選び直す'}
          </button>
          <button type="button" data-testid="reconnect-batch-button"
            onClick={props.onReconnect} disabled={props.loading}
            className="rounded border border-[#333] px-3 py-2 text-xs text-white disabled:opacity-50">
            状態を再確認
          </button>
        </div>
      ) : reconnectable ? (
        <button type="button" data-testid="reconnect-batch-button"
          onClick={props.onReconnect} disabled={props.loading}
          className="rounded bg-white px-4 py-2 text-xs font-medium text-black disabled:opacity-50">
          {props.loading ? '確認中...' : '状態を再確認'}
        </button>
      ) : retryable ? (
        <button type="button" data-testid="retry-button"
          onClick={props.onRetry} disabled={props.retryDisabled}
          className="rounded bg-white px-4 py-2 text-xs font-medium text-black disabled:opacity-50">
          再実行
        </button>
      ) : null}

      <details className="border-t border-[#252525] pt-2 text-xs text-[#888]"
        data-testid="batch-recovery-details">
        <summary className="cursor-pointer py-1 text-[#aaa]">{props.canResubmit ? '詳細・再送' : '詳細'}</summary>
        <div className="space-y-2 pt-2 leading-relaxed">
          {props.raceScope && (
            <div data-testid="batch-scrape-race-scope">対象: {scrapeRaceScopeLabel(props.raceScope)}</div>
          )}
          <div>期間: {props.startPeriod.replace('-', '/')}～{props.endPeriod.replace('-', '/')}</div>
          {props.jobId && <div className="break-all">ジョブID: {props.jobId}</div>}
          <div className="break-words">理由: {props.error || '応答を確認できませんでした。'}</div>
          {reconnectable && (
            <p>API接続と取得ジョブの確認は別です。ジョブIDは保持し、自動再送はしません。</p>
          )}
          {props.canResubmit && (
            <div className="space-y-2">
              <p>再送前に受付記録が見つからないことを再確認します。期間・対象・ジョブIDは変更しません。</p>
              <button type="button" data-testid="resubmit-batch-button"
                disabled={props.loading} onClick={props.onResubmit}
                className="rounded border border-[#333] px-3 py-1.5 text-xs text-white disabled:opacity-50">
                同じ依頼を再送
              </button>
            </div>
          )}
        </div>
      </details>
    </div>
  )
}
