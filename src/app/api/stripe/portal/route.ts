import { NextRequest, NextResponse } from 'next/server'
import { stripe } from '@/lib/stripe'
import { supabase } from '@/lib/supabase'

export async function POST(request: NextRequest) {
  try {
    if (!stripe || !supabase) {
      return NextResponse.json(
        { error: 'Stripe/Supabase設定が不足しています' },
        { status: 503 }
      )
    }

    const { userId } = await request.json()

    if (!userId) {
      return NextResponse.json(
        { error: 'ユーザーIDが必要です' },
        { status: 400 }
      )
    }

    // ユーザー情報を取得
    const { data: profile, error } = await supabase
      .from('profiles')
      .select('stripe_customer_id, stripe_subscription_id')
      .eq('id', userId)
      .single()

    if (error || !profile.stripe_customer_id) {
      return NextResponse.json(
        { error: 'ユーザー情報が見つかりません' },
        { status: 404 }
      )
    }

    // Stripeのポータルセッションを作成
    const session = await stripe.billingPortal.sessions.create({
      customer: profile.stripe_customer_id,
      return_url: `${process.env.NEXT_PUBLIC_APP_URL}/dashboard`,
    })

    return NextResponse.json({ url: session.url })
  } catch (error: any) {
    console.error('Portal session error:', error)
    return NextResponse.json(
      { error: 'ポータルセッションの作成に失敗しました: ' + error.message },
      { status: 500 }
    )
  }
}
