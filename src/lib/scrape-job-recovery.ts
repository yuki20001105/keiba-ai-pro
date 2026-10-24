import type { PersistedUncertaintyLock } from './scrape-uncertainty-approval'

const UUID = /^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/i
const TERMINAL = new Set(['completed', 'error', 'cancelled'])
const ACTIVE = new Set(['queued', 'running', 'cancelling', 'recovering', 'waiting_resources', 'paused_resource'])

export type RecoveryJob = {
  jobId: string
  status: string
  startDate: string
  endDate: string
  createdAt: string
}

function record(value: unknown): value is Record<string, unknown> {
  return typeof value === 'object' && value !== null && !Array.isArray(value)
}

function dateKey(value: unknown): string | null {
  if (typeof value !== 'string' || !/^(\d{8}|\d{4}-\d{2}-\d{2}|\d{4}\/\d{2}\/\d{2})$/.test(value)) return null
  const key = value.replace(/[-/]/g, '')
  const iso = `${key.slice(0, 4)}-${key.slice(4, 6)}-${key.slice(6, 8)}`
  const timestamp = Date.parse(`${iso}T00:00:00Z`)
  return Number.isFinite(timestamp) && new Date(timestamp).toISOString().slice(0, 10) === iso ? key : null
}

export function recoveryCandidate(value: unknown, lock: PersistedUncertaintyLock): RecoveryJob | null {
  if (!record(value) || !record(value.request_payload)) return null
  if (typeof value.job_id !== 'string' || !UUID.test(value.job_id)) return null
  if (typeof value.status !== 'string' || !TERMINAL.has(value.status)) return null
  const request = value.request_payload
  if (request.dry_run !== false || request.force_rescrape !== lock.request.forceRescrape) return null
  const start = dateKey(request.start_date)
  const end = dateKey(request.end_date)
  const firstMonth = lock.request.startPeriod.replace('-', '')
  const lastMonth = lock.request.endPeriod.replace('-', '')
  if (!/^\d{6}$/.test(firstMonth) || !/^\d{6}$/.test(lastMonth)) return null
  if (!start || !end || start > end || start.slice(0, 6) < firstMonth || end.slice(0, 6) > lastMonth) return null
  if (typeof value.created_at !== 'string') return null
  const created = Date.parse(value.created_at)
  const occurred = Date.parse(lock.occurredAt)
  if (!Number.isFinite(created) || !Number.isFinite(occurred) || created > occurred + 30_000) return null
  if (value.status === 'completed') {
    if (!record(value.result) || !Number.isSafeInteger(value.result.races_collected) || (value.result.races_collected as number) < 0) return null
  }
  return { jobId: value.job_id, status: value.status, startDate: start, endDate: end, createdAt: value.created_at }
}

export function recoveryHistory(value: unknown): unknown[] {
  if (!record(value) || !Array.isArray(value.jobs) || value.count !== value.jobs.length) throw new Error('取得履歴を確認できません。')
  if (value.jobs.some(job => !record(job) || typeof job.status !== 'string' || ![...ACTIVE, ...TERMINAL].includes(job.status))) throw new Error('取得履歴の状態を確認できません。')
  if (value.jobs.some(job => record(job) && ACTIVE.has(job.status as string))) throw new Error('別の取得が実行中です。実行中のジョブを先に確認してください。')
  return value.jobs
}
