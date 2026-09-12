import { NextRequest, NextResponse } from 'next/server'
import OpenAI from 'openai'
import { GoogleGenerativeAI } from '@google/generative-ai'
import { verifyRequestAuth } from '@/lib/server-auth'

export const maxDuration = 30

type ExplanationFeature = {
  label: string
  direction: 'positive' | 'negative'
  impact_pct: number
}

type ExplanationInput = {
  race_name: string
  horse_name: string
  predicted_rank: number
  features: ExplanationFeature[]
}

function sanitizeInput(raw: unknown): ExplanationInput | null {
  if (!raw || typeof raw !== 'object') return null
  const value = raw as Record<string, unknown>
  const horseName = typeof value.horse_name === 'string' ? value.horse_name.trim().slice(0, 80) : ''
  const raceName = typeof value.race_name === 'string' ? value.race_name.trim().slice(0, 100) : ''
  const rank = Number(value.predicted_rank)
  const rawFeatures = Array.isArray(value.features) ? value.features.slice(0, 6) : []
  const features = rawFeatures.flatMap(item => {
    if (!item || typeof item !== 'object') return []
    const feature = item as Record<string, unknown>
    const label = typeof feature.label === 'string' ? feature.label.trim().slice(0, 40) : ''
    const direction = feature.direction === 'negative' ? 'negative' : feature.direction === 'positive' ? 'positive' : null
    const impact = Number(feature.impact_pct)
    if (!label || !direction || !Number.isFinite(impact) || impact < 0 || impact > 100) return []
    return [{ label, direction, impact_pct: Math.round(impact * 10) / 10 } satisfies ExplanationFeature]
  })

  if (!horseName || !Number.isInteger(rank) || rank < 1 || rank > 99 || features.length === 0) return null
  return {
    race_name: raceName,
    horse_name: horseName,
    predicted_rank: rank,
    features,
  }
}

function fallbackExplanation(input: ExplanationInput): string {
  const positive = input.features.filter(feature => feature.direction === 'positive').slice(0, 2)
  const negative = input.features.filter(feature => feature.direction === 'negative').slice(0, 1)
  const parts = [`${input.horse_name}を${input.predicted_rank}位と予測。`]
  if (positive.length > 0) parts.push(`${positive.map(feature => feature.label).join('・')}が速度スコアを上げています。`)
  if (negative.length > 0) parts.push(`${negative.map(feature => feature.label).join('・')}は速度スコアを下げています。`)
  return parts.join('')
}

function buildPrompt(input: ExplanationInput): string {
  return [
    '以下のTreeSHAP寄与度だけを根拠に、競馬予測の説明を日本語で作成してください。',
    '専門用語を避け、2文・140文字以内にしてください。予測の保証や購入推奨はしないでください。',
    'positiveは速度スコアを上げた要素、negativeは下げた要素です。勝率への寄与とは表現しないでください。',
    '予測順位は速度スコアのレース内比較です。数値や事実を推測で追加しないでください。',
    JSON.stringify(input),
  ].join('\n')
}

async function explainWithOpenAI(input: ExplanationInput): Promise<string | null> {
  const apiKey = process.env.OPENAI_API_KEY
  if (!apiKey) return null
  const client = new OpenAI({ apiKey })
  const response = await client.chat.completions.create({
    model: process.env.OPENAI_EXPLANATION_MODEL || process.env.OPENAI_MODEL || 'gpt-4o-mini',
    messages: [
      {
        role: 'system',
        content: 'あなたは機械学習の予測根拠を、誇張せず簡潔に説明するアシスタントです。',
      },
      { role: 'user', content: buildPrompt(input) },
    ],
    temperature: 0.2,
    max_tokens: 180,
  })
  return response.choices[0]?.message?.content?.trim() || null
}

async function explainWithGemini(input: ExplanationInput): Promise<string | null> {
  const apiKey = process.env.GOOGLE_GEMINI_API_KEY
  if (!apiKey) return null
  const client = new GoogleGenerativeAI(apiKey)
  const model = client.getGenerativeModel({
    model: process.env.GEMINI_EXPLANATION_MODEL || 'gemini-pro',
  })
  const response = await model.generateContent(buildPrompt(input))
  return (await response.response).text().trim() || null
}

export async function POST(request: NextRequest) {
  const authz = await verifyRequestAuth(request, { requirePremiumOrAdmin: true })
  if (!authz.ok) {
    return NextResponse.json({ detail: authz.detail }, { status: authz.status })
  }

  try {
    const input = sanitizeInput(await request.json())
    if (!input) {
      return NextResponse.json({ detail: '説明データが不正です' }, { status: 400 })
    }

    try {
      const explanation = await explainWithOpenAI(input)
      if (explanation) return NextResponse.json({ explanation, source: 'openai' })
    } catch (error) {
      console.warn('OpenAI prediction explanation failed:', error)
    }

    try {
      const explanation = await explainWithGemini(input)
      if (explanation) return NextResponse.json({ explanation, source: 'gemini' })
    } catch (error) {
      console.warn('Gemini prediction explanation failed:', error)
    }

    return NextResponse.json({ explanation: fallbackExplanation(input), source: 'fallback' })
  } catch {
    return NextResponse.json({ detail: '解説を生成できませんでした' }, { status: 500 })
  }
}
