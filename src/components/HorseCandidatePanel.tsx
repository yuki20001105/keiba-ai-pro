'use client'

import { createContext, useContext, useReducer, type Dispatch, type ReactNode } from 'react'
import {
  assignHorseMark, buildCandidateFormation, buildCandidateTickets, buildFixedMixedPlan, CANDIDATE_BET_TYPES, FORMATION_OPTIONS,
  initialHorseCandidates, selectCandidateHorse,
  type CandidateBetType, type CandidateHorse, type CandidateSelection, type FormationKind, type HorseMark, TRIO_FORMATION_OPTIONS,
  type TrioFormationKind,
} from '@/lib/horse-candidates'

type Props = {
  raceId: string
  modelId?: string | null
  snapshotId?: string | null
  predictions: readonly CandidateHorse[]
  runnerCount?: number
}
type State = { selection: CandidateSelection; kind: FormationKind; trioKind: TrioFormationKind; betType: CandidateBetType; fixedMixed: boolean; notice: string | null }
type CandidateContextValue = { state: State; dispatch: Dispatch<Action> }
type Action =
  | { type: 'mark'; horseNumber: number; mark: HorseMark | null }
  | { type: 'formation'; kind: FormationKind }
  | { type: 'trio-formation'; kind: TrioFormationKind }
  | { type: 'bet-type'; betType: CandidateBetType }
  | { type: 'fixed-mixed' }

function initialState(predictions: readonly CandidateHorse[]): State {
  return { selection: initialHorseCandidates(predictions), kind: 'axis-first', trioKind: 'axis-flow', betType: '三連単', fixedMixed: true, notice: null }
}

const CandidateContext = createContext<CandidateContextValue | null>(null)

type ProviderProps = Props & { children: ReactNode }

export function HorseCandidateProvider({ raceId, modelId, snapshotId, predictions, runnerCount, children }: ProviderProps) {
  const contextKey = JSON.stringify([raceId, modelId ?? null, snapshotId ?? null, predictions, runnerCount ?? null])
  return <CandidateStateProvider key={contextKey} predictions={predictions}>{children}</CandidateStateProvider>
}

function CandidateStateProvider({ predictions, children }: Pick<Props, 'predictions'> & { children: ReactNode }) {
  const [state, dispatch] = useReducer(reducer, predictions, initialState)
  return <CandidateContext.Provider value={{ state, dispatch }}>{children}</CandidateContext.Provider>
}

export function useHorseCandidateSelection() {
  return useContext(CandidateContext)?.state.selection ?? null
}

export function useHorseCandidateState() {
  return useContext(CandidateContext)
}

function reducer(state: State, action: Action): State {
  switch (action.type) {
    case 'mark':
      if (action.mark && !state.selection.selected.includes(action.horseNumber) && state.selection.selected.length >= 5) {
        return { ...state, notice: '候補は最大5頭です。先に別の候補のチェックを外してください。' }
      }
      {
        let selection = assignHorseMark(state.selection, action.horseNumber, action.mark)
        if (action.mark && !selection.selected.includes(action.horseNumber)) {
          selection = selectCandidateHorse(selection, action.horseNumber, true)
        } else if (!action.mark && selection.selected.includes(action.horseNumber)) {
          selection = selectCandidateHorse(selection, action.horseNumber, false)
        }
        return { ...state, selection, notice: null }
      }
    case 'formation':
      return { ...state, kind: action.kind }
    case 'trio-formation':
      return { ...state, trioKind: action.kind }
    case 'bet-type':
      return { ...state, betType: action.betType, fixedMixed: false }
    case 'fixed-mixed':
      return { ...state, fixedMixed: true }
  }
}

export function HorseCandidatePanel(props: Props) {
  const shared = useContext(CandidateContext)
  if (shared) return <CandidateEditor {...props} shared={shared} />
  return <HorseCandidateProvider {...props}><StandaloneCandidateEditor {...props} /></HorseCandidateProvider>
}

function StandaloneCandidateEditor(props: Props) {
  const shared = useContext(CandidateContext)
  return shared ? <CandidateEditor {...props} shared={shared} /> : null
}

function CandidateEditor({ shared, predictions, runnerCount }: Props & { shared: CandidateContextValue }) {
  const { state, dispatch } = shared
  const mixed = buildFixedMixedPlan(predictions, runnerCount)
  const formations = FORMATION_OPTIONS.map(option => ({ ...option, plan: buildCandidateFormation(state.selection, option.kind) }))
  const active = formations.find(option => option.kind === state.kind)!
  const tickets = state.betType === '三連単'
    ? { tickets: active.plan.tickets, issue: active.plan.issue }
      : buildCandidateTickets(state.selection, state.betType, state.trioKind)
  const positions = [0, 1, 2].map(position => [...new Set(tickets.tickets.map(ticket => ticket[position]))].sort((a, b) => a - b))
  const ordered = state.betType === '馬単'
  const formatTicket = (ticket: number[]) => ticket.join(ordered || state.betType === '三連単' ? '→' : '-')
  return (
    <section aria-label="買い目試算" className="rounded border border-[#292929] bg-[#111] p-3 text-xs">
      <div className="mb-3 flex flex-wrap items-center justify-between gap-2">
        <h3 className="font-semibold text-white">買い目試算</h3>
        <span className="text-[#888]">{state.fixedMixed ? '単勝1番人気Aを軸に3点固定' : '印を付けた馬から組合せを作成'}</span>
      </div>
      <div role="group" aria-label="券種" className="mb-3 flex flex-wrap gap-1.5">
        <button type="button" aria-pressed={state.fixedMixed} onClick={() => dispatch({ type: 'fixed-mixed' })}
          className={`rounded px-3 py-2 ${state.fixedMixed ? 'bg-[#263848] text-[#7dd3fc]' : 'bg-[#1a1a1a] text-[#aaa] hover:bg-[#222]'}`}>
          ② ワイド1点＋三連単2点
        </button>
        {CANDIDATE_BET_TYPES.map(betType => <button key={betType} type="button"
          aria-pressed={!state.fixedMixed && state.betType === betType} onClick={() => dispatch({ type: 'bet-type', betType })}
          className={`rounded px-3 py-2 ${!state.fixedMixed && state.betType === betType ? 'bg-[#263848] text-[#7dd3fc]' : 'bg-[#1a1a1a] text-[#aaa] hover:bg-[#222]'}`}>
          {betType}
        </button>)}
      </div>
      {state.fixedMixed ? <>
        {mixed.issue ? <p className="py-2 text-[#fbbf24]">{mixed.issue}</p> : <>
          <div className="mb-2 flex items-center justify-between text-[#aaa]">
            <span>{mixed.points}点</span><span>A：{mixed.axis}番 · H2：{mixed.h2}番 · H3：{mixed.h3}番</span>
          </div>
          <div role="group" aria-label="②の買い目" className="space-y-2">
            {mixed.tickets.map(ticket => <div key={`${ticket.bet_type}:${ticket.combination}`} className="flex items-center justify-between gap-3 rounded bg-[#17232d] px-3 py-2">
              <span className="text-[#aaa]">{ticket.bet_type}</span>
              <span className="font-mono text-sm text-[#7dd3fc]">{ticket.combination}</span>
            </div>)}
          </div>
          <p className="mt-2 text-[#aaa]">研究時の基準配分：各{mixed.unitPrice}円 · 計{mixed.trialCost.toLocaleString('ja-JP')}円（購入額ではありません）</p>
        </>}
        <details className="mt-3 text-[#888]">
          <summary className="cursor-pointer">選定ルール・検証条件</summary>
          <p className="mt-2">Aは単勝オッズ最小の馬。Aを除くAI比率上位から最大5頭を取り、その中をAI比率×単勝オッズ順に並べた2・3位がH2・H3です。同値は馬番順です。手動の印は②に影響しません。</p>
          <p className="mt-2">このスコアは組合せ券の期待値ではありません。研究時のレース選別（Aの3着内確率65%以上など）は適用していません。モデル・対象レースが異なるため過去の回収率は再現保証ではありません。</p>
        </details>
      </> : <>
      {state.betType === '三連単' && <label className="mb-3 flex flex-wrap items-center justify-between gap-2">
        <span className="text-[#aaa]">買い方</span>
        <select aria-label="三連単の買い方" value={state.kind}
          onChange={event => dispatch({ type: 'formation', kind: event.target.value as FormationKind })}
          className="rounded border border-[#444] bg-[#1a1a1a] px-2 py-2">
          {formations.map(option => <option key={option.kind} value={option.kind} disabled={option.plan.issue !== null}>{option.label}</option>)}
        </select>
      </label>}
      {state.betType === '三連複' && <label className="mb-3 flex flex-wrap items-center justify-between gap-2">
        <span className="text-[#aaa]">買い方</span>
        <select aria-label="三連複の買い方" value={state.trioKind}
          onChange={event => dispatch({ type: 'trio-formation', kind: event.target.value as TrioFormationKind })}
          className="rounded border border-[#444] bg-[#1a1a1a] px-2 py-2">
          {TRIO_FORMATION_OPTIONS.map(option => {
            const plan = buildCandidateTickets(state.selection, '三連複', option.kind)
            return <option key={option.kind} value={option.kind} disabled={plan.issue !== null}>{option.label}</option>
          })}
        </select>
      </label>}
      {tickets.issue ? <p className="py-2 text-[#fbbf24]">{tickets.issue}</p> : <>
        <div className="mb-2 flex items-center justify-between text-[#aaa]">
          <span>{tickets.tickets.length}点</span>
          <span>組合せのみ</span>
        </div>
        {state.betType === '三連単' ? (
          <dl role="group" aria-label="三連単の着順候補" className="grid grid-cols-[3.5rem_1fr] gap-x-2 gap-y-1 font-mono text-sm">
            {positions.map((numbers, index) => <div key={index} className="contents">
              <dt className="text-[#888]">{index + 1}着：</dt><dd>{numbers.join('・')}</dd>
            </div>)}
          </dl>
        ) : (
          <div role="group" aria-label={`${state.betType}の買い目`}
            className="grid max-h-48 grid-cols-2 gap-x-4 gap-y-1 overflow-y-auto font-mono text-sm sm:grid-cols-4">
            {tickets.tickets.map((ticket, index) => <span key={`${formatTicket(ticket)}-${index}`}>{formatTicket(ticket)}</span>)}
          </div>
        )}
      </>}
      </>}
      <p className="mt-3 border-t border-[#222] pt-2 text-[11px] text-[#777]">券種別オッズ・払戻は含みません。購入判定とは別の参考案です。参考配分は購入・成績・予算に反映しません。</p>
    </section>
  )
}
