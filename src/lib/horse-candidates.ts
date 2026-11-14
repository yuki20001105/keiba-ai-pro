export type CandidateHorse = {
  horse_number: number
  horse_name: string
  predicted_rank: number
  jockey_name?: string | null
  odds: number | null
  p_norm?: number
  p_raw?: number
  win_probability?: number
}

export const HORSE_MARKS = [
  { mark: '◎', label: '本命', meaning: '最も勝つ確率が高い馬。トップ評価。' },
  { mark: '○', label: '対抗', meaning: '本命に対抗できる馬。2番手評価。' },
  { mark: '▲', label: '単穴', meaning: '◎や○に勝てる能力がある馬。3番手評価。' },
  { mark: '△', label: '連下', meaning: '2・3着に来る可能性がある馬。' },
  { mark: '☆', label: '星', meaning: '◎○▲△以外で、勝てる可能性がある穴馬。' },
  { mark: '注', label: '注意', meaning: '3着までなら可能性があると思われる馬。' },
] as const

export type HorseMark = typeof HORSE_MARKS[number]['mark']
export const INITIAL_HOLE_MIN_ODDS = 10

export type CandidateSelection = {
  available: number[]
  selected: number[]
  marks: Partial<Record<number, HorseMark>>
}

export const FORMATION_OPTIONS = [
  { kind: 'axis-first', label: '基本の1頭軸・1着固定', description: '◎ → 相手 → 相手' },
  { kind: 'narrow-second', label: '2着候補を絞る', description: '◎ → ○▲ → 相手' },
  { kind: 'longshot-cross', label: '穴を2・3着に入れる', description: '◎ → ○▲ → △☆ ＋ ◎ → △☆ → ○▲' },
  { kind: 'axis-multi', label: '1頭軸マルチ', description: '◎が1〜3着の全順序、相手へ流す' },
] as const

export type FormationKind = typeof FORMATION_OPTIONS[number]['kind']
export const TRIO_FORMATION_OPTIONS = [
  { kind: 'axis-flow', label: '◎1頭軸流し', description: '◎＋相手から2頭' },
  { kind: 'dual-axis', label: '◎○2頭軸', description: '◎○＋相手1頭' },
  { kind: 'longshot-axis', label: '穴を含める', description: '◎＋穴馬を含む相手2頭' },
  { kind: 'candidate-box', label: '候補ボックス', description: '印を付けた馬から3頭' },
] as const
export type TrioFormationKind = typeof TRIO_FORMATION_OPTIONS[number]['kind']
export const CANDIDATE_BET_TYPES = ['単勝', 'ワイド', '馬連', '馬単', '三連複', '三連単'] as const
export type CandidateBetType = typeof CANDIDATE_BET_TYPES[number]
export type CandidateTickets = { tickets: number[][]; issue: string | null }
export type CandidateFormation = {
  tickets: number[][]
  points: number
  trialCost: number
  issue: string | null
  purchase_eligible: false
}

export type FixedMixedPlan = {
  axis: number | null
  h2: number | null
  h3: number | null
  tickets: { bet_type: 'ワイド' | '三連単'; horse_numbers: number[]; combination: string }[]
  points: number
  unitPrice: number
  trialCost: number
  issue: string | null
  purchase_eligible: false
}

export function buildFixedMixedPlan(predictions: readonly CandidateHorse[], runnerCount?: number): FixedMixedPlan {
  const empty = (issue: string): FixedMixedPlan => ({
    axis: null, h2: null, h3: null, tickets: [], points: 0, unitPrice: 0,
    trialCost: 0, issue, purchase_eligible: false,
  })
  if (!Number.isInteger(runnerCount) || runnerCount! < 5 || runnerCount! > 18
    || !Array.isArray(predictions) || predictions.length !== runnerCount) {
    return empty('全出走馬の頭数を確認できません（5〜18頭が必要です）。')
  }
  const field: { number: number; probability: number; odds: number }[] = []
  for (const horse of predictions) {
    if (!horse || !Number.isInteger(horse.horse_number) || horse.horse_number < 1 || horse.horse_number > 18
      || typeof horse.p_norm !== 'number' || !Number.isFinite(horse.p_norm) || horse.p_norm < 0 || horse.p_norm > 1
      || typeof horse.odds !== 'number' || !Number.isFinite(horse.odds) || horse.odds < 1) {
      return empty('全出走馬のAI比率・単勝オッズを確認してください。')
    }
    field.push({ number: horse.horse_number, probability: horse.p_norm, odds: horse.odds })
  }
  if (new Set(field.map(horse => horse.number)).size !== runnerCount
    || Math.abs(field.reduce((sum, horse) => sum + horse.probability, 0) - 1) > 1e-8) {
    return empty('馬番の重複、またはAI比率の合計を確認してください。')
  }
  const axis = field.slice().sort((a, b) => a.odds - b.odds || a.number - b.number)[0].number
  const opponents = field.filter(horse => horse.number !== axis)
    .sort((a, b) => b.probability - a.probability || a.number - b.number).slice(0, 5)
    .sort((a, b) => b.probability * b.odds - a.probability * a.odds || a.number - b.number)
  const h2 = opponents[1].number
  const h3 = opponents[2].number
  const wide = [axis, h3].sort((a, b) => a - b)
  return {
    axis, h2, h3, points: 3, unitPrice: 500, trialCost: 1500, issue: null, purchase_eligible: false,
    tickets: [
      { bet_type: 'ワイド', horse_numbers: wide, combination: wide.join('-') },
      { bet_type: '三連単', horse_numbers: [h2, axis, h3], combination: [h2, axis, h3].join('→') },
      { bet_type: '三連単', horse_numbers: [h3, axis, h2], combination: [h3, axis, h2].join('→') },
    ],
  }
}

export function buildCandidateTickets(selection: CandidateSelection, betType: CandidateBetType, trioKind: TrioFormationKind = 'axis-flow'): CandidateTickets {
  if (betType === '三連単') {
    const formation = buildCandidateFormation(selection, 'axis-first')
    return { tickets: formation.tickets, issue: formation.issue }
  }
  const selected = selection.selected
  if (selected.length === 0) return { tickets: [], issue: '出走表で印を付けると試算できます。' }
  if (selected.length > 5 || new Set(selected).size !== selected.length
    || selected.some(n => !Number.isInteger(n) || n <= 0 || !selection.available.includes(n))) {
    return { tickets: [], issue: '印を付けた馬を確認してください（最大5頭）。' }
  }
  const tickets: number[][] = []
  if (betType === '単勝') return { tickets: selected.map(number => [number]), issue: null }
  if (betType === '三連複') {
    const formation = buildCandidateTrioFormation(selection, trioKind)
    return { tickets: formation.tickets, issue: formation.issue }
  }
  const cardinality = 2
  if (selected.length < cardinality) return { tickets: [], issue: `${betType}は候補${cardinality}頭以上が必要です。` }
  const ordered = betType === '馬単'
  const append = (tuple: number[]) => tickets.push(ordered ? tuple : tuple.slice().sort((a, b) => a - b))
  const choose = (start: number, current: number[]) => {
    if (current.length === cardinality) {
      if (ordered) {
        for (let i = 0; i < current.length; i++) for (let j = 0; j < current.length; j++) {
          if (i !== j) append([current[i], current[j]])
        }
      } else append(current)
      return
    }
    for (let i = start; i < selected.length; i++) choose(i + 1, [...current, selected[i]])
  }
  choose(0, [])
  return { tickets, issue: null }
}

export function buildCandidateTrioFormation(selection: CandidateSelection, kind: TrioFormationKind): CandidateFormation {
  const empty = (issue: string): CandidateFormation => ({
    tickets: [], points: 0, trialCost: 0, issue, purchase_eligible: false,
  })
  const selected = selection.selected
  if (selected.length < 3) return empty('三連複には候補3頭以上が必要です。')
  if (selected.length > 5 || new Set(selected).size !== selected.length
    || selected.some(n => !Number.isInteger(n) || n <= 0 || !selection.available.includes(n))) {
    return empty('候補の馬番を確認してください（重複なし・最大5頭）。')
  }
  const axes = selected.filter(n => selection.marks[n] === '◎')
  if (kind !== 'candidate-box' && axes.length !== 1) return empty('候補にチェックした1頭を◎にしてください。')
  const axis = axes[0]
  const others = selected.filter(n => n !== axis)
  const combinations: number[][] = []
  const addPairs = (pool: number[]) => {
    for (let i = 0; i < pool.length; i++) for (let j = i + 1; j < pool.length; j++) {
      combinations.push([axis, pool[i], pool[j]].sort((a, b) => a - b))
    }
  }
  switch (kind) {
    case 'axis-flow':
      addPairs(others)
      break
    case 'dual-axis': {
      const opponents = selected.filter(n => selection.marks[n] === '○')
      if (opponents.length !== 1) return empty('◎○2頭軸には○を1頭選んでください。')
      const secondAxis = opponents[0]
      for (const third of selected.filter(n => n !== axis && n !== secondAxis)) {
        combinations.push([axis, secondAxis, third].sort((a, b) => a - b))
      }
      break
    }
    case 'longshot-axis': {
      const holes = selected.filter(n => ['☆', '注'].includes(selection.marks[n] ?? ''))
      if (holes.length === 0) return empty('穴を含めるには☆または注の馬を選んでください。')
      for (let i = 0; i < others.length; i++) for (let j = i + 1; j < others.length; j++) {
        if (holes.includes(others[i]) || holes.includes(others[j])) {
          combinations.push([axis, others[i], others[j]].sort((a, b) => a - b))
        }
      }
      break
    }
    case 'candidate-box':
      for (let i = 0; i < selected.length; i++) for (let j = i + 1; j < selected.length; j++) {
        for (let k = j + 1; k < selected.length; k++) combinations.push([selected[i], selected[j], selected[k]].sort((a, b) => a - b))
      }
      break
    default:
      return empty('参考買い方を選択してください。')
  }
  const tickets = [...new Map(combinations.map(ticket => [ticket.join('-'), ticket])).values()]
  return { tickets, points: tickets.length, trialCost: tickets.length * 100, issue: null, purchase_eligible: false }
}

export function rankedCandidateHorses(predictions: readonly CandidateHorse[]): CandidateHorse[] {
  const seen = new Set<number>()
  return predictions
    .filter(h => h && Number.isInteger(h.horse_number) && h.horse_number > 0
      && Number.isInteger(h.predicted_rank) && h.predicted_rank > 0)
    .slice()
    .sort((a, b) => a.predicted_rank - b.predicted_rank || a.horse_number - b.horse_number)
    .filter(h => {
      if (seen.has(h.horse_number)) return false
      seen.add(h.horse_number)
      return true
    })
}

export function initialHorseCandidates(predictions: readonly CandidateHorse[]): CandidateSelection {
  const ranked = rankedCandidateHorses(predictions)
  const chosen = ranked.slice(0, 4)
  const rest = ranked.slice(4)
  const hole = rest.find(h => h.odds != null && Number.isFinite(h.odds) && h.odds >= INITIAL_HOLE_MIN_ODDS)
  const fifth = hole ?? rest[0]
  if (fifth) chosen.push(fifth)
  const marks: CandidateSelection['marks'] = {}
  chosen.forEach((h, index) => {
    marks[h.horse_number] = index === 4 ? (hole ? '☆' : '注') : HORSE_MARKS[index].mark
  })
  return { available: ranked.map(h => h.horse_number), selected: chosen.map(h => h.horse_number), marks }
}

export function selectCandidateHorse(selection: CandidateSelection, horseNumber: number, checked: boolean): CandidateSelection {
  if (!selection.available.includes(horseNumber)) return selection
  if (!checked) return { ...selection, selected: selection.selected.filter(n => n !== horseNumber) }
  if (selection.selected.includes(horseNumber) || selection.selected.length >= 5) return selection
  return {
    ...selection,
    selected: [...selection.selected, horseNumber],
    marks: { ...selection.marks, [horseNumber]: selection.marks[horseNumber] ?? '注' },
  }
}

export function assignHorseMark(selection: CandidateSelection, horseNumber: number, mark: HorseMark | null): CandidateSelection {
  if (!selection.available.includes(horseNumber) || (mark != null && !HORSE_MARKS.some(m => m.mark === mark))) return selection
  const marks = { ...selection.marks }
  const previous = marks[horseNumber]
  if (mark == null) delete marks[horseNumber]
  else {
    if (mark !== '注') {
      for (const [number, current] of Object.entries(marks)) {
        if (Number(number) !== horseNumber && current === mark) {
          if (previous) marks[Number(number)] = previous
          else delete marks[Number(number)]
        }
      }
    }
    marks[horseNumber] = mark
  }
  return { ...selection, marks }
}

export function buildCandidateFormation(selection: CandidateSelection, kind: FormationKind): CandidateFormation {
  const empty = (issue: string): CandidateFormation => ({
    tickets: [], points: 0, trialCost: 0, issue, purchase_eligible: false,
  })
  const selected = selection.selected
  if (selected.length < 3) return empty('三連単には候補3頭以上が必要です。')
  if (selected.length > 5 || new Set(selected).size !== selected.length
    || selected.some(n => !Number.isInteger(n) || n <= 0 || !selection.available.includes(n))) {
    return empty('候補の馬番を確認してください（重複なし・最大5頭）。')
  }
  const axes = selected.filter(n => selection.marks[n] === '◎')
  if (axes.length !== 1) return empty('候補にチェックした1頭を◎にしてください。')
  const axis = axes[0]
  const others = selected.filter(n => n !== axis)
  const roleGroup = (roles: readonly HorseMark[]) => roles.flatMap(mark => {
    const matches = selected.filter(n => selection.marks[n] === mark)
    return matches.length === 1 ? matches : []
  })
  const main = roleGroup(['○', '▲'])
  const side = roleGroup(['△', '☆'])
  const tickets: number[][] = []
  const seen = new Set<string>()
  const append = (first: number[], second: number[], third: number[], multi = false) => {
    for (const a of first) for (const b of second) for (const c of third) {
      if (a === b || b === c || a === c || (multi && ![a, b, c].includes(axis))) continue
      const key = `${a}>${b}>${c}`
      if (!seen.has(key)) {
        seen.add(key)
        tickets.push([a, b, c])
      }
    }
  }
  switch (kind) {
    case 'axis-first':
      append([axis], others, others)
      break
    case 'narrow-second':
      if (main.length !== 2) return empty('2着候補の○・▲をそれぞれ1頭選んでください。')
      append([axis], main, others)
      break
    case 'longshot-cross':
      if (main.length !== 2 || side.length !== 2) return empty('穴型には候補の○・▲・△・☆が必要です。')
      append([axis], main, side)
      append([axis], side, main)
      break
    case 'axis-multi':
      append(selected, selected, selected, true)
      break
    default:
      return empty('参考買い方を選択してください。')
  }
  return { tickets, points: tickets.length, trialCost: tickets.length * 100, issue: null, purchase_eligible: false }
}
