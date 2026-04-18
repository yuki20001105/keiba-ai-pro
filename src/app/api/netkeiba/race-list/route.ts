import { NextRequest, NextResponse } from 'next/server'
import { SCRAPE_SERVICE_URL } from '@/lib/backend-url'

/**
 * 特定日のレースID一覧を取得
 * Python APIのスクレイピングサービス（undetected_chromedriver使用）を利用
 */
export async function POST(request: NextRequest) {
  try {
    const { date } = await request.json()

    if (!date) {
      return NextResponse.json(
        { error: '日付が必要です' },
        { status: 400 }
      )
    }

    // YYYY-MM-DD → YYYYMMDD
    const dateStr = date.replace(/-/g, '')
    
    console.log(`[race-list] Fetching race list for date: ${dateStr}`)

    // Python APIの/scrape/race_listエンドポイントを呼び出し
    // このAPIはundetected_chromedriverを使用してJavaScript動的生成に対応
    const response = await fetch(`${SCRAPE_SERVICE_URL}/scrape/race_list`, {
      method: 'POST',
      headers: {
        'Content-Type': 'application/json',
      },
      body: JSON.stringify({
        kaisai_date: dateStr
      })
    })

    if (!response.ok) {
      const errorText = await response.text()
      console.error(`[race-list] Python API error: ${response.status} - ${errorText}`)
      throw new Error(`Python API returned ${response.status}`)
    }

    const data = await response.json()
    
    console.log(`[race-list] Response from Python API:`, data)
    console.log(`[race-list] Found ${data.races?.length || 0} races for ${dateStr}`)

    // Python APIのレスポンス形式: { races: [...], source: "scraping" }
    const raceIds = data.races || []

    return NextResponse.json({ 
      raceIds,
      count: raceIds.length 
    })
  } catch (error: any) {
    console.error('[race-list] Error:', error)
    return NextResponse.json(
      { error: 'レースIDの取得に失敗しました: ' + error.message },
      { status: 500 }
    )
  }
}
