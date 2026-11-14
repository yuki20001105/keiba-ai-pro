import { cleanup, fireEvent, render, screen, within } from '@testing-library/react'
import { afterEach, describe, expect, it } from 'vitest'
import { HorseCandidatePanel, HorseCandidateProvider } from '@/components/HorseCandidatePanel'
import { HorsePredictionTable } from '@/components/HorsePredictionTable'
import type { CandidateHorse } from '@/lib/horse-candidates'
import type { RacePredictResult } from '@/lib/race-analysis-types'
import { verifiedPredictionInput } from './helpers/verified-prediction-input'

const predictions: CandidateHorse[] = [[1, 2.3], [7, 2.5], [3, 10], [11, 11.7], [17, 8], [6, 32.8], [8, 100]]
  .map(([horse_number, odds], index) => ({
    horse_number, horse_name: `候補馬${horse_number}`, predicted_rank: index + 1, odds,
    p_norm: [.36, .22, .16, .12, .09, .04, .01][index],
  }))
const props = { raceId: 'test-race-one', modelId: 'test-model-one', snapshotId: 'test-snapshot-one', predictions, runnerCount: predictions.length }
const runnerRows = predictions.map((horse, index) => ({
  ...horse, jockey_name: '', trainer_name: '', sex: '牡', age: 3, horse_weight: 480,
  popularity: index + 1, win_probability: horse.p_norm ?? 0.1,
  p_raw: horse.p_raw ?? 0, p_norm: horse.p_norm ?? 0.1, expected_value: null,
})) as RacePredictResult['predictions']

afterEach(cleanup)

function mountTable() {
  render(<HorseCandidateProvider {...props}>
    <HorsePredictionTable predictions={runnerRows} />
    <HorseCandidatePanel {...props} />
  </HorseCandidateProvider>)
}

function tableRows() {
  return within(screen.getByRole('table', { name: '予測一覧' })).getAllByRole('row')
}

function openTrifectaTrial() {
  fireEvent.click(screen.getByRole('button', { name: '三連単' }))
}

describe('prediction list candidate controls', () => {
  it('shows captured actual odds and a neutral index rather than EV for an unvalidated model', () => {
    const result: RacePredictResult = {
      success: true, model_id: 'reference-model', predictions: runnerRows,
      race_info: { race_id: props.raceId, race_name: 'fixture', venue: '東京', date: '20261107',
        distance: 1600, track_type: '芝', num_horses: runnerRows.length },
      recommendation: null, best_bet_type: null, pro_evaluation: null,
      input_diagnostics: { ...verifiedPredictionInput, current_outcomes_consumed: false, reference_only: true },
      odds_refresh: { status: 'middle', observed_at: '2026-11-06T21:11:00Z' },
    }
    render(<HorsePredictionTable predictions={runnerRows} result={result} />)
    expect(screen.getByRole('columnheader', { name: /参考指数/ })).toBeInTheDocument()
    expect(within(tableRows()[1]).getAllByRole('cell')[6]).toHaveTextContent('0.83')
    expect(within(tableRows()[1]).getAllByRole('cell')[7]).toHaveTextContent('2.3')
    expect(screen.getByText(/06:11:00/)).toHaveTextContent('単勝オッズ取得')
    expect(screen.getByText(/期待値・購入根拠ではありません/)).toBeInTheDocument()
    expect(screen.queryByRole('button', { name: /購入.*記録/ })).not.toBeInTheDocument()
  })

  it('defaults to fixed candidate two with three explicit tickets, separate from manual marks and purchases', () => {
    mountTable()
    const trial = within(screen.getByRole('region', { name: '買い目試算' }))
    expect(trial.getByRole('button', { name: '② ワイド1点＋三連単2点' })).toHaveAttribute('aria-pressed', 'true')
    const book = within(trial.getByRole('group', { name: '②の買い目' }))
    expect(book.getByText('1-6')).toBeInTheDocument()
    expect(book.getByText('11→1→6')).toBeInTheDocument()
    expect(book.getByText('6→1→11')).toBeInTheDocument()
    expect(trial.getByText('3点')).toBeInTheDocument()
    expect(trial.getByText(/各500円.*1,500円/)).toBeInTheDocument()
    expect(screen.queryByRole('group', { name: '三連単の着順候補' })).not.toBeInTheDocument()
    fireEvent.change(screen.getByRole('combobox', { name: '6番の印を編集' }), { target: { value: '◎' } })
    expect(book.getByText('11→1→6')).toBeInTheDocument()
    expect(screen.queryByRole('button', { name: /購入|記録/ })).not.toBeInTheDocument()
  })

  it('clears unavailable selections and resets manual mode when predictions change', () => {
    const { rerender } = render(<HorseCandidatePanel {...props} />)
    fireEvent.click(screen.getByRole('button', { name: '三連単' }))
    expect(screen.getByRole('group', { name: '三連単の着順候補' })).toBeInTheDocument()
    rerender(<HorseCandidatePanel {...props} modelId="other-model" />)
    expect(screen.getByRole('group', { name: '②の買い目' })).toHaveTextContent('11→1→6')
    rerender(<HorseCandidatePanel {...props} modelId="other-model" predictions={predictions.map((horse, i) => i === 0 ? { ...horse, odds: null } : horse)} />)
    expect(screen.queryByRole('group', { name: '②の買い目' })).not.toBeInTheDocument()
    expect(screen.queryByText('11→1→6')).not.toBeInTheDocument()
    expect(screen.getByText(/全出走馬の.*オッズ/)).toBeInTheDocument()
  })

  it('edits marks directly in the table, removes duplicate columns and sorts both ways', () => {
    mountTable()
    const table = within(screen.getByRole('table', { name: '予測一覧' }))
    expect(screen.queryByText('出走馬', { selector: 'h2' })).not.toBeInTheDocument()
    expect(table.queryByRole('columnheader', { name: '複勝参考' })).not.toBeInTheDocument()
    expect(table.queryByRole('columnheader', { name: '合成参考' })).not.toBeInTheDocument()
    const firstRunner = within(tableRows()[1])
    expect(firstRunner.getByRole('combobox', { name: '1番の印を編集' })).toHaveValue('◎')
    expect(within(tableRows()[6]).getByRole('combobox', { name: '6番の印を編集' })).toHaveValue('☆')

    fireEvent.change(firstRunner.getByRole('combobox'), { target: { value: '△' } })
    expect(firstRunner.getByRole('combobox')).toHaveValue('△')
    openTrifectaTrial()
    fireEvent.change(screen.getByRole('combobox', { name: '三連単の買い方' }), { target: { value: 'narrow-second' } })
    const positions = screen.getByRole('group', { name: '三連単の着順候補' })
    expect(within(positions).getByText('1着：')).toBeInTheDocument()
    expect(within(positions).getByText('11')).toBeInTheDocument()
    expect(within(positions).getByText('3・7')).toBeInTheDocument()

    fireEvent.click(table.getByRole('button', { name: 'オッズで昇順に並べ替え' }))
    fireEvent.click(table.getByRole('button', { name: 'オッズで降順に並べ替え' }))
    expect(within(tableRows()[1]).getAllByRole('cell')[2]).toHaveTextContent('8')
  })

  it('keeps one compact multi-bet trial and no duplicate horse editor', () => {
    render(<HorseCandidatePanel {...props} />)
    expect(screen.queryByText('候補')).not.toBeInTheDocument()
    expect(screen.queryByText('候補と印を編集')).not.toBeInTheDocument()
    expect(screen.queryByRole('checkbox')).not.toBeInTheDocument()
    expect(screen.getByRole('region', { name: '買い目試算' })).toBeInTheDocument()
    expect(screen.getByRole('button', { name: '単勝' })).toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'ワイド' })).toBeInTheDocument()
    expect(screen.getByRole('button', { name: '馬連' })).toBeInTheDocument()
    expect(screen.getByRole('button', { name: '馬単' })).toBeInTheDocument()
    expect(screen.getByRole('button', { name: '三連複' })).toBeInTheDocument()
    expect(screen.getByRole('button', { name: '三連単' })).toBeInTheDocument()
  })

  it('recalculates each ticket type from the marked horses in one location', () => {
    mountTable()
    const trial = screen.getByRole('region', { name: '買い目試算' })
    fireEvent.click(within(trial).getByRole('button', { name: '三連単' }))
    expect(within(trial).getByText('12点')).toBeInTheDocument()
    fireEvent.click(within(trial).getByRole('button', { name: '単勝' }))
    expect(within(trial).getByText('5点')).toBeInTheDocument()
    const winTickets = within(trial).getByRole('group', { name: '単勝の買い目' })
    expect(within(winTickets).getAllByText(/^(1|7|3|11|6)$/).map(item => item.textContent)).toEqual(['1', '7', '3', '11', '6'])
    fireEvent.click(within(trial).getByRole('button', { name: '馬単' }))
    expect(within(trial).getByText('20点')).toBeInTheDocument()
    expect(within(trial).getByRole('group', { name: '馬単の買い目' })).toHaveTextContent('1→7')
    fireEvent.click(within(trial).getByRole('button', { name: '三連複' }))
    expect(within(trial).getByText('6点')).toBeInTheDocument()
    expect(within(trial).getByRole('group', { name: '三連複の買い目' })).toHaveTextContent('1-3-7')
    fireEvent.change(within(trial).getByRole('combobox', { name: '三連複の買い方' }), { target: { value: 'dual-axis' } })
    expect(within(trial).getByText('3点')).toBeInTheDocument()
    fireEvent.change(within(trial).getByRole('combobox', { name: '三連複の買い方' }), { target: { value: 'candidate-box' } })
    expect(within(trial).getByText('10点')).toBeInTheDocument()
  })

  it('limits direct table marks to five selected runners', () => {
    mountTable()
    fireEvent.change(screen.getByRole('combobox', { name: '17番の印を編集' }), { target: { value: '注' } })
    expect(screen.getByRole('status')).toHaveTextContent(/候補は最大5頭/)
    expect(screen.getByRole('combobox', { name: '17番の印を編集' })).toHaveValue('')
  })

  it('shows compact numeric position groups from the edited marks', () => {
    mountTable()
    fireEvent.change(screen.getByRole('combobox', { name: '6番の印を編集' }), { target: { value: '◎' } })
    openTrifectaTrial()
    fireEvent.change(screen.getByRole('combobox', { name: '三連単の買い方' }), { target: { value: 'narrow-second' } })
    const positions = screen.getByRole('group', { name: '三連単の着順候補' })
    expect(within(positions).getByText('6')).toBeInTheDocument()
    expect(within(positions).getByText('3・7')).toBeInTheDocument()
    expect(within(positions).getByText('1・3・7・11')).toBeInTheDocument()
    expect(screen.queryByRole('spinbutton')).not.toBeInTheDocument()
    expect(screen.queryByRole('button', { name: /購入|記録/ })).not.toBeInTheDocument()
  })
})
