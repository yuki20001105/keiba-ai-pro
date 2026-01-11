import { NextRequest, NextResponse } from 'next/server'
import OpenAI from 'openai'
import { GoogleGenerativeAI } from '@google/generative-ai'

const openai = process.env.OPENAI_API_KEY ? new OpenAI({
  apiKey: process.env.OPENAI_API_KEY,
}) : null

const genAI = process.env.GOOGLE_GEMINI_API_KEY ? new GoogleGenerativeAI(process.env.GOOGLE_GEMINI_API_KEY) : null

export async function POST(request: NextRequest) {
  try {
    const { extractedText, provider = 'openai' } = await request.json()

    if (!extractedText) {
      return NextResponse.json(
        { error: '抽出テキストが必要です' },
        { status: 400 }
      )
    }

    let correctedData

    if (provider === 'openai') {
      if (!openai) {
        return NextResponse.json(
          { error: 'OpenAI APIキーが設定されていません' },
          { status: 503 }
        )
      }
      correctedData = await correctWithOpenAI(extractedText)
    } else if (provider === 'gemini') {
      if (!genAI) {
        return NextResponse.json(
          { error: 'Gemini APIキーが設定されていません' },
          { status: 503 }
        )
      }
      correctedData = await correctWithGemini(extractedText)
    } else {
      return NextResponse.json(
        { error: '無効なプロバイダーです' },
        { status: 400 }
      )
    }

    return NextResponse.json(correctedData)
  } catch (error: any) {
    console.error('AI correction error:', error)
    return NextResponse.json(
      { error: 'AI補正中にエラーが発生しました: ' + error.message },
      { status: 500 }
    )
  }
}

async function correctWithOpenAI(text: string) {
  if (!openai) {
    throw new Error('OpenAI client not initialized')
  }
  
  const prompt = `
以下は競馬の馬券から抽出されたOCRテキストです。
このテキストを解析して、以下のJSON形式で正確な情報を抽出してください。

- raceName: レース名（例: 第1レース、1R）
- raceDate: 日付（YYYY-MM-DD形式）
- betType: 馬券種別（単勝、複勝、馬連、馬単、ワイド、三連複、三連単）
- horses: 馬番の配列（BOXやフォーメーションの場合は全組み合わせを展開）
- betAmount: 賭け金（円）
- odds: オッズ（あれば）

BOX買いの場合: 例えば「1-2-3 BOX」なら、すべての組み合わせを列挙してください。
フォーメーションの場合: 「1-2,3-4,5」なら1着候補、2着候補、3着候補を分けて返してください。

OCRテキスト:
${text}

必ずJSON形式で返してください。わからない項目はnullにしてください。
  `

  const response = await openai.chat.completions.create({
    model: 'gpt-4',
    messages: [
      {
        role: 'system',
        content: 'あなたは競馬の馬券情報を正確に解析するAIアシスタントです。',
      },
      {
        role: 'user',
        content: prompt,
      },
    ],
    temperature: 0.1,
    response_format: { type: 'json_object' },
  })

  const content = response.choices[0]?.message?.content
  if (!content) throw new Error('OpenAI response is empty')

  return JSON.parse(content)
}

async function correctWithGemini(text: string) {
  if (!genAI) {
    throw new Error('Gemini client not initialized')
  }
  
  const model = genAI.getGenerativeModel({ model: 'gemini-pro' })

  const prompt = `
以下は競馬の馬券から抽出されたOCRテキストです。
このテキストを解析して、以下のJSON形式で正確な情報を抽出してください。

- raceName: レース名（例: 第1レース、1R）
- raceDate: 日付（YYYY-MM-DD形式）
- betType: 馬券種別（単勝、複勝、馬連、馬単、ワイド、三連複、三連単）
- horses: 馬番の配列（BOXやフォーメーションの場合は全組み合わせを展開）
- betAmount: 賭け金（円）
- odds: オッズ（あれば）

BOX買いの場合: 例えば「1-2-3 BOX」なら、すべての組み合わせを列挙してください。
フォーメーションの場合: 「1-2,3-4,5」なら1着候補、2着候補、3着候補を分けて返してください。

OCRテキスト:
${text}

必ずJSON形式のみで返してください。説明文は不要です。
  `

  const result = await model.generateContent(prompt)
  const response = await result.response
  const content = response.text()

  // JSONを抽出（マークダウンのコードブロックを除去）
  const jsonMatch = content.match(/\{[\s\S]*\}/)
  if (!jsonMatch) throw new Error('Gemini response does not contain valid JSON')

  return JSON.parse(jsonMatch[0])
}
