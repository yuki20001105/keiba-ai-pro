import { NextRequest, NextResponse } from 'next/server'

const ML_API_URL = process.env.ML_API_URL || 'http://localhost:8000'

export async function GET(request: NextRequest) {
  try {
    // FastAPI機械学習サーバーにリクエスト
    const response = await fetch(`${ML_API_URL}/api/models`, {
      method: 'GET',
      headers: {
        'Content-Type': 'application/json',
      },
    })

    if (!response.ok) {
      const error = await response.json()
      return NextResponse.json(
        { error: error.detail || 'モデル一覧の取得に失敗しました' },
        { status: response.status }
      )
    }

    const data = await response.json()
    
    return NextResponse.json(data)
  } catch (error) {
    console.error('Models API error:', error)
    
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
      { error: 'モデル一覧の取得中にエラーが発生しました' },
      { status: 500 }
    )
  }
}
