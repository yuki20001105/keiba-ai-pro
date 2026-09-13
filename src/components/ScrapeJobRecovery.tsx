'use client'

import { useEffect, useLayoutEffect, useRef, useState } from 'react'
import { authFetch } from '@/lib/auth-fetch'
import type { PersistedUncertaintyLock } from '@/lib/scrape-uncertainty-approval'
import { recoveryCandidate, recoveryHistory, type RecoveryJob } from '@/lib/scrape-job-recovery'

type Props = {
  lock: PersistedUncertaintyLock
  onVerifiedJob: (jobId: string) => void
  disabled?: boolean
}

function label(job: RecoveryJob) {
  const format = (date: string) => `${date.slice(0, 4)}/${date.slice(4, 6)}/${date.slice(6, 8)}`
  const status = { completed: '完了', cancelled: '停止済み', error: '終了（エラー）' }[job.status]
  return `${format(job.startDate)}〜${format(job.endDate)} · ${status} · 開始 ${new Date(job.createdAt).toLocaleString('ja-JP')}`
}

/** Read-only evidence gathering. This component never unlocks or starts work. */
export function ScrapeJobRecovery({ lock, onVerifiedJob, disabled = false }: Props) {
  const [jobs, setJobs] = useState<RecoveryJob[]>([])
  const [selected, setSelected] = useState('')
  const [acknowledged, setAcknowledged] = useState(false)
  const [busy, setBusy] = useState(false)
  const [message, setMessage] = useState('')
  const fingerprint = JSON.stringify(lock)
  const latest = useRef(fingerprint)
  const mounted = useRef(true)
  useLayoutEffect(() => { latest.current = fingerprint }, [fingerprint])
  useEffect(() => {
    mounted.current = true
    return () => { mounted.current = false }
  }, [])
  useEffect(() => {
    setJobs([]); setSelected(''); setAcknowledged(false); setMessage('')
  }, [fingerprint])

  const readHistory = async () => {
    const response = await authFetch('/api/scrape/history?limit=100', { cache: 'no-store', signal: AbortSignal.timeout(10_000) })
    if (!response.ok) throw new Error('取得履歴を確認できません。ログイン状態と接続を確認してください。')
    return recoveryHistory(await response.json())
  }

  const run = async (verify: boolean) => {
    if (busy || disabled) return
    const identity = fingerprint
    setBusy(true); setMessage('')
    try {
      const history = await readHistory()
      const candidates = history.map(job => recoveryCandidate(job, lock)).filter((job): job is RecoveryJob => job !== null)
      if (!mounted.current || latest.current !== identity) return
      if (!verify) {
        setJobs(candidates); setSelected(''); setAcknowledged(false)
        if (candidates.length === 0) setMessage('対応する終了済みの取得が見つかりません。ロックを維持します。')
        return
      }
      const candidate = candidates.find(job => job.jobId === selected)
      if (!candidate || !acknowledged) throw new Error('対象の取得を選び、内容を確認してください。')
      const response = await authFetch(`/api/scrape/status/${candidate.jobId}`, { cache: 'no-store', signal: AbortSignal.timeout(10_000) })
      if (!response.ok) throw new Error('対象ジョブの状態を確認できません。ロックを維持します。')
      const confirmed = recoveryCandidate(await response.json(), lock)
      if (!confirmed || JSON.stringify(confirmed) !== JSON.stringify(candidate)) throw new Error('履歴とジョブの記録が一致しません。ロックを維持します。')
      if (mounted.current && latest.current === identity) onVerifiedJob(confirmed.jobId)
    } catch (error) {
      if (mounted.current && latest.current === identity) setMessage(error instanceof Error ? error.message : '照合に失敗しました。ロックを維持します。')
    } finally {
      if (mounted.current) setBusy(false)
    }
  }

  return <div className="mt-3 space-y-3 rounded border border-[#334155] bg-[#0b121b] p-3 text-xs text-[#cbd5e1]" data-testid="scrape-job-recovery">
    <p>前回の取得を履歴から確認できます。全期間の完了や再取得の開始を意味しません。</p>
    <button type="button" onClick={() => void run(false)} disabled={disabled || busy} className="rounded bg-white px-3 py-2 text-black disabled:opacity-40">{busy ? '確認中…' : '前回の取得を探す'}</button>
    {jobs.length > 0 && <>
      <label className="block">対象の取得
        <select value={selected} onChange={event => { setSelected(event.target.value); setAcknowledged(false) }} disabled={disabled || busy} className="mt-1 w-full rounded border border-[#444] bg-[#111] p-2">
          <option value="">選択してください</option>
          {jobs.map(job => <option key={job.jobId} value={job.jobId}>{label(job)}</option>)}
        </select>
      </label>
      {selected && <p className="break-all text-[#888]">ID: {selected}</p>}
      <label className="flex items-start gap-2"><input type="checkbox" checked={acknowledged} onChange={event => setAcknowledged(event.target.checked)} disabled={disabled || busy || !selected} />前回の取得であることを確認しました。残りの期間は未実行の可能性があります。</label>
      <button type="button" onClick={() => void run(true)} disabled={disabled || busy || !selected || !acknowledged} className="rounded bg-white px-3 py-2 text-black disabled:opacity-40">この取得と照合する</button>
      <p className="text-[#888]">照合後、「状態を再確認」で完了・停止を確認します。</p>
    </>}
    {message && <p role="status">{message}</p>}
  </div>
}
