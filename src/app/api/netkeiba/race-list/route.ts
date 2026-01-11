import { NextRequest, NextResponse } from 'next/server'
import * as cheerio from 'cheerio'

const USER_AGENT = 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36'
const BASE_URL = 'https://race.netkeiba.com'

/**
 * 特定日のレースID一覧を取得（効率化版）
 * レース一覧ページから直接race_idを抽出
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
    const url = `${BASE_URL}/top/race_list.html?kaisai_date=${dateStr}`

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

    const raceIds: string[] = []

    // HTMLから全てのrace_idリンクを抽出
    $('a[href*="race_id="]').each((_, element) => {
      const href = $(element).attr('href')
      if (href) {
        const match = href.match(/race_id=(\d+)/)
        if (match) {
          const raceId = match[1]
          if (!raceIds.includes(raceId)) {
            raceIds.push(raceId)
          }
        }
      }
    })

    return NextResponse.json({ 
      raceIds,
      count: raceIds.length 
    })
  } catch (error: any) {
    console.error('Netkeiba race list error:', error)
    return NextResponse.json(
      { error: 'レースIDの取得に失敗しました: ' + error.message },
      { status: 500 }
    )
  }
}
