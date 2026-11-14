import { z } from 'zod'

const count = z.number().int().nonnegative()
const roi = z.number().nonnegative().nullable()
const totalsFields = {
  opportunities: count, bet_races: count, hit_races: count,
  investment: count, payout: count, profit: z.number().int(), roi,
}
const totals = z.object(totalsFields).refine(value =>
  value.profit === value.payout - value.investment && value.hit_races <= value.bet_races
  && value.bet_races <= value.opportunities
  && (value.investment === 0 ? value.roi === null
    : value.roi !== null && Math.abs(value.roi - value.payout / value.investment * 100) < 1e-8))
const date = z.string().regex(/^\d{4}-\d{2}-\d{2}$/)
const scope = z.enum(['development', 'retrospective'])
const schema = z.object({
  schema: z.literal('betting-research-performance-v1'), study_id: z.string().min(1),
  feature_count: z.literal(101), reference_only: z.literal(true), purchase_eligible: z.literal(false),
  recommended_method: z.string().nullable(), historical_support: z.boolean(),
  scope, policy: z.string(), date_from: date.nullable(), date_to: date.nullable(), excluded_races: count,
  periods: z.object({ development: z.array(date).length(2), retrospective: z.array(date).length(2) }),
  policies: z.array(z.object({
    id: z.string(), label: z.string(), development: totals, retrospective: totals, baseline: totals,
  })).length(14),
  points: z.array(z.object({ ...totalsFields, date, baseline_roi: roi })),
  monthly: z.array(z.object({ ...totalsFields, month: z.string().regex(/^\d{4}-\d{2}$/), baseline_roi: roi })),
})

export type ResearchPerformance = z.infer<typeof schema>
export type ResearchScope = z.infer<typeof scope>

export function readResearchPerformance(raw: unknown, policy: string, requestedScope: ResearchScope,
  dateFrom: string, dateTo: string): ResearchPerformance | null {
  const result = schema.safeParse(raw)
  if (!result.success) return null
  const data = result.data
  if (data.policy !== policy || data.scope !== requestedScope
    || data.date_from !== (dateFrom || null) || data.date_to !== (dateTo || null)
    || new Set(data.policies.map(row => row.id)).size !== 14) return null
  const selected = data.policies.find(row => row.id === policy)
  if (!selected) return null
  const stats = selected[requestedScope]
  const last = data.points.at(-1)
  if (last ? last.investment !== stats.investment || last.payout !== stats.payout || last.roi !== stats.roi
    || last.baseline_roi !== selected.baseline.roi : stats.opportunities !== 0) return null
  for (const [index, point] of data.points.entries()) {
    if ((dateFrom && point.date < dateFrom) || (dateTo && point.date > dateTo)
      || (index > 0 && point.date <= data.points[index - 1].date)) return null
  }
  for (const field of ['investment', 'payout', 'opportunities', 'bet_races', 'hit_races'] as const) {
    if (data.monthly.reduce((sum, month) => sum + month[field], 0) !== stats[field]) return null
  }
  return data
}
