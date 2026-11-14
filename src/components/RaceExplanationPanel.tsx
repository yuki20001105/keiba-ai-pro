'use client'

import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import { authFetch } from '@/lib/auth-fetch'
import {
  buildRaceNarrativeInput,
  narrativeCacheKey,
  type NarrativeResponse,
} from '@/lib/prediction-explanation'
import type { FeatureContribution, RacePredictResult } from '@/lib/race-analysis-types'
import { predictionFeatureLabel, predictionFeatureValue, predictionScoreContribution } from '@/lib/prediction-feature-display'

type Props = {
  result: RacePredictResult
}

// The API advertises a 30 second budget. Allow a small transport/auth margin.
const NARRATIVE_REQUEST_TIMEOUT_MS = 35_000

function readNarrative(value: unknown): NarrativeResponse | null {
  if (!value || typeof value !== 'object') return null
  const record = value as Record<string, unknown>
  return typeof record.explanation === 'string' && record.explanation.trim().length > 0
    && typeof record.source === 'string' && ['openai', 'gemini', 'fallback'].includes(record.source)
    ? { explanation: record.explanation, source: record.source as NarrativeResponse['source'] }
    : null
}

function ContributionList({
  title,
  features,
  tone,
}: {
  title: string
  features: FeatureContribution[]
  tone: 'positive' | 'negative'
}) {
  const color = tone === 'positive' ? '#4ade80' : '#fb923c'
  if (features.length === 0) return null

  return (
    <div className="space-y-2">
      <div className="text-xs text-[#888]">{title}</div>
      {features.slice(0, 3).map(feature => (
        <div key={feature.feature} className="space-y-1.5 rounded border border-[#202020] bg-[#101010] px-2.5 py-2">
          <div className="flex items-center justify-between gap-3 text-xs" title="全特徴量の寄与量合計に対する割合です">
            <span className="truncate text-[#ddd]">{predictionFeatureLabel(feature)}</span>
            <span className="shrink-0 tabular-nums" style={{ color }}>
              割合 {feature.impact_pct.toFixed(0)}%
            </span>
          </div>
          {feature.feature === 'safe_race_class' && (
            <p className="text-[10px] text-[#777]">レースの条件区分です（例：新馬・未勝利・1勝クラス・重賞）。</p>
          )}
          <div className="flex flex-wrap items-center justify-between gap-x-3 gap-y-1 text-[11px] text-[#999]">
            <span>今回の値：<span className="text-[#ddd]">{predictionFeatureValue(feature)}</span></span>
            <span>スコア寄与：<span className="tabular-nums" style={{ color }}>{predictionScoreContribution(feature.contribution)}</span></span>
          </div>
          <div className="h-1.5 overflow-hidden rounded-full bg-[#1e1e1e]">
            <div
              className="h-full rounded-full"
              style={{ width: `${Math.max(4, Math.min(feature.impact_pct, 100))}%`, backgroundColor: color }}
            />
          </div>
        </div>
      ))}
    </div>
  )
}

export function RaceExplanationPanel({ result }: Props) {
  const candidates = useMemo(
    () => [...result.predictions].sort((a, b) => a.predicted_rank - b.predicted_rank).slice(0, 3),
    [result.predictions],
  )
  const [horseNumber, setHorseNumber] = useState(candidates[0]?.horse_number ?? 0)
  const [narrativeState, setNarrativeState] = useState<{ identity: string; value: NarrativeResponse } | null>(null)
  const [pendingIdentity, setPendingIdentity] = useState<string | null>(null)
  const [narrativeFailure, setNarrativeFailure] = useState<{ identity: string; message: string } | null>(null)
  const generationRef = useRef(0)
  const requestControllerRef = useRef<AbortController | null>(null)
  const stopNarrativeRequest = useCallback(() => {
    ++generationRef.current
    requestControllerRef.current?.abort()
    requestControllerRef.current = null
  }, [])

  const prediction = candidates.find(candidate => candidate.horse_number === horseNumber) ?? candidates[0]
  const features = prediction?.explanation?.features ?? []
  const positive = features.filter(feature => feature.direction === 'positive')
  const negative = features.filter(feature => feature.direction === 'negative')
  const cacheKey = prediction
    ? narrativeCacheKey(result.race_info.race_id, result.model_id, prediction.horse_number)
    : ''
  const narrativeInput = prediction ? buildRaceNarrativeInput(result, prediction) : null
  // Same model ID is not enough: a refreshed prediction can have new SHAP
  // evidence. Legacy cache entries without this exact identity are ignored.
  const explanationIdentity = JSON.stringify({
    race_id: result.race_info.race_id, model_id: result.model_id ?? null,
    horse_number: prediction?.horse_number ?? null, input: narrativeInput,
    score: prediction?.p_raw, explanation: prediction?.explanation,
  })
  const narrative = narrativeState?.identity === explanationIdentity ? narrativeState.value : null
  const narrativeLoading = pendingIdentity === explanationIdentity
  const narrativeError = narrativeFailure?.identity === explanationIdentity ? narrativeFailure.message : ''

  useEffect(() => {
    stopNarrativeRequest()
    setPendingIdentity(null)
    setNarrativeFailure(null)
    setNarrativeState(null)
    if (!cacheKey) {
      // There is no valid prediction to explain.
    } else {
      try {
        const cached = JSON.parse(localStorage.getItem(cacheKey) || 'null')
        const value = cached?.version === 3 && cached.inputIdentity === explanationIdentity
          ? readNarrative(cached.narrative) : null
        if (value) setNarrativeState({ identity: explanationIdentity, value })
      } catch { /* A corrupt or obsolete cache is not prediction evidence. */ }
    }
    return stopNarrativeRequest
  }, [cacheKey, explanationIdentity, stopNarrativeRequest])

  if (!prediction) {
    return <div className="p-5 text-sm text-[#666]">説明できる予測がありません。</div>
  }

  const loadNarrative = async () => {
    if (!narrativeInput || requestControllerRef.current) return
    const generation = ++generationRef.current
    const controller = new AbortController()
    requestControllerRef.current = controller
    const current = () => generation === generationRef.current
    setPendingIdentity(explanationIdentity)
    setNarrativeFailure(null)
    let timeoutId: ReturnType<typeof setTimeout> | undefined
    let abortRequest: (() => void) | undefined
    let timedOut = false
    try {
      const cancellation = new Promise<never>((_resolve, reject) => {
        abortRequest = () => reject(new Error('narrative-request-aborted'))
        controller.signal.addEventListener('abort', abortRequest, { once: true })
        timeoutId = setTimeout(() => { timedOut = true; controller.abort() }, NARRATIVE_REQUEST_TIMEOUT_MS)
      })
      const readResponse = (async () => {
        const response = await authFetch('/api/prediction-explanation', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify(narrativeInput), signal: controller.signal,
        })
        const data: unknown = await response.json()
        if (!response.ok) {
          const detail = data && typeof data === 'object' ? (data as Record<string, unknown>).detail : null
          throw new Error(typeof detail === 'string' ? detail : '解説を生成できませんでした')
        }
        const next = readNarrative(data)
        if (!next) throw new Error('解説の応答を確認できませんでした')
        return next
      })()
      const next = await Promise.race([readResponse, cancellation])
      if (!current()) return
      setNarrativeState({ identity: explanationIdentity, value: next })
      try {
        localStorage.setItem(cacheKey, JSON.stringify({ version: 3, inputIdentity: explanationIdentity, narrative: next }))
      } catch { /* session display only */ }
    } catch (error) {
      if (current()) setNarrativeFailure({ identity: explanationIdentity,
        message: timedOut ? '解説の生成が時間切れになりました。もう一度お試しください。'
          : error instanceof Error ? error.message : '解説を生成できませんでした' })
    } finally {
      if (timeoutId !== undefined) clearTimeout(timeoutId)
      if (abortRequest) controller.signal.removeEventListener('abort', abortRequest)
      if (current()) {
        setPendingIdentity(null)
        if (requestControllerRef.current === controller) requestControllerRef.current = null
      }
    }
  }

  return (
    <div className="p-4 space-y-4" data-testid="race-explanation-panel">
      <div>
        <h3 className="text-sm font-semibold text-white">予測スコアの内訳</h3>
        <p className="mt-1 text-xs text-[#888]">速度スコアへの寄与 · 勝率ではありません</p>
      </div>

      <div className="flex flex-wrap gap-2" aria-label="説明する馬を選択">
        {candidates.map(candidate => (
          <button
            key={candidate.horse_number}
            type="button"
            onClick={() => setHorseNumber(candidate.horse_number)}
            className={`rounded-lg border px-3 py-2 text-left transition-colors ${
              candidate.horse_number === prediction.horse_number
                ? 'border-[#4ade80] bg-[#102016] text-white'
                : 'border-[#262626] bg-[#111] text-[#888] hover:text-white'
            }`}
          >
            <span className="mr-2 text-xs font-bold">{candidate.predicted_rank}位</span>
            <span className="text-sm">{candidate.horse_name}</span>
          </button>
        ))}
      </div>

      {features.length > 0 ? (
        <>
          <div className="flex flex-wrap items-center justify-between gap-2 rounded-lg border border-[#222] bg-[#0c0c0c] px-3 py-2.5 text-xs">
            <span className="text-[#ddd]">{prediction.horse_name} · 予測{prediction.predicted_rank}位</span>
            <span className="text-[#aaa]">速度スコア <b className="ml-1 tabular-nums text-white">{Number.isFinite(prediction.p_raw) ? prediction.p_raw.toFixed(2) : '—'}</b></span>
          </div>
          <p className="text-[11px] leading-5 text-[#888]">緑はスコアを上げる要素、橙は下げる要素です。「今回の値」は実際の入力値、「スコア寄与」は予測への加算量、「割合」は全特徴量の寄与量合計に占める比率です。</p>

          <div className="grid gap-5 md:grid-cols-2">
            <ContributionList title="スコアを上げる要素" features={positive} tone="positive" />
            <ContributionList title="スコアを下げる要素" features={negative} tone="negative" />
          </div>

          <div className="flex flex-wrap items-center gap-3 border-t border-[#1e1e1e] pt-4">
            <button
              type="button"
              onClick={loadNarrative}
              disabled={narrativeLoading}
              className="rounded border border-[#333] bg-[#171717] px-3 py-2 text-xs text-white hover:bg-[#222] disabled:opacity-50"
            >
              {narrativeLoading ? '生成中…' : narrative ? '解説を更新' : '今回のレースを解説'}
            </button>
            <span className="text-[10px] text-[#777]">レース条件・上位比較・展開材料を照合します。根拠のない展開は断定しません。</span>
          </div>

          {narrative && (
            <div className="rounded-lg border border-[#223047] bg-[#0c1420] px-4 py-3">
              <div className="mb-1 text-[10px] text-[#60a5fa]">
                {narrative.source === 'fallback' ? '予測データからの要約' : '今回のレース解説'}
              </div>
              <p className="whitespace-pre-line text-sm leading-6 text-[#dbeafe]">{narrative.explanation}</p>
            </div>
          )}
          {narrativeError && <p className="text-xs text-[#f87171]">{narrativeError}</p>}
        </>
      ) : (
        <div className="rounded-lg border border-[#2a2a2a] bg-[#111] px-4 py-5 text-sm text-[#777]">
          この予測には説明データがありません。次回の予測から表示されます。
        </div>
      )}

      <p className="text-[10px] text-[#555]">寄与は速度スコアの内訳です。勝率や着順を直接示す値ではありません。</p>
    </div>
  )
}
