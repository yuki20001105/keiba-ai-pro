/**
 * e2e/helpers/mock-api.ts
 * 共通APIモックヘルパー
 */
import { Page } from '@playwright/test'

/** Supabase auth — ログイン不要の匿名ユーザーを返す */
export async function mockAuth(page: Page) {
  await page.route('**/auth/v1/user**', route =>
    route.fulfill({ status: 401, json: { error: 'not authenticated' } })
  )
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
          { model_id: 'abc123-def456', model_type: 'lightgbm', target: 'win', auc: 0.7234, cv_auc_mean: 0.710, created_at: '2026-04-01T10:00:00Z', is_active: true, n_rows: 5000, training_date_from: '2024-01-01', training_date_to: '2025-12-31' },
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
        race_id: '202604070101',
        race_info: { race_name: 'テストレース1', venue: '東京', date: '2026-04-07', race_no: 1, track_type: '芝', distance: 1600, num_horses: 3 },
        predictions: [
          { horse_number: 1, horse_name: 'テスト馬A', jockey_name: '騎手A', predicted_rank: 1, p_raw: 0.35, p_norm: 0.40, odds: 3.2, expected_value: 1.28, popularity: 1 },
          { horse_number: 2, horse_name: 'テスト馬B', jockey_name: '騎手B', predicted_rank: 2, p_raw: 0.25, p_norm: 0.30, odds: 5.0, expected_value: 1.50, popularity: 2 },
          { horse_number: 3, horse_name: 'テスト馬C', jockey_name: '騎手C', predicted_rank: 3, p_raw: 0.15, p_norm: 0.18, odds: 8.0, expected_value: 1.44, popularity: 3 },
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
