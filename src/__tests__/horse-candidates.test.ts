import { describe, expect, it } from 'vitest'
import {
  assignHorseMark,
  buildCandidateFormation,
  buildCandidateTickets,
  buildCandidateTrioFormation,
  buildFixedMixedPlan,
  initialHorseCandidates,
  selectCandidateHorse,
  type CandidateHorse,
  type FormationKind,
} from '@/lib/horse-candidates'

const mixedHorses: CandidateHorse[] = [
  [1, .36, 2], [2, .22, 4], [3, .16, 8],
  [4, .12, 15], [5, .09, 30], [6, .05, 60],
].map(([horse_number, p_norm, odds], index) => ({
  horse_number, p_norm, odds, predicted_rank: index + 1, horse_name: `馬${horse_number}`,
}))

describe('fixed mixed candidate two', () => {
  it('selects A-H3 wide and exactly the two A-second trifectas without mutating predictions', () => {
    const before = JSON.stringify(mixedHorses)
    const plan = buildFixedMixedPlan(mixedHorses, 6)
    expect(plan).toMatchObject({ axis: 1, h2: 5, h3: 4, points: 3,
      unitPrice: 500, trialCost: 1500, purchase_eligible: false, issue: null })
    expect(plan.tickets).toEqual([
      { bet_type: 'ワイド', horse_numbers: [1, 4], combination: '1-4' },
      { bet_type: '三連単', horse_numbers: [5, 1, 4], combination: '5→1→4' },
      { bet_type: '三連単', horse_numbers: [4, 1, 5], combination: '4→1→5' },
    ])
    expect(JSON.stringify(mixedHorses)).toBe(before)
  })

  it('excludes A before taking five AI opponents and never promotes lower-ranked high scores', () => {
    const field = [
      [1, .28, 8], [2, .22, 12], [3, .16, 18], [4, .12, 30],
      [5, .09, 50], [6, .06, 200], [7, .04, 300], [8, .03, 2],
    ].map(([horse_number, p_norm, odds], index) => ({
      horse_number, p_norm, odds, predicted_rank: index + 1, horse_name: `馬${horse_number}`,
    }))
    const plan = buildFixedMixedPlan(field.reverse(), 8)
    expect(plan).toMatchObject({ axis: 8, h2: 4, h3: 3, points: 3 })
    expect(plan.tickets.map(ticket => ticket.combination)).toEqual(['3-8', '4→8→3', '3→8→4'])
    expect(buildFixedMixedPlan(mixedHorses.slice().reverse(), 6).h2).toBe(5)
  })

  it('breaks odds, AI and H-score ties by horse number independently of input order', () => {
    const tied = mixedHorses.slice().reverse().map(horse => ({ ...horse, odds: 2, p_norm: 1 / 6 }))
    expect(buildFixedMixedPlan(tied, 6)).toMatchObject({ axis: 1, h2: 3, h3: 4 })
    const five = mixedHorses.slice(0, 5).map((horse, i) => ({ ...horse, p_norm: horse.p_norm! + (i === 0 ? .05 : 0) }))
    expect(buildFixedMixedPlan(five, 5)).toMatchObject({ axis: 1, h2: 4, h3: 3, points: 3 })
  })

  it.each([undefined, 5, 7, 6.5])('does not form even a reference book for an unverified runner count: %s', count => {
    expect(buildFixedMixedPlan(mixedHorses, count)).toMatchObject({
      tickets: [], points: 0, unitPrice: 0, trialCost: 0, issue: expect.any(String), purchase_eligible: false,
    })
  })

  it.each([
    { odds: null }, { odds: 0 }, { odds: Number.NaN }, { odds: Number.POSITIVE_INFINITY },
    { p_norm: undefined }, { p_norm: .9 }, { p_norm: Number.NaN }, { horse_number: 2 },
  ])('does not silently drop a malformed runner or invent a partial plan: %o', changes => {
    const field = mixedHorses.map((horse, i) => i === 0 ? { ...horse, ...changes } : horse)
    expect(buildFixedMixedPlan(field, 6)).toMatchObject({
      tickets: [], points: 0, trialCost: 0, issue: expect.any(String), purchase_eligible: false,
    })
  })
})

const horses: CandidateHorse[] = [
  { horse_number: 17, horse_name: '馬17', predicted_rank: 5, odds: 8, p_norm: 0.9 },
  { horse_number: 6, horse_name: '馬6', predicted_rank: 6, odds: 32.8 },
  { horse_number: 3, horse_name: '馬3', predicted_rank: 3, odds: 10 },
  { horse_number: 1, horse_name: '馬1', predicted_rank: 1, odds: 2.3, p_norm: 0.01 },
  { horse_number: 8, horse_name: '馬8', predicted_rank: 7, odds: 100 },
  { horse_number: 11, horse_name: '馬11', predicted_rank: 4, odds: 11.7 },
  { horse_number: 7, horse_name: '馬7', predicted_rank: 2, odds: 2.5 },
]

describe('five-horse provisional candidates', () => {
  it('uses declared ranks for four main candidates and the highest-ranked remaining longshot', () => {
    const before = JSON.stringify(horses)
    const selection = initialHorseCandidates(horses)
    expect(selection.selected).toEqual([1, 7, 3, 11, 6])
    expect(selection.marks).toMatchObject({ 1: '◎', 7: '○', 3: '▲', 11: '△', 6: '☆' })
    expect(JSON.stringify(horses)).toBe(before)
  })

  it('does not label unknown, zero or nonfinite odds as a longshot', () => {
    const withoutHole = horses.map(h => ({
      ...h, odds: h.horse_number === 6 ? Number.NaN : h.horse_number === 8 ? Number.POSITIVE_INFINITY : 0,
    }))
    const selection = initialHorseCandidates(withoutHole)
    expect(selection.selected).toEqual([1, 7, 3, 11, 17])
    expect(selection.marks[17]).toBe('注')
    expect(buildCandidateFormation(selection, 'longshot-cross').tickets).toEqual([])
  })

  it('caps selection at five and preserves an unchecked horse evaluation', () => {
    const initial = initialHorseCandidates(horses)
    expect(selectCandidateHorse(initial, 17, true).selected).toEqual(initial.selected)
    const removed = selectCandidateHorse(initial, 6, false)
    expect(removed.marks[6]).toBe('☆')
    const replaced = selectCandidateHorse(removed, 17, true)
    expect(replaced.selected).toEqual([1, 7, 3, 11, 17])
    expect(replaced.marks[17]).toBe('注')
    expect(initial.selected).toEqual([1, 7, 3, 11, 6])
    expect(selectCandidateHorse(initial, 99, true).selected).toEqual(initial.selected)
  })

  it('swaps a new axis evaluation without changing selected horses', () => {
    const initial = initialHorseCandidates(horses)
    const changed = assignHorseMark(initial, 6, '◎')
    expect(changed.selected).toEqual(initial.selected)
    expect(changed.marks).toMatchObject({ 6: '◎', 1: '☆' })
    expect(initial.marks[1]).toBe('◎')
    const plan = buildCandidateFormation(changed, 'axis-first')
    expect(plan.tickets.every(t => t[0] === 6)).toBe(true)
    expect(plan.points).toBe(12)
  })

  it('ignores invalid identities and duplicate horse rows without inventing candidates', () => {
    const selection = initialHorseCandidates([
      ...horses, { ...horses[3] }, { ...horses[3], horse_number: 0 },
      { ...horses[3], horse_number: 4.5 }, { ...horses[3], horse_number: 4, predicted_rank: Number.NaN },
    ])
    expect(selection.selected).toEqual([1, 7, 3, 11, 6])
    expect(new Set(selection.available).size).toBe(selection.available.length)
    expect(selection.available).not.toContain(0)
    expect(selection.available).not.toContain(4.5)
    expect(selection.available).not.toContain(4)
  })
})

describe('display-only ordered trifecta formations', () => {
  it.each([
    ['axis-first', 12, 1200],
    ['narrow-second', 6, 600],
    ['longshot-cross', 8, 800],
    ['axis-multi', 36, 3600],
  ] as [FormationKind, number, number][])('generates %s with distinct horses and exact trial cost', (kind, points, cost) => {
    const selection = initialHorseCandidates(horses)
    const plan = buildCandidateFormation(selection, kind)
    expect(plan.points).toBe(points)
    expect(plan.trialCost).toBe(cost)
    expect(plan.purchase_eligible).toBe(false)
    expect(plan.issue).toBeNull()
    expect(plan.tickets.every(t => t.length === 3 && new Set(t).size === 3 && t.includes(1))).toBe(true)
    expect(new Set(plan.tickets.map(t => t.join('>'))).size).toBe(points)
  })

  it('retains ordered reversals and excludes duplicate positions in the six-ticket formation', () => {
    const plan = buildCandidateFormation(initialHorseCandidates(horses), 'narrow-second')
    expect(plan.tickets).toEqual([
      [1, 7, 3], [1, 7, 11], [1, 7, 6],
      [1, 3, 7], [1, 3, 11], [1, 3, 6],
    ])
  })

  it('does not generate tickets for a missing selected axis, duplicates or unknown horses', () => {
    const selection = initialHorseCandidates(horses)
    for (const invalid of [
      assignHorseMark(selection, 1, null),
      { ...selection, selected: [1, 1, 7] },
      { ...selection, selected: [1, 7, 99] },
      { ...selection, selected: [...selection.selected, 17] },
      selectCandidateHorse(selection, 1, false),
      initialHorseCandidates([]),
    ]) {
      const plan = buildCandidateFormation(invalid, 'axis-first')
      expect(plan.tickets).toEqual([])
      expect(plan.trialCost).toBe(0)
      expect(plan.issue).not.toBeNull()
    }
  })

  it('counts a smaller valid selection rather than pretending it still has five horses', () => {
    const selection = initialHorseCandidates(horses.slice().filter(h => [1, 7, 3].includes(h.horse_number)))
    const plan = buildCandidateFormation(selection, 'axis-first')
    expect(plan.tickets).toEqual([[1, 7, 3], [1, 3, 7]])
    expect(plan.points).toBe(2)
    expect(plan.trialCost).toBe(200)
  })
})

describe('candidate ticket combinations by bet type', () => {
  const selection = initialHorseCandidates(horses)

  it('builds selected-horse win tickets, unordered pairs and triples', () => {
    expect(buildCandidateTickets(selection, '単勝').tickets).toEqual([[1], [7], [3], [11], [6]])
    expect(buildCandidateTickets(selection, 'ワイド').tickets).toHaveLength(10)
    expect(buildCandidateTickets(selection, '馬連').tickets).toEqual(buildCandidateTickets(selection, 'ワイド').tickets)
    expect(buildCandidateTickets(selection, '三連複').tickets).toHaveLength(6)
    expect(buildCandidateTickets(selection, '三連複').tickets[0]).toEqual([1, 3, 7])
  })

  it('keeps order for exacta and reports when too few horses are marked', () => {
    const exacta = buildCandidateTickets(selection, '馬単')
    expect(exacta.tickets).toHaveLength(20)
    expect(exacta.tickets).toContainEqual([1, 7])
    expect(exacta.tickets).toContainEqual([7, 1])
    const two = { ...selection, selected: [1, 7] }
    expect(buildCandidateTickets(two, '三連複')).toMatchObject({ tickets: [], issue: expect.any(String) })
  })

  it.each([
    ['axis-flow', 6], ['dual-axis', 3], ['longshot-axis', 3], ['candidate-box', 10],
  ] as const)('builds the %s three-horse combination plan', (kind, points) => {
    const plan = buildCandidateTrioFormation(selection, kind)
    expect(plan.points).toBe(points)
    expect(plan.trialCost).toBe(points * 100)
    expect(plan.purchase_eligible).toBe(false)
    expect(plan.issue).toBeNull()
    expect(plan.tickets.every(ticket => ticket.length === 3 && new Set(ticket).size === 3)).toBe(true)
    expect(new Set(plan.tickets.map(ticket => ticket.join('-'))).size).toBe(points)
    if (kind === 'axis-flow') expect(plan.tickets.every(ticket => ticket.includes(1))).toBe(true)
    if (kind === 'dual-axis') expect(plan.tickets.every(ticket => ticket.includes(1) && ticket.includes(7))).toBe(true)
    if (kind === 'longshot-axis') expect(plan.tickets.every(ticket => ticket.includes(1) && ticket.includes(6))).toBe(true)
  })
})
