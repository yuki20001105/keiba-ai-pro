import Stripe from 'stripe'

export const stripe = process.env.STRIPE_SECRET_KEY 
  ? new Stripe(process.env.STRIPE_SECRET_KEY, {
      apiVersion: '2023-10-16',
      typescript: true,
    })
  : null as any

export const PLANS = {
  FREE: {
    name: 'Free',
    ocrLimit: 10,
    price: 0,
  },
  PREMIUM: {
    name: 'Premium',
    ocrLimit: 1000,
    price: 1980,
    priceId: process.env.STRIPE_PRICE_ID_PREMIUM,
  },
}
