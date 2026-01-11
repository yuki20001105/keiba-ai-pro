import { NextRequest, NextResponse } from 'next/server'
import { KeibaAI, HorseData } from '@/lib/keiba-ai'
import { supabase } from '@/lib/supabase'

export async function POST(request: NextRequest) {
  try {
    const { horses, userId, raceName, raceDate } = await request.json()

    if (!horses || !Array.isArray(horses) || horses.length === 0) {
      return NextResponse.json(
        { error: '馬のデータが必要です' },
        { status: 400 }
      )
    }

    // AI予測を実行
    const ai = new KeibaAI()
    const predictions = ai.predict(horses as HorseData[])
    const recommendations = ai.recommendBetType(predictions)

    // Supabaseに保存（ユーザーIDがある場合）
    if (userId) {
      const { error } = await supabase.from('predictions').insert({
        user_id: userId,
        race_name: raceName || '未設定',
        race_date: raceDate || new Date().toISOString().split('T')[0],
        horse_data: horses,
        predicted_results: predictions,
        confidence_score: predictions[0]?.confidenceScore || 0,
        bet_type: recommendations[0]?.betType || null,
      })

      if (error) {
        console.error('Supabase insert error:', error)
      }
    }

    return NextResponse.json({
      predictions,
      recommendations,
    })
  } catch (error) {
    console.error('Prediction error:', error)
    return NextResponse.json(
      { error: '予測中にエラーが発生しました' },
      { status: 500 }
    )
  }
}
