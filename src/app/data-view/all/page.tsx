'use client'

import { useEffect, useState } from 'react'
import Link from 'next/link'
import { Logo } from '@/components/Logo'
import { PremiumRequiredNotice } from '@/components/PremiumRequiredNotice'
import { useAuth } from '@/contexts/AuthContext'
import { authFetch } from '@/lib/auth-fetch'

type Dataset = 'races' | 'horses' | 'features'
type FeaturePipeline = 'local-db-shared-v1' | 'local-db-shared-optimized-v1' | 'local-db-context-v1'
type FeatureTarget = 'speed_deviation' | 'win' | 'place3' | 'rank'
type FeatureMissingSummary = {
  year?: string
  feature: string
  row_count: number
  missing_count: number
  missing_fraction: number
  all_missing?: boolean
  saved_history_gap_count: number
  unclassified_count: number
}
type TableResult = {
  dataset: Dataset
  pipeline?: FeaturePipeline
  target?: FeatureTarget
  columns: string[]
  records: Record<string, unknown>[]
  total_rows: number
  race_count: number
  horse_count: number
  feature_count?: number
  limit: number
  offset: number
  cache_hit?: boolean
  preparation_seconds?: number
  point_in_time_validated?: boolean
  target_baseline?: {
    available: boolean
    date_from?: string | null
    date_to?: string | null
    training_sample_count?: number
  } | null
  missing_summary?: FeatureMissingSummary[]
  missing_summary_by_year?: FeatureMissingSummary[]
}

const COLUMN_LABELS: Record<string, string> = {
  race_id: 'レースID', date: '開催日', race_date: '開催日', race_name: 'レース名',
  race_no: 'レース番号', venue: '競馬場', track_type: 'コース', surface: 'コース',
  distance: '距離', num_horses: '出走頭数', horse_number: '馬番', horse_num: '馬番',
  bracket_number: '枠番', horse_name: '馬名', jockey_name: '騎手', trainer_name: '調教師',
  age: '年齢', sex: '性別', sex_age: '性齢', burden_weight: '斤量', jockey_weight: '斤量',
  horse_weight: '馬体重', weight_kg: '馬体重', weight_change: '馬体重増減',
  horse_weight_change: '馬体重増減', odds: 'オッズ', popularity: '人気',
  finish_position: '着順', finish: '着順', finish_time: '走破タイム', time_seconds: '走破タイム',
  last_3f: '上がり3F', last_3f_time: '上がり3F', field_condition: '馬場状態',
  weather: '天候', race_class: 'クラス', data_observed_at: 'データ取得日時',
  odds_observation: 'オッズ取得証跡', odds_status: 'オッズ状態', source_url: '取得元URL',
  target_value: '目的変数', horse_id: '馬ID',
  safe_distance: '今回の距離（m）', safe_num_horses: '今回の出走頭数',
  safe_horse_number: '今回の馬番', safe_bracket_number: '今回の枠番', safe_age: '年齢',
  safe_burden_weight: '今回の斤量（kg）', safe_horse_weight: '今回の馬体重（kg）',
  safe_weight_change: '馬体重の前走比（kg）', safe_month: '開催月',
  safe_race_number: 'レース番号', safe_surface: '今回のコース種別', safe_venue: '競馬場',
  safe_sex: '性別', safe_field_condition: '馬場状態', safe_weather: '天候',
  safe_race_class: 'レースのクラス', safe_prior_starts: '過去の出走数',
  safe_days_since_last: '前走からの日数', safe_prev_finish: '前走の着順',
  safe_prev_time_seconds: '前走の走破タイム（秒）', safe_prev_distance: '前走の距離（m）',
  safe_prev_speed_mps: '前走の平均速度（m/s）', safe_prev2_finish: '2走前の着順',
  safe_prev2_time_seconds: '2走前の走破タイム（秒）', safe_prev2_distance: '2走前の距離（m）',
  safe_prev2_speed_mps: '2走前の平均速度（m/s）',
  safe_past3_count: '直近3走の記録数', safe_past3_observed_coverage: '直近3走の記録割合',
  safe_past3_avg_finish: '直近3走の平均着順', safe_past3_avg_speed_mps: '直近3走の平均速度（m/s）',
  safe_past3_avg_last3f_time: '直近3走の平均上がり3F（秒）', safe_past3_win_rate: '直近3走の勝率',
  safe_past3_top3_rate: '直近3走の3着以内率',
  safe_past5_count: '直近5走の記録数', safe_past5_observed_coverage: '直近5走の記録割合',
  safe_past5_avg_finish: '直近5走の平均着順', safe_past5_avg_speed_mps: '直近5走の平均速度（m/s）',
  safe_past5_avg_last3f_time: '直近5走の平均上がり3F（秒）', safe_past5_win_rate: '直近5走の勝率',
  safe_past5_top3_rate: '直近5走の3着以内率',
  safe_past10_count: '直近10走の記録数', safe_past10_observed_coverage: '直近10走の記録割合',
  safe_past10_avg_finish: '直近10走の平均着順', safe_past10_avg_speed_mps: '直近10走の平均速度（m/s）',
  safe_past10_avg_last3f_time: '直近10走の平均上がり3F（秒）', safe_past10_win_rate: '直近10走の勝率',
  safe_past10_top3_rate: '直近10走の3着以内率',
  ctx_condition_starts: '同じ馬場区分での過去走数',
  ctx_condition_rank_mean: '同じ馬場区分での平均着順',
  ctx_condition_speed_delta_mean: '同じ馬場区分での平均レース内速度差',
  ctx_course_starts: '同じ競馬場・コース種別での過去走数',
  ctx_course_rank_mean: '同じ競馬場での平均着順',
  ctx_course_speed_delta_mean: '同じ競馬場での平均レース内速度差',
  ctx_surface_changed: '前走からの芝・ダート変更',
  ctx_distance_change_ratio: '前走からの距離変更率',
  ctx_weight_history_count5: '直近5走の馬体重記録数',
  ctx_weight_delta_last: '前走の記録体重との差（kg）',
  ctx_weight_delta_mean5: '直近5走の記録体重平均との差（kg）',
  ctx_weight_std5: '直近5走の記録体重のばらつき',
  ctx_weight_change_pct: '公表馬体重の増減率',
  ctx_weight_trend5: '直近5走の馬体重傾向',
  ctx_sire_surface_starts: '父の産駒・同じコース種別の記録数',
  ctx_sire_surface_rank_mean: '父の産駒・同じコース種別の平均着順',
  ctx_sire_condition_starts: '父の産駒・同じ馬場区分の記録数',
  ctx_sire_condition_rank_mean: '父の産駒・同じ馬場区分の平均着順',
  ctx_damsire_surface_starts: '母父の産駒・同じコース種別の記録数',
  ctx_damsire_surface_rank_mean: '母父の産駒・同じコース種別の平均着順',
  ctx_pedigree_verified_prior: '過去データで確認できた血統情報',
  ctx_gate_fraction: '出走頭数に対する馬番の位置',
  ctx_gate_prior_count: '同条件・同馬番帯の過去記録数',
  ctx_gate_rank_mean: '同条件・同馬番帯の平均着順',
  ctx_gate_rank_delta: '同条件全体と比べた馬番帯の成績差',
  ctx_style_count5: '直近5走の通過順位記録数',
  ctx_early_position_mean5: '直近5走の前半通過順位の平均',
  ctx_early_position_std5: '直近5走の前半通過順位のばらつき',
  ctx_front_rate5: '直近5走の前方通過割合',
  ctx_late_gain_mean5: '直近5走の後半順位改善幅',
  ctx_field_front_pressure: '今回の出走馬全体の前方傾向',
  ctx_other_front_pressure: '他の出走馬の前方傾向',
  ctx_field_style_coverage: '今回の出走馬の通過順位データ割合',
  ctx_style_pressure_interaction: '自馬と他馬の前方傾向の組み合わせ',
}

function displayValue(value: unknown): string {
  if (value === null || value === undefined || value === '') return '—'
  if (typeof value === 'object') return JSON.stringify(value)
  if (typeof value === 'number') return Number.isFinite(value) ? value.toLocaleString('ja-JP', { maximumFractionDigits: 3 }) : '—'
  return String(value)
}

function friendlyError(payload: unknown, fallback: string): string {
  if (payload && typeof payload === 'object' && 'detail' in payload && typeof payload.detail === 'string') {
    return payload.detail
  }
  return fallback
}

export default function AllDataViewPage() {
  const { isPremium, loading: authLoading } = useAuth()
  const [dataset, setDataset] = useState<Dataset>('horses')
  const [pipeline, setPipeline] = useState<FeaturePipeline>('local-db-shared-optimized-v1')
  const [target, setTarget] = useState<FeatureTarget>('win')
  const [fromDate, setFromDate] = useState('')
  const [toDate, setToDate] = useState('')
  const [appliedRange, setAppliedRange] = useState({ from: '', to: '' })
  const [offset, setOffset] = useState(0)
  const [data, setData] = useState<TableResult | null>(null)
  const [loading, setLoading] = useState(false)
  const [downloading, setDownloading] = useState(false)
  const [error, setError] = useState('')
  const [missingYear, setMissingYear] = useState('all')
  const pageSize = 100

  useEffect(() => {
    if (authLoading || !isPremium) return
    if (dataset === 'features' && (!appliedRange.from || !appliedRange.to)) {
      setData(null)
      setError('学習前特徴量を見るには、開始年月と終了年月を指定してください。')
      return
    }
    const controller = new AbortController()
    setLoading(true)
    setError('')
    const params = new URLSearchParams({
      dataset,
      limit: String(pageSize),
      offset: String(offset),
    })
    if (dataset === 'features') {
      params.set('pipeline', pipeline)
      params.set('target', target)
      if (appliedRange.from) params.set('date_from', appliedRange.from)
      if (appliedRange.to) params.set('date_to', appliedRange.to)
    } else {
      if (appliedRange.from) params.set('from_date', appliedRange.from)
      if (appliedRange.to) params.set('to_date', appliedRange.to)
    }

    authFetch(`/api/dataframe?${params}`, { signal: controller.signal })
      .then(async response => {
        const payload = await response.json().catch(() => ({}))
        if (!response.ok) throw new Error(friendlyError(payload, `データの読み込みに失敗しました（HTTP ${response.status}）`))
        setData(payload as TableResult)
      })
      .catch(reason => {
        if (!controller.signal.aborted) {
          setError(reason instanceof Error ? reason.message : 'データの読み込みに失敗しました')
          setData(null)
        }
      })
      .finally(() => { if (!controller.signal.aborted) setLoading(false) })

    return () => controller.abort()
  }, [appliedRange, authLoading, dataset, isPremium, offset, pipeline, target])

  const applyRange = (event: React.FormEvent<HTMLFormElement>) => {
    event.preventDefault()
    const formValues = new FormData(event.currentTarget)
    const from = String(formValues.get('range_from') || '')
    const to = String(formValues.get('range_to') || '')
    setFromDate(from)
    setToDate(to)
    setOffset(0)
    setAppliedRange({ from, to })
  }

  const downloadCsv = async () => {
    setDownloading(true)
    setError('')
    const params = new URLSearchParams({ dataset, format: 'csv' })
    if (dataset === 'features') {
      params.set('pipeline', pipeline)
      params.set('target', target)
      if (appliedRange.from) params.set('date_from', appliedRange.from)
      if (appliedRange.to) params.set('date_to', appliedRange.to)
    } else {
      if (appliedRange.from) params.set('from_date', appliedRange.from)
      if (appliedRange.to) params.set('to_date', appliedRange.to)
    }
    try {
      const response = await authFetch(`/api/dataframe?${params}`)
      if (!response.ok) {
        const payload = await response.json().catch(() => ({}))
        throw new Error(friendlyError(payload, `CSVの作成に失敗しました（HTTP ${response.status}）`))
      }
      const blob = await response.blob()
      const url = URL.createObjectURL(blob)
      const anchor = document.createElement('a')
      anchor.href = url
      anchor.download = dataset === 'features'
        ? `keiba_features_${pipeline}_${target}_${appliedRange.from}_${appliedRange.to}.csv`
        : `keiba_${dataset === 'races' ? 'races' : 'horse-data'}_${appliedRange.from || 'all'}_${appliedRange.to || 'all'}.csv`
      document.body.appendChild(anchor)
      anchor.click()
      anchor.remove()
      URL.revokeObjectURL(url)
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : 'CSVの作成に失敗しました')
    } finally {
      setDownloading(false)
    }
  }

  const page = data ? Math.floor(data.offset / data.limit) + 1 : 1
  const pageCount = data ? Math.max(1, Math.ceil(data.total_rows / data.limit)) : 1
  const missingYears = [...new Set((data?.missing_summary_by_year ?? []).map(item => item.year).filter((year): year is string => Boolean(year)))].sort().reverse()
  const visibleMissingSummary = missingYear === 'all'
    ? data?.missing_summary ?? []
    : (data?.missing_summary_by_year ?? []).filter(item => item.year === missingYear)

  return (
    <div className="min-h-screen bg-[#0a0a0a] text-white flex flex-col">
      <header className="border-b border-[#1e1e1e] px-6 py-4 flex items-center justify-between">
        <div className="flex items-center gap-4">
          <Logo href="/home" />
          <span className="text-sm text-[#888]">保存済みデータ一覧</span>
          <span className="text-[10px] px-2 py-0.5 rounded border border-yellow-600/40 bg-yellow-500/20 text-yellow-400">Premium 専用</span>
        </div>
        <Link href="/data-view" className="text-xs text-[#888] hover:text-white">一レース表示へ</Link>
      </header>

      <main className="mx-auto w-full max-w-[1600px] p-5 md:p-8">
        {!authLoading && !isPremium ? (
          <PremiumRequiredNotice
            title="保存済みデータの表示は Premium 専用です"
            message="認証後、保存済みのレース情報・馬データを読み取り専用で表示します。"
          />
        ) : (
          <>
            <div className="mb-5 flex flex-wrap items-end justify-between gap-4">
              <div>
                <h1 className="text-xl font-bold">取得済みデータ</h1>
                <p className="mt-1 text-xs text-[#777]">取得データベースは読み取り専用です。学習前特徴量は学習用の派生キャッシュを再利用し、未作成の場合のみ生成します。</p>
              </div>
              <button
                type="button"
                onClick={downloadCsv}
                disabled={downloading || loading || !data || data.total_rows === 0}
                className="rounded bg-white px-4 py-2 text-sm font-semibold text-black disabled:cursor-not-allowed disabled:opacity-40"
              >{downloading ? 'CSVを準備中...' : `${dataset === 'races' ? 'レース情報' : dataset === 'features' ? '学習前特徴量' : '馬データ'}をCSVで保存`}</button>
            </div>

            <div className="mb-4 rounded-lg border border-[#242424] bg-[#101010] p-4">
              <div className="mb-4 flex flex-wrap gap-2">
                {([
                  ['horses', '馬データ'],
                  ['races', 'レース情報'],
                  ['features', '学習前特徴量'],
                ] as const).map(([value, label]) => (
                  <button
                    type="button"
                    key={value}
                    onClick={() => {
                      setDataset(value)
                      setOffset(0)
                      setError('')
                      setFromDate('')
                      setToDate('')
                      setAppliedRange({ from: '', to: '' })
                    }}
                    className={`rounded px-3 py-2 text-sm ${dataset === value ? 'bg-[#303030] text-white' : 'bg-[#171717] text-[#888] hover:text-white'}`}
                  >{label}</button>
                ))}
              </div>
              <form onSubmit={applyRange} className="flex flex-wrap items-end gap-3">
                <label className="text-xs text-[#888]">
                  {dataset === 'features' ? '開始年月' : '開始日'}
                  <input name="range_from" type={dataset === 'features' ? 'month' : 'date'} value={fromDate} onChange={event => setFromDate(event.target.value)} className="mt-1 block rounded border border-[#303030] bg-[#090909] px-3 py-2 text-sm text-white" />
                </label>
                <label className="text-xs text-[#888]">
                  {dataset === 'features' ? '終了年月' : '終了日'}
                  <input name="range_to" type={dataset === 'features' ? 'month' : 'date'} value={toDate} onChange={event => setToDate(event.target.value)} className="mt-1 block rounded border border-[#303030] bg-[#090909] px-3 py-2 text-sm text-white" />
                </label>
                <button type="submit" className="rounded border border-[#3a3a3a] px-4 py-2 text-sm hover:bg-[#1c1c1c]">期間を適用</button>
                {dataset !== 'features' && <span className="pb-2 text-xs text-[#666]">期間未指定は全期間</span>}
              </form>
              {dataset === 'features' && (
                <div className="mt-4 flex flex-wrap items-end gap-3 border-t border-[#242424] pt-4">
                  <label className="text-xs text-[#888]">
                    特徴量構成
                    <select value={pipeline} onChange={event => { setPipeline(event.target.value as FeaturePipeline); setOffset(0) }} className="mt-1 block rounded border border-[#303030] bg-[#090909] px-3 py-2 text-sm text-white">
                      <option value="local-db-shared-optimized-v1">候補46項目</option>
                      <option value="local-db-shared-v1">基準47項目</option>
                      <option value="local-db-context-v1">条件・展開80項目</option>
                    </select>
                  </label>
                  <label className="text-xs text-[#888]">
                    目的変数
                    <select value={target} onChange={event => { setTarget(event.target.value as FeatureTarget); setOffset(0) }} className="mt-1 block rounded border border-[#303030] bg-[#090909] px-3 py-2 text-sm text-white">
                      <option value="speed_deviation">速度偏差</option>
                      <option value="win">1着（勝ち）</option>
                      <option value="place3">3着以内</option>
                      <option value="rank">着順スコア</option>
                    </select>
                  </label>
                  <p className="max-w-2xl pb-2 text-xs text-[#666]">目的変数は確認用のラベルです。特徴量にはレース結果・オッズを含めません。速度偏差は表示期間より前の適格なレース（最大365日分）だけで基準を作るため、表示期間自体が短くても確認できます。</p>
                </div>
              )}
            </div>

            {data && (
              <div className="mb-3 flex flex-wrap items-center gap-x-5 gap-y-1 text-xs text-[#999]">
                <span>{dataset === 'features' ? '対象レース数' : 'レース数'} <b className="text-white">{data.race_count.toLocaleString('ja-JP')}</b></span>
                <span>{dataset === 'features' ? '対象行数' : '馬データ行数'} <b className="text-white">{data.horse_count.toLocaleString('ja-JP')}</b></span>
                {dataset === 'features' && <span>特徴量 <b className="text-white">{data.feature_count ?? data.columns.length}項目</b></span>}
                <span>この表: {data.total_rows.toLocaleString('ja-JP')} 行・{data.columns.length} 列</span>
                <span>ページ {page} / {pageCount}</span>
                {dataset === 'features' && <span>{data.cache_hit ? '生成済み特徴量を再利用' : `特徴量を生成（${data.preparation_seconds ?? 0}秒）`}</span>}
                {dataset === 'features' && data.target === 'speed_deviation' && data.target_baseline?.available && <span>速度偏差の基準期間 {data.target_baseline.date_from ?? '—'}〜{data.target_baseline.date_to ?? '—'}（{(data.target_baseline.training_sample_count ?? 0).toLocaleString('ja-JP')}頭）</span>}
              </div>
            )}

            {error && <div role="alert" className="mb-3 rounded border border-red-900 bg-[#190909] p-3 text-sm text-red-300">{error}</div>}
            {dataset === 'features' && data?.target === 'speed_deviation' && data.target_baseline && !data.target_baseline.available && <div role="status" className="mb-3 rounded border border-yellow-900 bg-[#171306] p-3 text-sm text-yellow-200">表示期間より前に速度偏差の基準を作れる履歴がありません。表とCSVは表示しますが、速度偏差の目的変数は空欄です。開始年月を後ろにずらすと、過去履歴を基準に計算できます。</div>}
            {loading && <div className="mb-3 text-xs text-[#777]">{dataset === 'features' ? '学習前特徴量を準備中です。初回や期間変更時は時間がかかることがあります...' : '保存済みデータを読み込み中...'}</div>}

            {dataset === 'features' && data && (
              <section aria-label="特徴量の欠損状況" className="mb-4 rounded-lg border border-[#252525] bg-[#101010] p-4">
                <div className="mb-3 flex flex-wrap items-end justify-between gap-3">
                  <div>
                    <h2 className="text-sm font-semibold">特徴量ごとの欠損状況</h2>
                    <p className="mt-1 text-xs text-[#888]">生成した学習前特徴量を集計しています。保存済みレースデータの取得率そのものとは異なります。</p>
                  </div>
                  <label className="text-xs text-[#888]">
                    集計期間
                    <select value={missingYear} onChange={event => setMissingYear(event.target.value)} className="ml-2 rounded border border-[#303030] bg-[#090909] px-3 py-2 text-sm text-white">
                      <option value="all">指定期間全体</option>
                      {missingYears.map(year => <option key={year} value={year}>{year}年</option>)}
                    </select>
                  </label>
                </div>
                <div className="max-h-64 overflow-auto rounded border border-[#252525]">
                  <table className="min-w-full border-collapse text-xs">
                    <thead className="sticky top-0 bg-[#151515] text-[#aaa]">
                      <tr>
                        <th className="border-b border-[#292929] px-3 py-2 text-left">特徴量</th>
                        <th className="border-b border-[#292929] px-3 py-2 text-right">欠損数 / 対象数</th>
                        <th className="border-b border-[#292929] px-3 py-2 text-right">欠損率</th>
                        <th className="border-b border-[#292929] px-3 py-2 text-right">保存済み履歴不足</th>
                        <th className="border-b border-[#292929] px-3 py-2 text-right">原因未分類</th>
                      </tr>
                    </thead>
                    <tbody>
                      {visibleMissingSummary.map(item => (
                        <tr key={`${item.year ?? 'all'}-${item.feature}`}>
                          <td className="border-b border-[#1d1d1d] px-3 py-2">{COLUMN_LABELS[item.feature] ?? item.feature.replaceAll('_', ' ')}</td>
                          <td className="border-b border-[#1d1d1d] px-3 py-2 text-right">{item.missing_count.toLocaleString('ja-JP')} / {item.row_count.toLocaleString('ja-JP')}</td>
                          <td className="border-b border-[#1d1d1d] px-3 py-2 text-right">{(item.missing_fraction * 100).toFixed(1)}%{item.all_missing ? '（全件）' : ''}</td>
                          <td className="border-b border-[#1d1d1d] px-3 py-2 text-right">{item.saved_history_gap_count.toLocaleString('ja-JP')}</td>
                          <td className="border-b border-[#1d1d1d] px-3 py-2 text-right">{item.unclassified_count.toLocaleString('ja-JP')}</td>
                        </tr>
                      ))}
                      {visibleMissingSummary.length === 0 && <tr><td colSpan={5} className="p-4 text-center text-[#777]">この期間に欠損はありません</td></tr>}
                    </tbody>
                  </table>
                </div>
                <p className="mt-2 text-[11px] text-[#777]">「保存済み履歴不足」は特徴量と履歴件数から確認できた範囲です。新人・初出走を断定せず、その他の欠損原因は未分類として扱います。</p>
              </section>
            )}

            <div className="max-h-[65vh] overflow-auto rounded-lg border border-[#252525]">
              <table className="min-w-full border-collapse text-xs">
                <thead className="sticky top-0 z-10 bg-[#151515]">
                  <tr>
                    {data?.columns.map(column => (
                      <th key={column} title={column} className="border-b border-r border-[#292929] px-3 py-2 text-left align-bottom whitespace-nowrap">
                        <span className="block text-[#ddd]">{COLUMN_LABELS[column] ?? column.replaceAll('_', ' ')}</span>
                      </th>
                    ))}
                  </tr>
                </thead>
                <tbody>
                  {data?.records.map((record, index) => (
                    <tr key={`${String(record.race_id)}-${String(record.horse_number ?? index)}`} className={index % 2 ? 'bg-[#0d0d0d]' : 'bg-[#090909]'}>
                      {data.columns.map(column => (
                        <td key={column} title={displayValue(record[column])} className="max-w-[280px] border-b border-r border-[#1d1d1d] px-3 py-2 align-top whitespace-pre-wrap break-words text-[#ddd]">
                          {displayValue(record[column])}
                        </td>
                      ))}
                    </tr>
                  ))}
                  {!loading && data?.records.length === 0 && (
                    <tr><td colSpan={Math.max(data.columns.length, 1)} className="p-8 text-center text-[#777]">該当する保存済みデータはありません</td></tr>
                  )}
                </tbody>
              </table>
            </div>

            <div className="mt-4 flex items-center justify-between">
              <button type="button" disabled={!data || offset === 0 || loading} onClick={() => setOffset(Math.max(0, offset - pageSize))} className="rounded border border-[#333] px-3 py-2 text-xs disabled:opacity-30">前の100行</button>
              <span className="text-xs text-[#777]">一覧は100行ずつ表示。CSVは1回あたり最大20万行です。{dataset === 'features' ? ' 表示はモデル学習を実行しません。' : ''}</span>
              <button type="button" disabled={!data || offset + pageSize >= data.total_rows || loading} onClick={() => setOffset(offset + pageSize)} className="rounded border border-[#333] px-3 py-2 text-xs disabled:opacity-30">次の100行</button>
            </div>
          </>
        )}
      </main>
    </div>
  )
}
