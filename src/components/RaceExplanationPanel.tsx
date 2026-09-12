'use client'

import { useEffect, useMemo, useState } from 'react'
import { authFetch } from '@/lib/auth-fetch'
import {
  buildLocalPredictionExplanation,
  narrativeCacheKey,
  type NarrativeResponse,
} from '@/lib/prediction-explanation'
import type { FeatureContribution, RacePredictResult } from '@/lib/race-analysis-types'

type Props = {
  result: RacePredictResult
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
        <div key={feature.feature} className="space-y-1" title={feature.description || feature.feature}>
          <div className="flex items-center justify-between gap-3 text-xs">
            <span className="truncate text-[#ddd]">{feature.label}</span>
            <span className="shrink-0 tabular-nums" style={{ color }}>
              影響 {feature.impact_pct.toFixed(0)}%
            </span>
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
  const [narrative, setNarrative] = useState<NarrativeResponse | null>(null)
  const [narrativeLoading, setNarrativeLoading] = useState(false)
  const [narrativeError, setNarrativeError] = useState('')

  const prediction = candidates.find(candidate => candidate.horse_number === horseNumber) ?? candidates[0]
  const features = prediction?.explanation?.features ?? []
  const positive = features.filter(feature => feature.direction === 'positive')
  const negative = features.filter(feature => feature.direction === 'negative')
  const cacheKey = prediction
    ? narrativeCacheKey(result.race_info.race_id, result.model_id, prediction.horse_number)
    : ''

  useEffect(() => {
    setNarrativeError('')
    if (!cacheKey) {
      setNarrative(null)
      return
    }
    try {
      const cached = localStorage.getItem(cacheKey)
      setNarrative(cached ? JSON.parse(cached) as NarrativeResponse : null)
    } catch {
      setNarrative(null)
    }
  }, [cacheKey])

  if (!prediction) {
    return <div className="p-5 text-sm text-[#666]">説明できる予測がありません。</div>
  }

  const loadNarrative = async () => {
    setNarrativeLoading(true)
    setNarrativeError('')
    try {
      const response = await authFetch('/api/prediction-explanation', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          race_name: result.race_info.race_name,
          horse_name: prediction.horse_name,
          predicted_rank: prediction.predicted_rank,
          win_probability: prediction.win_probability,
          features: features.slice(0, 6).map(feature => ({
            label: feature.label,
            direction: feature.direction,
            impact_pct: feature.impact_pct,
          })),
        }),
      })
      const data = await response.json()
      if (!response.ok) throw new Error(data.detail || '解説を生成できませんでした')
      const next = data as NarrativeResponse
      setNarrative(next)
      try { localStorage.setItem(cacheKey, JSON.stringify(next)) } catch { /* session display only */ }
    } catch (error) {
      setNarrativeError(error instanceof Error ? error.message : '解説を生成できませんでした')
    } finally {
      setNarrativeLoading(false)
    }
  }

  return (
    <div className="p-5 space-y-5" data-testid="race-explanation-panel">
      <div>
        <h3 className="text-sm font-semibold text-white">AIの判断</h3>
        <p className="mt-1 text-xs text-[#666]">予測スコアに影響した主な要素</p>
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
          <p className="rounded-lg border border-[#222] bg-[#0c0c0c] px-4 py-3 text-sm leading-6 text-[#ddd]">
            {buildLocalPredictionExplanation(prediction)}
          </p>

          <div className="grid gap-5 md:grid-cols-2">
            <ContributionList title="評価を上げた要素" features={positive} tone="positive" />
            <ContributionList title="評価を下げた要素" features={negative} tone="negative" />
          </div>

          <div className="flex flex-wrap items-center gap-3 border-t border-[#1e1e1e] pt-4">
            <button
              type="button"
              onClick={loadNarrative}
              disabled={narrativeLoading}
              className="rounded border border-[#333] bg-[#171717] px-3 py-2 text-xs text-white hover:bg-[#222] disabled:opacity-50"
            >
              {narrativeLoading ? '生成中…' : narrative ? '解説を更新' : '文章で解説'}
            </button>
            <span className="text-[10px] text-[#555]">必要なときだけ生成します</span>
          </div>

          {narrative && (
            <div className="rounded-lg border border-[#223047] bg-[#0c1420] px-4 py-3">
              <div className="mb-1 text-[10px] text-[#60a5fa]">
                {narrative.source === 'fallback' ? '自動要約' : 'LLM解説'}
              </div>
              <p className="text-sm leading-6 text-[#dbeafe]">{narrative.explanation}</p>
            </div>
          )}
          {narrativeError && <p className="text-xs text-[#f87171]">{narrativeError}</p>}
        </>
      ) : (
        <div className="rounded-lg border border-[#2a2a2a] bg-[#111] px-4 py-5 text-sm text-[#777]">
          この予測には説明データがありません。次回の予測から表示されます。
        </div>
      )}

      <p className="text-[10px] text-[#555]">
        影響度はこの馬の予測スコア内の相対値です。結果を保証するものではありません。
      </p>
    </div>
  )
}
