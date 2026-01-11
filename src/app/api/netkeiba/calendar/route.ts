import { NextRequest, NextResponse } from 'next/server'
import * as cheerio from 'cheerio'

const USER_AGENT = 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36'
const BASE_URL = 'https://race.netkeiba.com'
const REQUEST_INTERVAL = 3000 // 3 seconds

// レート制限用のキュー
let lastRequestTime = 0

async function waitForRateLimit() {
  const now = Date.now()
  const timeSinceLastRequest = now - lastRequestTime
  
  if (timeSinceLastRequest < REQUEST_INTERVAL) {
    await new Promise(resolve => setTimeout(resolve, REQUEST_INTERVAL - timeSinceLastRequest))
  }
  
  lastRequestTime = Date.now()
}

/**
 * 開催日一覧を取得
 * includeRaces=true の場合、各日のレースID情報も含める
 */
export async function GET(request: NextRequest) {
  try {
    const { searchParams } = new URL(request.url)
    const year = searchParams.get('year')
    const month = searchParams.get('month')
    const includeRaces = searchParams.get('includeRaces') === 'true'

    if (!year || !month) {
      return NextResponse.json(
        { error: '年月が必要です' },
        { status: 400 }
      )
    }

    await waitForRateLimit()

    const url = `${BASE_URL}/top/calendar.html?year=${year}&month=${month}`
    const response = await fetch(url, {
      headers: {
        'User-Agent': USER_AGENT,
      },
    })

    if (!response.ok) {
      throw new Error(`HTTP ${response.status}`)
    }

    const html = await response.text()
    const $ = cheerio.load(html)

    const dates: string[] = []
    const racesByDate: { [date: string]: string[] } = {}

    // カレンダーから開催日とレースIDを抽出
    $('.RaceCellBox a').each((_, element) => {
      const href = $(element).attr('href')
      if (href) {
        // 開催日を抽出
        const dateMatch = href.match(/kaisai_date=(\d{8})/)
        if (dateMatch) {
          const dateStr = dateMatch[1]
          // YYYYMMDD → YYYY-MM-DD
          const formattedDate = `${dateStr.slice(0, 4)}-${dateStr.slice(4, 6)}-${dateStr.slice(6, 8)}`
          if (!dates.includes(formattedDate)) {
            dates.push(formattedDate)
          }
        }
        
        // レースIDを抽出（オプション）
        if (includeRaces) {
          const raceMatch = href.match(/race_id=(\d+)/)
          if (raceMatch && dateMatch) {
            const raceId = raceMatch[1]
            const dateStr = dateMatch[1]
            const formattedDate = `${dateStr.slice(0, 4)}-${dateStr.slice(4, 6)}-${dateStr.slice(6, 8)}`
            
            if (!racesByDate[formattedDate]) {
              racesByDate[formattedDate] = []
            }
            if (!racesByDate[formattedDate].includes(raceId)) {
              racesByDate[formattedDate].push(raceId)
            }
          }
        }
      }
    })

    if (includeRaces) {
      return NextResponse.json({ 
        dates: dates.sort(),
        racesByDate 
      })
    }

    return NextResponse.json({ dates: dates.sort() })
  } catch (error: any) {
    console.error('Netkeiba calendar error:', error)
    return NextResponse.json(
      { error: '開催日の取得に失敗しました: ' + error.message },
      { status: 500 }
    )
  }
}

/**
 * 特定日のレースID一覧を取得
 * 注意: この機能は簡略化のため、実装をスキップします
 * 代わりに、一括取得機能で開催日から直接レースを列挙します
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

    // この機能は使用されないため、空の配列を返す
    // 一括取得機能では開催日から直接レースIDを生成します
    return NextResponse.json({ 
      races: [],
      message: 'この機能は使用されません。期間指定一括取得機能を使用してください。'
    })
  } catch (error: any) {
    console.error('Netkeiba race list error:', error)
    return NextResponse.json(
      { error: 'レース一覧の取得に失敗しました: ' + error.message },
      { status: 500 }
    )
  }
}
