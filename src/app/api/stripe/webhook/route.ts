import { NextRequest, NextResponse } from 'next/server'
import { stripe } from '@/lib/stripe'
import { supabase } from '@/lib/supabase'
import Stripe from 'stripe'

export async function POST(request: NextRequest) {
  try {
    if (!stripe || !supabase) {
      return NextResponse.json(
        { error: 'Stripe/Supabase設定が不足しています' },
        { status: 503 }
      )
    }

    const body = await request.text()
    const signature = request.headers.get('stripe-signature')

    if (!signature) {
      return NextResponse.json(
        { error: 'Stripe署名がありません' },
        { status: 400 }
      )
    }

    let event: Stripe.Event

    try {
      event = stripe.webhooks.constructEvent(
        body,
        signature,
        process.env.STRIPE_WEBHOOK_SECRET!
      )
    } catch (err: any) {
      console.error('Webhook signature verification failed:', err.message)
      return NextResponse.json(
        { error: 'Webhook署名の検証に失敗しました' },
        { status: 400 }
      )
    }

    // イベントタイプに応じて処理
    switch (event.type) {
      case 'checkout.session.completed': {
        const session = event.data.object as Stripe.Checkout.Session
        const userId = session.metadata?.supabaseUserId

        if (userId) {
          // プレミアムプランに更新
          await supabase
            .from('profiles')
            .update({
              subscription_tier: 'premium',
              stripe_subscription_id: session.subscription as string,
              ocr_monthly_limit: 1000,
            })
            .eq('id', userId)
        }
        break
      }

      case 'customer.subscription.updated': {
        const subscription = event.data.object as Stripe.Subscription
        const customerId = subscription.customer as string

        // 顧客IDからユーザーを検索
        const { data: profile } = await supabase
          .from('profiles')
          .select('id')
          .eq('stripe_customer_id', customerId)
          .single()

        if (profile) {
          const tier = subscription.status === 'active' ? 'premium' : 'free'
          const ocrLimit = tier === 'premium' ? 1000 : 10

          await supabase
            .from('profiles')
            .update({
              subscription_tier: tier,
              ocr_monthly_limit: ocrLimit,
            })
            .eq('id', profile.id)
        }
        break
      }

      case 'customer.subscription.deleted': {
        const subscription = event.data.object as Stripe.Subscription
        const customerId = subscription.customer as string

        const { data: profile } = await supabase
          .from('profiles')
          .select('id')
          .eq('stripe_customer_id', customerId)
          .single()

        if (profile) {
          // フリープランにダウングレード
          await supabase
            .from('profiles')
            .update({
              subscription_tier: 'free',
              stripe_subscription_id: null,
              ocr_monthly_limit: 10,
            })
            .eq('id', profile.id)
        }
        break
      }

      default:
        console.log(`Unhandled event type: ${event.type}`)
    }

    return NextResponse.json({ received: true })
  } catch (error: any) {
    console.error('Webhook error:', error)
    return NextResponse.json(
      { error: 'Webhook処理中にエラーが発生しました: ' + error.message },
      { status: 500 }
    )
  }
}
