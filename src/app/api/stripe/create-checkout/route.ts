import { NextRequest, NextResponse } from 'next/server'
import { stripe } from '@/lib/stripe'
import { PLANS } from '@/lib/stripe'
import { createSupabaseServiceClient, verifyRequestAuth } from '@/lib/server-auth'

function getAllowedPriceIds(): Set<string> {
  const values = [
    PLANS.PREMIUM.priceId,
    process.env.STRIPE_PRICE_ID_PREMIUM,
    ...(process.env.STRIPE_ALLOWED_PRICE_IDS || '').split(',').map(v => v.trim()),
  ]
  return new Set(values.filter((v): v is string => !!v))
}

export async function POST(request: NextRequest) {
  try {
    const authz = await verifyRequestAuth(request)
    if (!authz.ok) {
      return NextResponse.json({ detail: authz.detail }, { status: authz.status })
    }

    const supabase = createSupabaseServiceClient()
    if (!stripe || !supabase) {
      return NextResponse.json(
        { error: 'Stripe/Supabase設定が不足しています' },
        { status: 503 }
      )
    }

    const { userId, priceId, customerId: bodyCustomerId } = await request.json()
    const verifiedUserId = authz.context.user.id

    if (!priceId) {
      return NextResponse.json(
        { error: '価格IDが必要です' },
        { status: 400 }
      )
    }

    if (typeof userId === 'string' && userId.trim() && userId !== verifiedUserId) {
      return NextResponse.json(
        { error: 'userId mismatch is not allowed' },
        { status: 403 }
      )
    }

    if (typeof bodyCustomerId === 'string' && bodyCustomerId.trim()) {
      return NextResponse.json(
        { error: 'customerId must not be supplied by client' },
        { status: 403 }
      )
    }

    const allowedPriceIds = getAllowedPriceIds()
    if (!allowedPriceIds.has(String(priceId))) {
      return NextResponse.json(
        { error: '許可されていないpriceIdです' },
        { status: 400 }
      )
    }

    // ユーザー情報を取得
    const { data: profile, error: profileError } = await supabase
      .from('profiles')
      .select('stripe_customer_id, email')
      .eq('id', verifiedUserId)
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
          supabaseUserId: verifiedUserId,
        },
      })
      customerId = customer.id

      // Supabaseに保存
      await supabase
        .from('profiles')
        .update({ stripe_customer_id: customerId })
        .eq('id', verifiedUserId)
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
        supabaseUserId: verifiedUserId,
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
