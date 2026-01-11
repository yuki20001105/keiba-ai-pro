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

    const { userId, priceId } = await request.json()

    if (!userId || !priceId) {
      return NextResponse.json(
        { error: 'ユーザーIDと価格IDが必要です' },
        { status: 400 }
      )
    }

    // ユーザー情報を取得
    const { data: profile, error: profileError } = await supabase
      .from('profiles')
      .select('stripe_customer_id, email')
      .eq('id', userId)
      .single()

    if (profileError) {
      return NextResponse.json(
        { error: 'ユーザー情報の取得に失敗しました' },
        { status: 500 }
      )
    }

    let customerId = profile.stripe_customer_id

    // Stripe顧客が存在しない場合は作成
    if (!customerId) {
      const customer = await stripe.customers.create({
        email: profile.email,
        metadata: {
          supabaseUserId: userId,
        },
      })
      customerId = customer.id

      // Supabaseに保存
      await supabase
        .from('profiles')
        .update({ stripe_customer_id: customerId })
        .eq('id', userId)
    }

    // Checkoutセッションを作成
    const session = await stripe.checkout.sessions.create({
      customer: customerId,
      mode: 'subscription',
      payment_method_types: ['card'],
      line_items: [
        {
          price: priceId,
          quantity: 1,
        },
      ],
      success_url: `${process.env.NEXT_PUBLIC_APP_URL}/dashboard?success=true`,
      cancel_url: `${process.env.NEXT_PUBLIC_APP_URL}/dashboard?canceled=true`,
      metadata: {
        supabaseUserId: userId,
      },
    })

    return NextResponse.json({ sessionId: session.id, url: session.url })
  } catch (error: any) {
    console.error('Checkout error:', error)
    return NextResponse.json(
      { error: 'チェックアウトセッションの作成に失敗しました: ' + error.message },
      { status: 500 }
    )
  }
}
