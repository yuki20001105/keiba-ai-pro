import { NextRequest, NextResponse } from 'next/server'
import { stripe } from '@/lib/stripe'
import { createSupabaseServiceClient, verifyRequestAuth } from '@/lib/server-auth'

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

    const { userId, customerId } = await request.json()
    const verifiedUserId = authz.context.user.id

    if (typeof userId === 'string' && userId.trim() && userId !== verifiedUserId) {
      return NextResponse.json({ error: 'userId mismatch is not allowed' }, { status: 403 })
    }

    // ユーザー情報を取得
    const { data: profile, error } = await supabase
      .from('profiles')
      .select('stripe_customer_id, stripe_subscription_id')
      .eq('id', verifiedUserId)
      .single()

    if (error || !profile.stripe_customer_id) {
      return NextResponse.json(
        { error: 'ユーザー情報が見つかりません' },
        { status: 404 }
      )
    }

    if (typeof customerId === 'string' && customerId.trim() && customerId !== profile.stripe_customer_id) {
      return NextResponse.json(
        { error: 'customer mismatch is not allowed' },
        { status: 403 }
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
