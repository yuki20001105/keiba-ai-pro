/**
 * e2e/helpers/mock-api.ts
 * 共通APIモックヘルパー
 */
import { Page } from '@playwright/test'
import { buildSupabaseSessionCookie, getAppOrigin, type SupabaseSessionOptions } from './supabase-session'

/** Supabase auth — protected-page test session (Admin/Premium by default for legacy suites). */
export async function mockAuth(
  page: Page,
  opts: { role?: 'admin' | 'user'; tier?: 'free' | 'premium' } = {},
) {
  const appBaseUrl = process.env.PW_BASE_URL || `http://127.0.0.1:${process.env.PW_PORT || '3101'}`
  const role = opts.role ?? 'admin'
  const tier = opts.tier ?? 'premium'
  await setSupabaseTestSession(page, {
    appBaseUrl,
    role,
    tier,
  })
  await mockSupabaseIdentity(page, {
    authenticated: true,
    role,
    tier,
  })
}

export async function setSupabaseTestSession(page: Page, opts: SupabaseSessionOptions) {
  const appOrigin = getAppOrigin(opts.appBaseUrl)
  const cookie = buildSupabaseSessionCookie(opts)
  await page.context().addCookies([
    {
      name: cookie.name,
      value: cookie.value,
      url: appOrigin,
      httpOnly: false,
      sameSite: 'Lax',
    },
  ])
  if ((opts.role ?? 'user') === 'admin') {
    await page.route('/api/admin/unlock**', route => {
      if (route.request().method() !== 'GET') return route.fallback()
      return route.fulfill({
        status: 200,
        contentType: 'application/json',
        body: JSON.stringify({
          version: 1,
          unlocked: true,
          expires_at: new Date(Date.now() + 10 * 60 * 1000).toISOString(),
        }),
      })
    })
  }
  return cookie.name
}

export async function clearSupabaseTestSession(page: Page) {
  await page.context().clearCookies()
}

export async function mockSupabaseIdentity(
  page: Page,
  opts: { authenticated: boolean; role?: 'admin' | 'user'; tier?: 'free' | 'premium' }
) {
  const role = opts.role ?? 'user'
  const tier = opts.tier ?? 'free'

  await page.route('**/auth/v1/user**', route => {
    if (!opts.authenticated) {
      return route.fulfill({
        status: 401,
        contentType: 'application/json',
        body: JSON.stringify({ message: 'JWT expired' }),
      })
    }
    return route.fulfill({
      status: 200,
      contentType: 'application/json',
      body: JSON.stringify({
        id: 'e2e-user-id',
        aud: 'authenticated',
        role: 'authenticated',
        email: 'e2e@example.com',
      }),
    })
  })

  await page.route('**/rest/v1/profiles**', route => {
    const url = route.request().url()
    if (url.includes('select=role%2Csubscription_tier') || url.includes('select=role,subscription_tier')) {
      return route.fulfill({
        status: 200,
        contentType: 'application/json',
        body: JSON.stringify({ role, subscription_tier: tier }),
      })
    }
    return route.fulfill({
      status: 200,
      contentType: 'application/json',
      body: JSON.stringify([
        {
          id: 'e2e-user-id',
          email: 'e2e@example.com',
          role,
          full_name: 'E2E User',
          subscription_tier: tier,
          created_at: '2026-01-01T00:00:00Z',
        },
      ]),
    })
  })
}

/** FastAPI /health */
export async function mockHealth(page: Page, online = true) {
  await page.route('/api/health**', route =>
    route.fulfill({ status: online ? 200 : 503, json: { status: online ? 'ok' : 'offline' } })
  )
}

/** FastAPI /api/data_stats */
export async function mockDataStats(page: Page) {
  await page.route('/api/data-stats**', route =>
    route.fulfill({
      json: { total_races: 12345, total_horses: 98765, total_models: 3, latest_date: '2026-04-01', db_exists: true },
    })
  )
}

/** レース一覧 */
export async function mockRacesByDate(page: Page) {
  await page.route('/api/races/by-date**', route =>
    route.fulfill({
      json: {
        races: [
          { race_id: '202604070101', race_name: 'テストレース1', venue: '東京', race_number: 1, race_no: 1, start_time: '10:00', num_horses: 8, track_type: '芝', distance: 1600 },
          { race_id: '202604070102', race_name: 'テストレース2', venue: '東京', race_number: 2, race_no: 2, start_time: '10:30', num_horses: 10, track_type: '芝', distance: 2000 },
        ],
      },
    })
  )
}

/** モデル一覧 */
export async function mockModels(page: Page) {
  await page.route('/api/models**', route =>
    route.fulfill({
      json: {
        models: [
          {
            model_id: 'abc123-def456', model_type: 'lightgbm', target: 'speed_deviation',
            auc: 0.7234, cv_auc_mean: 0.710, created_at: '2026-04-01T10:00:00Z',
            is_active: true, n_rows: 5000, training_date_from: '2024-01-01', training_date_to: '2025-12-31',
            evaluation: {
              primary: {
                rank_correlation: 0.7234, rmse: 0.681,
                top_pick_win_rate: 0.352, favorite_win_rate: 0.410,
                top_pick_win_rate_delta: -0.058, win_roi: 84.6,
              },
              details: {
                mae: 0.512, r2: 0.573, evaluation_date_from: '2025-07-19',
                evaluation_date_to: '2026-04-01', evaluation_race_count: 412,
              },
              time_slices: [],
            },
          },
        ],
      },
    })
  )
}

/** 予測結果（/api/analyze-race用） */
export async function mockAnalyzeRace(page: Page) {
  await page.route('/api/analyze-race**', route =>
    route.fulfill({
      json: {
        success: true,
        model_id: 'abc123-def456',
        explanation_method: 'tree_shap',
        race_id: '202604070101',
        race_info: { race_id: '202604070101', race_name: 'テストレース1', venue: '東京', date: '2026-04-07', race_no: 1, track_type: '芝', distance: 1600, num_horses: 3 },
        predictions: [
          {
            horse_number: 1, horse_name: 'テスト馬A', jockey_name: '騎手A', predicted_rank: 1,
            win_probability: 0.40, p_raw: 0.35, p_norm: 0.40, odds: 3.2, expected_value: 1.28, popularity: 1,
            explanation: {
              method: 'tree_shap', base_value: 0.1, features: [
                { feature: 'jockey_win_rate', label: '騎手の勝率', description: '騎手の勝率', value: 0.15, contribution: 0.21, impact_pct: 34, direction: 'positive' },
                { feature: 'prev_race_finish', label: '前走着順', description: '前走着順', value: 1, contribution: 0.15, impact_pct: 24, direction: 'positive' },
                { feature: 'horse_weight_change', label: '馬体重変化', description: '前走からの馬体重変化', value: -8, contribution: -0.09, impact_pct: 15, direction: 'negative' },
              ],
            },
          },
          {
            horse_number: 2, horse_name: 'テスト馬B', jockey_name: '騎手B', predicted_rank: 2,
            win_probability: 0.30, p_raw: 0.25, p_norm: 0.30, odds: 5.0, expected_value: 1.50, popularity: 2,
            explanation: {
              method: 'tree_shap', base_value: 0.1, features: [
                { feature: 'days_since_last_race', label: '前走からの日数', description: '前走からの経過日数', value: 28, contribution: 0.12, impact_pct: 30, direction: 'positive' },
              ],
            },
          },
          {
            horse_number: 3, horse_name: 'テスト馬C', jockey_name: '騎手C', predicted_rank: 3,
            win_probability: 0.18, p_raw: 0.15, p_norm: 0.18, odds: 8.0, expected_value: 1.44, popularity: 3,
            explanation: {
              method: 'tree_shap', base_value: 0.1, features: [
                { feature: 'odds', label: '単勝オッズ', description: '単勝オッズ', value: 8, contribution: -0.1, impact_pct: 27, direction: 'negative' },
              ],
            },
          },
        ],
        best_bet_type: '単勝',
        bet_types: { '単勝': [{ combination: '1', odds: 3.2 }, { combination: '2', odds: 5.0 }] },
        recommendation: { action: '購入推奨', purchase_count: 1, unit_price: 1000, total_cost: 1000, expected_return: 1280, reason: '期待値 > 1.0' },
      },
    })
  )
}

/** @deprecated Use mockAnalyzeRace instead */
export const mockPredict = mockAnalyzeRace

/** 購入履歴 */
export async function mockPurchaseHistory(page: Page) {
  await page.route('/api/purchase-history**', route => {
    if (route.request().method() === 'GET') {
      return route.fulfill({
        json: {
          success: true,
          history: [
            { id: 'bet-001', race_id: '202604070101', race_name: 'テストレース1', bet_type: '単勝', amount: 1000, total_cost: 1000, actual_return: null, is_hit: null, purchased_at: '2026-04-07T10:00:00Z' },
          ],
          count: 1,
        },
      })
    }
    return route.fulfill({ json: { success: true } })
  })
  await page.route('/api/statistics**', route =>
    route.fulfill({ json: { statistics: { by_bet_type: [{ bet_type: '単勝', count: 1, hit_count: 0, recovery_rate: 0, hit_rate: 0 }] } } })
  )
}
