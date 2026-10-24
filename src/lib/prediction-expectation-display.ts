import type { RacePredictResult, Recommendation } from '@/lib/race-analysis-types'
import { BETTING_POLICY_VERSION, bettingRecommendationExpired } from '@/lib/betting-policy'
import { predictionInputVerified, sharedFeatureProfileCount, sharedInputFormulaMatchedCaptureUnverified } from '@/lib/prediction-input-contract'

export const REFERENCE_EXPECTATION_NOTE = '参考期待値＝校正済み確率×表示オッズ（1.00が損益分岐の目安）。取得時点未確認の試算で、購入判断や実績評価には使えません。'
export const REFERENCE_EXPECTATION_CAVEAT = '現在購入できるオッズ・発走前予測を保証しません。'

type ExpectationDisplay = {
  mode: 'official' | 'reference' | 'unavailable'
  label: '期待値' | '参考期待値'
  values: ReadonlyMap<number, number>
}

function numeric(value: unknown): value is number {
  return typeof value === 'number' && Number.isFinite(value)
}

function sameNumber(a: number, b: number): boolean {
  return Math.abs(a - b) <= 1e-9 * Math.max(1, Math.abs(a), Math.abs(b))
}

function utcTime(value: unknown): number {
  return typeof value === 'string' && /^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d{1,6})?(?:Z|\+00:00)$/.test(value)
    ? Date.parse(value) : Number.NaN
}

/** Validate display-only evidence independently. Never modifies a recommendation. */
export function predictionExpectationDisplay(
  result: RacePredictResult | null | undefined,
  recommendation: Recommendation | null | undefined = result?.recommendation,
  nowMs = Date.now(),
  expectedRaceId?: string,
): ExpectationDisplay {
  const unavailable: ExpectationDisplay = { mode: 'unavailable', label: '期待値', values: new Map() }
  const diagnostics = result?.input_diagnostics
  // The new context candidate has no independent live probability admission.
  // A cached/client-crafted reference payload cannot upgrade this profile.
  if (diagnostics?.feature_pipeline === 'local-db-context-v1' || diagnostics?.reference_only === true) return unavailable
  const official = recommendation?.probability_basis === 'calibrated'
    && recommendation.input_verified === true && predictionInputVerified(diagnostics)

  // An expired official EV cannot fall through to a reference calculation.
  if (official) {
    if (bettingRecommendationExpired({ ...recommendation, decision: 'buy' }, nowMs)) return unavailable
    const values = new Map<number, number>()
    for (const prediction of result?.predictions ?? []) {
      if (numeric(prediction.expected_value) && prediction.expected_value >= 0) values.set(prediction.horse_number, prediction.expected_value)
    }
    return { mode: 'official', label: '期待値', values }
  }

  const reference = result?.reference_expectation
  const expectedCount = sharedFeatureProfileCount(diagnostics?.feature_pipeline)
  if (!result?.success || !sharedInputFormulaMatchedCaptureUnverified(diagnostics)
    || expectedCount === null || diagnostics?.expected_count !== expectedCount || diagnostics.actual_count !== expectedCount
    || diagnostics.current_outcomes_consumed !== false
    || !numeric(diagnostics.missing_value_rate) || diagnostics.missing_value_rate < 0 || diagnostics.missing_value_rate > 0.2
    || recommendation?.policy_version !== BETTING_POLICY_VERSION || recommendation.probability_basis !== 'calibrated'
    || recommendation.input_verified !== false || recommendation.decision !== 'skip'
    || recommendation.purchase_count !== 0 || recommendation.unit_price !== 0 || recommendation.total_cost !== 0
    || recommendation.expected_return !== 0 || !Array.isArray(recommendation.tickets) || recommendation.tickets.length !== 0
    || reference?.schema !== 'calibrated-win-reference-v1' || reference.basis !== 'calibrated_probability_displayed_odds'
    || reference.purchase_eligible !== false || reference.capture_verified !== false || reference.odds_freshness_verified !== false
    || typeof result.model_id !== 'string' || !result.model_id.trim() || reference.model_id !== result.model_id
    || typeof result.race_info?.race_id !== 'string' || !result.race_info.race_id || reference.race_id !== result.race_info.race_id
    || (expectedRaceId !== undefined && reference.race_id !== expectedRaceId)
    || !Array.isArray(reference.items) || reference.items.length === 0
    || !Array.isArray(result.predictions) || result.predictions.length < 2 || result.predictions.length > 18
    || !Number.isInteger(result.race_info.num_horses) || result.race_info.num_horses < 2 || result.race_info.num_horses > 18
    || result.race_info.num_horses !== result.predictions.length) return unavailable

  // This TTL only bounds how long the trial is displayed, not the age of its odds.
  const computedAt = utcTime(reference.computed_at)
  const validUntil = utcTime(reference.valid_until)
  if (!numeric(nowMs) || !numeric(computedAt) || !numeric(validUntil)
    || computedAt > nowMs || validUntil <= computedAt || validUntil > computedAt + 5 * 60 * 1000
    || nowMs >= validUntil) return unavailable

  const predictions = new Map<number, RacePredictResult['predictions'][number]>()
  let probabilitySum = 0
  for (const prediction of result.predictions) {
    if (!prediction || !Number.isInteger(prediction.horse_number) || prediction.horse_number <= 0 || prediction.horse_number > 18
      || predictions.has(prediction.horse_number) || !numeric(prediction.p_norm)
      || prediction.p_norm < 0 || prediction.p_norm > 1) return unavailable
    predictions.set(prediction.horse_number, prediction)
    probabilitySum += prediction.p_norm
  }
  if (Math.abs(probabilitySum - 1) > 1e-5) return unavailable

  const values = new Map<number, number>()
  for (const item of reference.items) {
    if (!item || !Number.isInteger(item.horse_number) || values.has(item.horse_number)) return unavailable
    const prediction = predictions.get(item.horse_number)
    if (!prediction || !numeric(item.probability) || item.probability < 0 || item.probability > 1
      || !numeric(item.odds) || item.odds < 1 || !numeric(prediction.odds) || prediction.odds < 1
      || !numeric(item.expected_value) || !sameNumber(item.probability, prediction.p_norm)
      || !sameNumber(item.odds, prediction.odds) || !sameNumber(item.expected_value, item.probability * item.odds)) return unavailable
    values.set(item.horse_number, item.expected_value)
  }
  return { mode: 'reference', label: '参考期待値', values }
}
