import { createClient } from '@supabase/supabase-js'

const supabaseUrl = process.env.NEXT_PUBLIC_SUPABASE_URL
const supabaseAnonKey = process.env.NEXT_PUBLIC_SUPABASE_ANON_KEY

export const supabase = (supabaseUrl && supabaseAnonKey) 
  ? createClient(supabaseUrl, supabaseAnonKey)
  : null as any

export type Profile = {
  id: string
  email: string
  full_name?: string
  subscription_tier: 'free' | 'premium'
  stripe_customer_id?: string
  stripe_subscription_id?: string
  ocr_monthly_limit: number
  ocr_used_this_month: number
  ocr_reset_date: string
  created_at: string
  updated_at: string
}

export type Prediction = {
  id: string
  user_id: string
  race_name: string
  race_date: string
  horse_data: any
  predicted_results: any
  confidence_score?: number
  bet_type?: string
  created_at: string
}

export type Bet = {
  id: string
  user_id: string
  prediction_id?: string
  race_name: string
  race_date: string
  bet_type: string
  bet_amount: number
  odds?: number
  actual_result?: any
  payout: number
  profit_loss?: number
  ocr_scanned: boolean
  scanned_image_url?: string
  created_at: string
}

export type BankRecord = {
  id: string
  user_id: string
  initial_bank: number
  current_bank: number
  total_bet: number
  total_return: number
  roi: number
  recovery_rate: number
  created_at: string
  updated_at: string
}

export type OCRUsage = {
  id: string
  user_id: string
  image_url?: string
  extracted_text?: string
  corrected_data?: any
  success: boolean
  error_message?: string
  created_at: string
}
