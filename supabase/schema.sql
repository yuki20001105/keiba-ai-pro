-- Enable UUID extension
CREATE EXTENSION IF NOT EXISTS "uuid-ossp";

-- Users table (extends Supabase auth.users)
CREATE TABLE public.profiles (
  id UUID REFERENCES auth.users ON DELETE CASCADE PRIMARY KEY,
  email TEXT UNIQUE NOT NULL,
  full_name TEXT,
  subscription_tier TEXT DEFAULT 'free' CHECK (subscription_tier IN ('free', 'premium')),
  stripe_customer_id TEXT UNIQUE,
  stripe_subscription_id TEXT UNIQUE,
  ocr_monthly_limit INTEGER DEFAULT 10,
  ocr_used_this_month INTEGER DEFAULT 0,
  ocr_reset_date TIMESTAMP WITH TIME ZONE DEFAULT NOW(),
  created_at TIMESTAMP WITH TIME ZONE DEFAULT NOW(),
  updated_at TIMESTAMP WITH TIME ZONE DEFAULT NOW()
);

-- Predictions table (競馬予測履歴)
CREATE TABLE public.predictions (
  id UUID DEFAULT uuid_generate_v4() PRIMARY KEY,
  user_id UUID REFERENCES public.profiles(id) ON DELETE CASCADE NOT NULL,
  race_name TEXT NOT NULL,
  race_date DATE NOT NULL,
  horse_data JSONB NOT NULL,
  predicted_results JSONB NOT NULL,
  confidence_score NUMERIC(5,2),
  bet_type TEXT,
  created_at TIMESTAMP WITH TIME ZONE DEFAULT NOW()
);

-- Bets table (賭け履歴)
CREATE TABLE public.bets (
  id UUID DEFAULT uuid_generate_v4() PRIMARY KEY,
  user_id UUID REFERENCES public.profiles(id) ON DELETE CASCADE NOT NULL,
  prediction_id UUID REFERENCES public.predictions(id) ON DELETE SET NULL,
  race_name TEXT NOT NULL,
  race_date DATE NOT NULL,
  bet_type TEXT NOT NULL,
  bet_amount INTEGER NOT NULL,
  odds NUMERIC(10,2),
  actual_result JSONB,
  payout INTEGER DEFAULT 0,
  profit_loss INTEGER,
  ocr_scanned BOOLEAN DEFAULT false,
  scanned_image_url TEXT,
  created_at TIMESTAMP WITH TIME ZONE DEFAULT NOW()
);

-- Bank management (資金管理)
CREATE TABLE public.bank_records (
  id UUID DEFAULT uuid_generate_v4() PRIMARY KEY,
  user_id UUID REFERENCES public.profiles(id) ON DELETE CASCADE NOT NULL,
  initial_bank INTEGER NOT NULL,
  current_bank INTEGER NOT NULL,
  total_bet INTEGER DEFAULT 0,
  total_return INTEGER DEFAULT 0,
  roi NUMERIC(10,2) DEFAULT 0,
  recovery_rate NUMERIC(10,2) DEFAULT 0,
  created_at TIMESTAMP WITH TIME ZONE DEFAULT NOW(),
  updated_at TIMESTAMP WITH TIME ZONE DEFAULT NOW()
);

-- OCR usage tracking
CREATE TABLE public.ocr_usage (
  id UUID DEFAULT uuid_generate_v4() PRIMARY KEY,
  user_id UUID REFERENCES public.profiles(id) ON DELETE CASCADE NOT NULL,
  image_url TEXT,
  extracted_text TEXT,
  corrected_data JSONB,
  success BOOLEAN DEFAULT true,
  error_message TEXT,
  created_at TIMESTAMP WITH TIME ZONE DEFAULT NOW()
);

-- Enable Row Level Security
ALTER TABLE public.profiles ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.predictions ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.bets ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.bank_records ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.ocr_usage ENABLE ROW LEVEL SECURITY;

-- RLS Policies for profiles
CREATE POLICY "Users can view own profile"
  ON public.profiles FOR SELECT
  USING (auth.uid() = id);

CREATE POLICY "Users can update own profile"
  ON public.profiles FOR UPDATE
  USING (auth.uid() = id);

-- RLS Policies for predictions
CREATE POLICY "Users can view own predictions"
  ON public.predictions FOR SELECT
  USING (auth.uid() = user_id);

CREATE POLICY "Users can insert own predictions"
  ON public.predictions FOR INSERT
  WITH CHECK (auth.uid() = user_id);

CREATE POLICY "Users can update own predictions"
  ON public.predictions FOR UPDATE
  USING (auth.uid() = user_id);

CREATE POLICY "Users can delete own predictions"
  ON public.predictions FOR DELETE
  USING (auth.uid() = user_id);

-- RLS Policies for bets
CREATE POLICY "Users can view own bets"
  ON public.bets FOR SELECT
  USING (auth.uid() = user_id);

CREATE POLICY "Users can insert own bets"
  ON public.bets FOR INSERT
  WITH CHECK (auth.uid() = user_id);

CREATE POLICY "Users can update own bets"
  ON public.bets FOR UPDATE
  USING (auth.uid() = user_id);

CREATE POLICY "Users can delete own bets"
  ON public.bets FOR DELETE
  USING (auth.uid() = user_id);

-- RLS Policies for bank_records
CREATE POLICY "Users can view own bank records"
  ON public.bank_records FOR SELECT
  USING (auth.uid() = user_id);

CREATE POLICY "Users can insert own bank records"
  ON public.bank_records FOR INSERT
  WITH CHECK (auth.uid() = user_id);

CREATE POLICY "Users can update own bank records"
  ON public.bank_records FOR UPDATE
  USING (auth.uid() = user_id);

-- RLS Policies for ocr_usage
CREATE POLICY "Users can view own ocr usage"
  ON public.ocr_usage FOR SELECT
  USING (auth.uid() = user_id);

CREATE POLICY "Users can insert own ocr usage"
  ON public.ocr_usage FOR INSERT
  WITH CHECK (auth.uid() = user_id);

-- Functions
CREATE OR REPLACE FUNCTION public.handle_new_user()
RETURNS TRIGGER AS $$
BEGIN
  INSERT INTO public.profiles (id, email, full_name)
  VALUES (NEW.id, NEW.email, NEW.raw_user_meta_data->>'full_name');
  
  INSERT INTO public.bank_records (user_id, initial_bank, current_bank)
  VALUES (NEW.id, 100000, 100000);
  
  RETURN NEW;
END;
$$ LANGUAGE plpgsql SECURITY DEFINER;

-- Trigger for new user
CREATE TRIGGER on_auth_user_created
  AFTER INSERT ON auth.users
  FOR EACH ROW EXECUTE FUNCTION public.handle_new_user();

-- Function to reset OCR counter monthly
CREATE OR REPLACE FUNCTION public.reset_ocr_counter()
RETURNS void AS $$
BEGIN
  UPDATE public.profiles
  SET ocr_used_this_month = 0,
      ocr_reset_date = NOW()
  WHERE ocr_reset_date < NOW() - INTERVAL '1 month';
END;
$$ LANGUAGE plpgsql;
