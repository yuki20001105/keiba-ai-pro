import { NextRequest, NextResponse } from 'next/server'

const ML_API_URL = process.env.ML_API_URL || 'http://localhost:8000'

export async function POST(request: NextRequest) {
  try {
    const body = await request.json()
    
    const {
      modelId,
      horses,
    } = body

    if (!horses || !Array.isArray(horses) || horses.length === 0) {
      return NextResponse.json(
        { error: '馬のデータが必要です' },
        { status: 400 }
      )
    }

    // FastAPI機械学習サーバーにリクエスト
    const response = await fetch(`${ML_API_URL}/api/predict`, {
      method: 'POST',
      headers: {
        'Content-Type': 'application/json',
      },
      body: JSON.stringify({
        model_id: modelId || null,
        horses,
      }),
    })

    if (!response.ok) {
      const error = await response.json()
      return NextResponse.json(
        { error: error.detail || '予測に失敗しました' },
        { status: response.status }
      )
    }

    const data = await response.json()
    
    return NextResponse.json({
      success: true,
      predictions: data.predictions,
      modelId: data.model_id,
      message: data.message,
    })
  } catch (error) {
    console.error('Prediction API error:', error)
    
    // FastAPIサーバーが起動していない場合
    if (error instanceof TypeError && error.message.includes('fetch')) {
      return NextResponse.json(
        { 
          error: 'Python機械学習サーバーに接続できません。python-api/main.pyを起動してください。',
          hint: 'ターミナルで以下を実行: cd python-api && pip install -r requirements.txt && python main.py'
        },
        { status: 503 }
      )
    }
    
    return NextResponse.json(
      { error: '予測中にエラーが発生しました' },
      { status: 500 }
    )
  }
}
