import { NextRequest, NextResponse } from 'next/server'

const ML_API_URL = process.env.ML_API_URL || 'http://localhost:8000'

export async function POST(request: NextRequest) {
  try {
    const body = await request.json()
    
    const {
      target = 'win',
      modelType = 'logistic_regression',
      testSize = 0.2,
      cvFolds = 5,
    } = body

    // FastAPI機械学習サーバーにリクエスト
    const response = await fetch(`${ML_API_URL}/api/train`, {
      method: 'POST',
      headers: {
        'Content-Type': 'application/json',
      },
      body: JSON.stringify({
        target,
        model_type: modelType,
        test_size: testSize,
        cv_folds: cvFolds,
        use_sqlite: true,
      }),
    })

    if (!response.ok) {
      const error = await response.json()
      return NextResponse.json(
        { error: error.detail || 'モデル学習に失敗しました' },
        { status: response.status }
      )
    }

    const data = await response.json()
    
    return NextResponse.json({
      success: true,
      modelId: data.model_id,
      modelPath: data.model_path,
      metrics: data.metrics,
      dataCount: data.data_count,
      raceCount: data.race_count,
      featureCount: data.feature_count,
      trainingTime: data.training_time,
      message: data.message,
    })
  } catch (error) {
    console.error('Training API error:', error)
    
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
      { error: 'モデル学習中にエラーが発生しました' },
      { status: 500 }
    )
  }
}
