export interface ScrapeCompletionOutcome {
  kind: 'success' | 'warning'
  message: string
}

function record(value: unknown): Record<string, unknown> {
  return value !== null && typeof value === 'object' && !Array.isArray(value)
    ? value as Record<string, unknown>
    : {}
}

function text(value: unknown): string {
  return typeof value === 'string' ? value.trim() : ''
}

/** A terminal job is not necessarily a successful acquisition. */
export function scrapeCompletionOutcome(
  statusData: unknown,
  availableRaceCount?: number,
): ScrapeCompletionOutcome {
  const status = record(statusData)
  const result = record(status.result)
  const summary = record(result.fetch_summary)
  const quality = record(summary.quality_audit)
  const failed = result.success === false || quality.quality_complete === false
    || (typeof quality.incomplete_date_count === 'number' && quality.incomplete_date_count > 0)
    || Boolean(text(result.failure_code))

  if (failed) {
    const reason = text(result.failure_reason)
    const message = text(result.message)
    const legacyErrors = JSON.stringify([result.errors, status.errors, result.failure_code])
    const fallback = /recent_race_list_empty_recheck|race_list_empty/.test(legacyErrors)
      ? 'この日のレース情報はまだ確認できません。時間をおいて再試行してください。'
      : availableRaceCount === 0
        ? 'レース情報を取得できませんでした。時間をおいて再試行してください。'
        : '取得できなかったデータがあります。時間をおいて再試行してください。'
    return {
      kind: 'warning',
      // Older jobs report only that a repair queue was created, hiding the failure.
      message: reason || (message && !message.startsWith('収集は完了しましたが') ? message : fallback),
    }
  }

  if (availableRaceCount === 0) {
    if (typeof result.verified_no_race_dates === 'number' && result.verified_no_race_dates > 0) {
      return { kind: 'success', message: 'この日の開催はありません（確認済み）。' }
    }
    return {
      kind: 'warning',
      message: 'この日のレースを取得できませんでした。時間をおいて再試行してください。',
    }
  }
  return { kind: 'success', message: 'レース情報を取得しました。' }
}
