'use client'

import { useEffect, useRef, useState } from 'react'
import type { PredictionInputDiagnostics, Recommendation, ReferenceBets } from '@/lib/race-analysis-types'
import { ReferenceBetsPanel } from '@/components/ReferenceBetsPanel'
import { BETTING_POLICY_VERSION, bettingRecommendationExpired, selectedTickets } from '@/lib/betting-policy'
import { predictionInputVerified, sharedInputFormulaMatchedCaptureUnverified } from '@/lib/prediction-input-contract'
import { useBettingClock } from '@/hooks/useBettingClock'
import { savePerformanceRecord } from '@/lib/performance-records'

export function bettingDecisionLabel(rec: Recommendation | null | undefined, nowMs = Date.now(), diagnostics?: PredictionInputDiagnostics | null): string {
  if (rec?.policy_version !== BETTING_POLICY_VERSION || bettingRecommendationExpired(rec, nowMs)) return '再判定が必要です'
  if ((rec.decision === 'buy' || rec.decision === 'priority') && (!predictionInputVerified(diagnostics) || rec.input_verified !== true)) return '参考予測 · 見送り'
  if (selectedTickets(rec, nowMs).length === 0) return '見送り'
  return rec.decision === 'priority' ? '勝負候補' : '購入候補'
}

export function BettingRecommendationPanel({ recommendation, snapshotId, nowMs, diagnostics, referenceBets }: { recommendation: Recommendation | null | undefined; snapshotId?: string | null; nowMs?: number; diagnostics?: PredictionInputDiagnostics | null; referenceBets?: ReferenceBets | null }) {
  const clock = useBettingClock(nowMs === undefined)
  const now = nowMs ?? clock
  const [confirming, setConfirming] = useState(false)
  const [saving, setSaving] = useState(false)
  const [recorded, setRecorded] = useState(false)
  const [message, setMessage] = useState('')
  const [recordError, setRecordError] = useState('')
  const currentSnapshot = useRef(snapshotId)
  useEffect(() => {
    currentSnapshot.current = snapshotId
    setConfirming(false); setSaving(false); setRecorded(false); setMessage(''); setRecordError('')
  }, [snapshotId])
  const current = recommendation?.policy_version === BETTING_POLICY_VERSION
  const expired = bettingRecommendationExpired(recommendation, now)
  const inputVerified = predictionInputVerified(diagnostics) && recommendation?.input_verified === true
  const captureReason = sharedInputFormulaMatchedCaptureUnverified(diagnostics)
    && Array.isArray(recommendation?.reason_codes) && recommendation.reason_codes.includes('input_contract_unverified')
  // Only replace the inaccurate input phrase, retaining every other rejection.
  const displayReason = captureReason && typeof recommendation?.reason === 'string'
    ? recommendation.reason.replace('モデルの入力互換性が未確認', '発走前の取得証拠が未確認')
      .replace('入力互換性未確認のため', '発走前の取得証拠が未確認のため')
    : recommendation?.reason
  const unverifiedInputReason = captureReason ? '発走前の取得証拠が未確認のため買い目には使用しません。' : '入力互換性未確認のため買い目には使用しません。'
  const tickets = inputVerified ? selectedTickets(recommendation, now) : []
  const total = tickets.reduce((sum, ticket) => sum + ticket.stake, 0)
  const recordActual = async () => {
    if (saving || !confirming || !inputVerified || tickets.length === 0) return
    setSaving(true)
    setRecordError('')
    const submittedSnapshot = snapshotId
    try {
      const saved = await savePerformanceRecord(snapshotId, recommendation, 'actual')
      if (submittedSnapshot !== currentSnapshot.current) return
      setRecorded(true)
      setMessage(saved.existing ? 'このレースは記録済みです。最初の買い目を保持しています。' : '購入済みとして記録しました。')
      setConfirming(false)
    } catch (error) {
      if (submittedSnapshot === currentSnapshot.current) setRecordError(error instanceof Error ? error.message : '成績の記録に失敗しました。')
    } finally { if (submittedSnapshot === currentSnapshot.current) setSaving(false) }
  }
  return (
    <section aria-label="買い目判定" className="p-4 space-y-3">
      {tickets.length === 0 && <ReferenceBetsPanel reference={referenceBets} />}
      <div className="flex flex-wrap items-center justify-between gap-2">
        <span className={`text-sm font-semibold ${tickets.length ? 'text-[#86efac]' : 'text-[#aaa]'}`}>
          {bettingDecisionLabel(recommendation, now, diagnostics)}
        </span>
        <span className="text-sm font-semibold">合計 ¥{total.toLocaleString()}</span>
      </div>
      <p className="text-xs text-[#888]">{expired ? 'オッズまたは発走時刻の有効期限を過ぎました。予測を再実行してください。' : current && (recommendation?.decision === 'buy' || recommendation?.decision === 'priority') && !inputVerified ? unverifiedInputReason : current ? displayReason : '設定を確認して予測を再実行してください。'}</p>
      {tickets.length > 0 && (
        <div className="space-y-2">
          {tickets.map((ticket, index) => (
            <div key={`${ticket.bet_type}-${ticket.combination}-${index}`} className="flex flex-wrap items-center justify-between gap-2 text-xs rounded border border-[#2a3a2a] bg-[#0d160d] px-3 py-2">
              <span><span className="text-[#888] mr-2">{ticket.bet_type}</span><span className="font-mono text-[#86efac]">{ticket.combination}</span></span>
              <span className="flex gap-3"><span className="text-[#888]">{ticket.odds}倍 · 期待倍率 {ticket.conservative_expected_value.toFixed(2)}</span><span className="font-semibold">¥{ticket.stake.toLocaleString()}</span></span>
            </div>
          ))}
        </div>
      )}
      {snapshotId && tickets.length > 0 && !recorded && (
        <div className="border-t border-[#252525] pt-3 space-y-2">
          {confirming ? (
            <div role="group" aria-label="購入記録の確認" className="space-y-2">
              <p className="text-xs text-[#aaa]">この買い目を購入済みとして記録しますか？購入は行いません。</p>
              <div className="flex flex-wrap gap-2">
                <button onClick={recordActual} disabled={saving || tickets.length === 0} className="rounded bg-white px-3 py-2 text-xs font-medium text-black disabled:opacity-40">{saving ? '記録中...' : '確認して記録'}</button>
                <button onClick={() => setConfirming(false)} disabled={saving} className="rounded border border-[#333] px-3 py-2 text-xs text-[#aaa] disabled:opacity-40">キャンセル</button>
              </div>
            </div>
          ) : (
            <button onClick={() => { setConfirming(true); setRecordError('') }} className="rounded border border-[#444] px-3 py-2 text-xs text-[#ddd] hover:border-[#888]">購入済みとして記録</button>
          )}
        </div>
      )}
      {message && <p role="status" className="text-xs text-[#86efac]">{message}</p>}
      {recordError && <p role="alert" className="text-xs text-[#f87171]">{recordError}</p>}
    </section>
  )
}
