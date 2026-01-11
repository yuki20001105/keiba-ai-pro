import { NextRequest, NextResponse } from 'next/server'
import vision from '@google-cloud/vision'
import { supabase } from '@/lib/supabase'

// Google Vision APIクライアント
const visionClient = new vision.ImageAnnotatorClient({
  keyFilename: process.env.GOOGLE_APPLICATION_CREDENTIALS,
})

export async function POST(request: NextRequest) {
  try {
    if (!supabase) {
      return NextResponse.json(
        { error: 'Supabase設定が不足しています' },
        { status: 503 }
      )
    }

    const formData = await request.formData()
    const image = formData.get('image') as File
    const userId = formData.get('userId') as string

    if (!image) {
      return NextResponse.json(
        { error: '画像ファイルが必要です' },
        { status: 400 }
      )
    }

    // ユーザーのOCR利用制限をチェック
    if (userId) {
      const { data: profile, error } = await supabase
        .from('profiles')
        .select('ocr_monthly_limit, ocr_used_this_month, ocr_reset_date')
        .eq('id', userId)
        .single()

      if (error) {
        return NextResponse.json(
          { error: 'ユーザー情報の取得に失敗しました' },
          { status: 500 }
        )
      }

      // 月次リセットチェック
      const resetDate = new Date(profile.ocr_reset_date)
      const now = new Date()
      const monthDiff = (now.getFullYear() - resetDate.getFullYear()) * 12 + 
                        (now.getMonth() - resetDate.getMonth())

      if (monthDiff >= 1) {
        // リセット
        await supabase
          .from('profiles')
          .update({ 
            ocr_used_this_month: 0, 
            ocr_reset_date: now.toISOString() 
          })
          .eq('id', userId)
      } else if (profile.ocr_used_this_month >= profile.ocr_monthly_limit) {
        return NextResponse.json(
          { error: '月間OCR利用制限に達しました。プレミアムプランにアップグレードしてください。' },
          { status: 429 }
        )
      }
    }

    // 画像をバッファに変換
    const bytes = await image.arrayBuffer()
    const buffer = Buffer.from(bytes)

    // Google Vision APIでOCR実行
    const [result] = await visionClient.textDetection(buffer)
    const detections = result.textAnnotations

    if (!detections || detections.length === 0) {
      return NextResponse.json(
        { error: 'テキストが検出されませんでした' },
        { status: 400 }
      )
    }

    const extractedText = detections[0].description || ''

    // 馬券情報を抽出（簡易パーサー）
    const betInfo = parseBetTicket(extractedText)

    // OCR使用回数を増加
    if (userId) {
      // 現在の使用回数を取得
      const { data: profile } = await supabase
        .from('profiles')
        .select('ocr_used_this_month')
        .eq('id', userId)
        .single()

      // 使用回数を増加
      await supabase
        .from('profiles')
        .update({ 
          ocr_used_this_month: (profile?.ocr_used_this_month || 0) + 1
        })
        .eq('id', userId)

      // OCR使用履歴を記録
      await supabase.from('ocr_usage').insert({
        user_id: userId,
        extracted_text: extractedText,
        corrected_data: betInfo,
        success: true,
      })
    }

    return NextResponse.json({
      extractedText,
      betInfo,
      needsCorrection: !betInfo.confidence || betInfo.confidence < 0.8,
    })
  } catch (error: any) {
    console.error('OCR error:', error)
    return NextResponse.json(
      { error: 'OCR処理中にエラーが発生しました: ' + error.message },
      { status: 500 }
    )
  }
}

/**
 * 馬券テキストをパース
 */
function parseBetTicket(text: string): {
  raceName?: string
  raceDate?: string
  betType?: string
  horses?: number[]
  betAmount?: number
  odds?: number
  confidence: number
} {
  const result: any = { confidence: 0 }

  // レース名を抽出
  const raceNameMatch = text.match(/第\d+レース|(\d+)R/)
  if (raceNameMatch) {
    result.raceName = raceNameMatch[0]
    result.confidence += 0.2
  }

  // 馬券タイプを抽出
  const betTypeMatch = text.match(/(単勝|複勝|馬連|馬単|ワイド|三連複|三連単)/)
  if (betTypeMatch) {
    result.betType = betTypeMatch[0]
    result.confidence += 0.2
  }

  // 馬番を抽出
  const horseNumbers = text.match(/\d+番/g)
  if (horseNumbers) {
    result.horses = horseNumbers.map(h => parseInt(h.replace('番', '')))
    result.confidence += 0.2
  }

  // 金額を抽出
  const amountMatch = text.match(/(\d{2,})円/)
  if (amountMatch) {
    result.betAmount = parseInt(amountMatch[1])
    result.confidence += 0.2
  }

  // オッズを抽出
  const oddsMatch = text.match(/(\d+\.\d+)倍/)
  if (oddsMatch) {
    result.odds = parseFloat(oddsMatch[1])
    result.confidence += 0.2
  }

  return result
}
