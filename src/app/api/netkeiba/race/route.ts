import { NextRequest, NextResponse } from 'next/server'
import { createClient } from '@supabase/supabase-js'

// APIルート用のSupabaseクライアント（SERVICE_ROLE_KEYでRLSをバイパス）
const supabaseUrl = process.env.NEXT_PUBLIC_SUPABASE_URL
const supabaseServiceKey = process.env.SUPABASE_SERVICE_ROLE_KEY || process.env.NEXT_PUBLIC_SUPABASE_ANON_KEY
const supabase = (supabaseUrl && supabaseServiceKey) ? createClient(supabaseUrl, supabaseServiceKey) : null


/**
 * レース結果をスクレイピングしてDBに保存
 */
export async function POST(request: NextRequest) {
  try {
    if (!supabase) {
      return NextResponse.json(
        { error: 'Supabase設定が不足しています' },
        { status: 503 }
      )
    }

    const { raceId, userId, testOnly } = await request.json()

    if (!raceId || !userId) {
      return NextResponse.json(
        { error: 'レースIDとユーザーIDが必要です' },
        { status: 400 }
      )
    }

    // testOnlyモード: スクレイピングサービスの簡易チェック
    if (testOnly) {
      try {
        const scrapeResponse = await fetch('http://localhost:8001/scrape/ultimate', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ race_id: raceId, include_details: false }),
        })
        
        if (scrapeResponse.ok) {
          const data = await scrapeResponse.json()
          if (data.success && data.race_name) {
            return NextResponse.json({ success: true, exists: true })
          }
        }
        return NextResponse.json({ success: false, error: 'レースが存在しません' })
      } catch (error) {
        return NextResponse.json({ success: false, error: 'スクレイピングサービスに接続できません' })
      }
    }

    // フルスクレイピング: Ultimate版スクレイピングサービスを呼び出す
    const scrapeResponse = await fetch('http://localhost:8001/scrape/ultimate', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ race_id: raceId, include_details: false }),
    })

    if (!scrapeResponse.ok) {
      throw new Error(`Scraping service returned ${scrapeResponse.status}`)
    }

    const scrapeData = await scrapeResponse.json()

    if (!scrapeData.success) {
      return NextResponse.json({
        success: false,
        error: scrapeData.error || 'データ取得に失敗しました'
      })
    }

    // Ultimate版のレスポンス構造に対応
    const raceInfo = scrapeData.race_info || {}
    const raceTitle = raceInfo.race_name || ''
    const distance = raceInfo.distance || 0
    const trackType = raceInfo.track_type || ''
    const weather = raceInfo.weather || ''
    const fieldCondition = raceInfo.field_condition || ''
    const venue = raceInfo.venue || ''

    // レース情報をDBに保存
    const raceRecord: any = {
      race_id: raceId,
      race_name: raceTitle,
      venue,
      distance,
      track_type: trackType,
      weather,
      field_condition: fieldCondition,
    }
    
    if (userId && userId !== '00000000-0000-0000-0000-000000000000') {
      raceRecord.user_id = userId
    }
    
    const { error: raceError } = await supabase.from('races').upsert(raceRecord)

    if (raceError) throw raceError

    // スクレイピングサービスから取得した結果を保存
    const scrapedResults = scrapeData.results || []
    if (scrapedResults.length > 0) {
      const results = scrapedResults.map((result: any) => {
        // sex_ageを分解（例: "牡3" → sex: "牡", age: 3）
        const sexAge = result.sex_age || ''
        const sex = sexAge ? sexAge.charAt(0) : ''
        const age = sexAge ? parseInt(sexAge.substring(1)) || 0 : 0
        
        return {
          race_id: raceId,
          finish_position: parseInt(result.finish_position) || 0,
          bracket_number: parseInt(result.bracket_number) || 0,
          horse_number: parseInt(result.horse_number) || 0,
          horse_name: result.horse_name || '',
          sex,
          age,
          jockey_weight: parseFloat(result.jockey_weight) || 0,
          jockey_name: result.jockey_name || '',
          finish_time: result.finish_time || '',
          odds: parseFloat(result.odds) || 0,
          popularity: parseInt(result.popularity) || 0,
          ...(userId && userId !== '00000000-0000-0000-0000-000000000000' ? { user_id: userId } : {})
        }
      })

      const { error: resultsError } = await supabase
        .from('results')
        .insert(results)

      if (resultsError) throw resultsError
    }

    // 払い戻し情報を保存
    const scrapedPayouts = scrapeData.payouts || []
    if (scrapedPayouts.length > 0) {
      const payouts = scrapedPayouts.map((payout: any) => {
        // 金額から「円」と「,」を削除して数値に変換
        const amountStr = (payout.amount || payout.payout || '0').toString()
        const amount = parseInt(amountStr.replace(/[円,]/g, '')) || 0
        
        return {
          race_id: raceId,
          bet_type: payout.type || payout.bet_type || '',
          combination: payout.numbers || payout.combination || '',
          payout: amount,
          ...(userId && userId !== '00000000-0000-0000-0000-000000000000' ? { user_id: userId } : {})
        }
      })

      const { error: payoutsError } = await supabase
        .from('race_payouts')
        .insert(payouts)

      if (payoutsError) throw payoutsError
    }

    return NextResponse.json({
      success: true,
      raceId,
      resultsCount: scrapedResults.length,
      payoutsCount: scrapedPayouts.length,
    })
  } catch (error: any) {
    console.error('Scrape race error:', error)
    return NextResponse.json(
      { error: 'レース結果の取得に失敗しました: ' + error.message },
      { status: 500 }
    )
  }
}
