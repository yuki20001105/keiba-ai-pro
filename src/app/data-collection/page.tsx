'use client'

import { useState, useEffect } from 'react'
import { supabase } from '@/lib/supabase'
import Link from 'next/link'
import { Logo } from '@/components/Logo'

export default function DataCollectionPage() {
  const [loading, setLoading] = useState(false)

  // Ultimate版常時ON（固定）
  const ultimateMode = true

  // 期間指定用
  const [startYear, setStartYear] = useState(2024)
  const [startMonth, setStartMonth] = useState(1)
  const [endYear, setEndYear] = useState(new Date().getFullYear())
  const [endMonth, setEndMonth] = useState(new Date().getMonth() + 1)
  const batchMaxWorkers = 3
  const [batchResult, setBatchResult] = useState<any>(null)
  const [batchLoading, setBatchLoading] = useState(false)
  const [batchProgress, setBatchProgress] = useState({ current: 0, total: 100, message: '' })

  // データ統計と表示
  const [dataStats, setDataStats] = useState({ totalRaces: 0, totalResults: 0, latestDate: '' })
  const [showCollectedData, setShowCollectedData] = useState(false)
  const [collectedRaces, setCollectedRaces] = useState<any[]>([])
  const [selectedRaceDetail, setSelectedRaceDetail] = useState<any>(null)

  useEffect(() => {
    loadStats()
  }, [])

  const loadStats = async () => {
    try {
      const { data: racesData, error: racesError } = await supabase
        .from('races')
        .select('race_id', { count: 'exact', head: true })
      
      const { data: resultsData, error: resultsError } = await supabase
        .from('race_results')
        .select('race_id, created_at', { count: 'exact' })
        .order('created_at', { ascending: false })
        .limit(1)
      
      if (!racesError && !resultsError) {
        setDataStats({
          totalRaces: racesData?.length || 0,
          totalResults: resultsData?.length || 0,
          latestDate: resultsData && resultsData.length > 0 ? resultsData[0].created_at : ''
        })
      }
    } catch (error) {
      console.error('統計取得エラー:', error)
    }
  }

  const fetchCollectedData = async () => {
    try {
      const { data, error } = await supabase
        .from('races')
        .select('*')
        .order('created_at', { ascending: false })
        .limit(50)
      if (error) throw error
      setCollectedRaces(data || [])
    } catch (error) {
      console.error('データ取得エラー:', error)
    }
  }

  const fetchRaceDetail = async (raceId: string) => {
    try {
      const { data, error } = await supabase
        .from('race_results')
        .select('*')
        .eq('race_id', raceId)
        .order('finish_position', { ascending: true })
      
      if (error) throw error
      setSelectedRaceDetail({ raceId, results: data || [] })
    } catch (error) {
      console.error('レース詳細取得エラー:', error)
    }
  }

  // 🚀 v2.0 期間指定バッチスクレイピング（超高速版）
  const handlePeriodBatchScrape = async () => {
    console.log('=== 期間バッチスクレイピング開始 ===')
    const startDate = new Date(startYear, startMonth - 1, 1)
    const endDate = new Date(endYear, endMonth - 1, 1)
    
    if (startDate > endDate) {
      alert('開始年月が終了年月より後になっています')
      return
    }

    const startDateStr = `${startYear}${String(startMonth).padStart(2, '0')}01`
    
    // 終了月の最終日を計算
    const lastDay = new Date(endYear, endMonth, 0).getDate()
    const endDateStr = `${endYear}${String(endMonth).padStart(2, '0')}${lastDay}`

    console.log('リクエストパラメータ:', {
      start_date: startDateStr,
      end_date: endDateStr,
      max_workers: batchMaxWorkers
    })

    const confirmMsg = `${startYear}年${startMonth}月 ～ ${endYear}年${endMonth}月のデータを取得します。続行しますか？`
    if (!confirm(confirmMsg)) return

    setBatchLoading(true)
    setBatchResult(null)
    setBatchProgress({ current: 0, total: 100, message: 'スクレイピング開始中...' })
    
    const startTime = Date.now()
    
    try {
      console.log('[Step 1] スクレイピングジョブ開始...')

      // Vercelプロキシ経由でジョブ開始（即座にjob_idが返る）
      const startRes = await fetch(`/api/scrape`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ start_date: startDateStr, end_date: endDateStr }),
      })

      if (!startRes.ok) {
        const err = await startRes.json()
        throw new Error(err.detail || `HTTP ${startRes.status}`)
      }

      const { job_id } = await startRes.json()
      console.log('[Step 2] ジョブ開始 job_id:', job_id)
      setBatchProgress({ current: 5, total: 100, message: `ジョブ開始 (${job_id}) - データ収集中...` })

      // ポーリング（3秒間隔）
      const pollInterval = 3000
      let completed = false

      while (!completed) {
        await new Promise(resolve => setTimeout(resolve, pollInterval))

        const statusRes = await fetch(`/api/scrape/status/${job_id}`)
        if (!statusRes.ok) continue

        const status = await statusRes.json()
        const prog = status.progress || {}

        if (prog.total > 0) {
          const pct = Math.min(95, Math.round((prog.done / prog.total) * 90) + 5)
          setBatchProgress({
            current: pct,
            total: 100,
            message: prog.message || `${prog.done}/${prog.total}日処理済み`,
          })
        }

        console.log('ジョブ状態:', status.status, prog.message)

        if (status.status === 'completed') {
          completed = true
          const result = status.result || {}
          setBatchProgress({ current: 100, total: 100, message: `完了: ${result.races_collected || 0}レース取得` })
          setBatchResult(result)
          alert(
            `取得完了\n\n` +
            `${result.message || '完了'}\n` +
            `所要時間: ${result.elapsed_time?.toFixed(1) || '?'}秒`
          )
          loadStats()
        } else if (status.status === 'error') {
          completed = true
          throw new Error(status.error || 'スクレイピングジョブが失敗しました')
        }
      }
    } catch (error: any) {
      console.error('=== エラー発生 ===')
      console.error('エラー詳細:', error)
      alert(`期間バッチ取得エラー: ${error.message}`)
      setBatchProgress({ current: 0, total: 100, message: 'エラーが発生しました' })
    } finally {
      setBatchLoading(false)
    }
  }

  return (
    <div className="min-h-screen bg-[#0a0a0a] text-white">
      <header className="border-b border-[#1e1e1e] px-6 py-4 flex items-center justify-between">
        <Logo href="/home" />
        <div className="flex items-center gap-4">
          <Link href="/home" className="flex items-center gap-1 text-xs text-[#555] hover:text-white transition-colors">
            <svg className="w-3 h-3" fill="none" viewBox="0 0 24 24" stroke="currentColor">
              <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M15 19l-7-7 7-7" />
            </svg>
            ホーム
          </Link>
          <span className="text-sm text-[#888]">データ取得</span>
        </div>
      </header>

      <main className="max-w-3xl mx-auto px-6 py-10 space-y-6">
        {/* 期間指定一括取得 */}
        <div className="bg-[#111] border border-[#1e1e1e] rounded-lg p-6 mb-6">
          <h2 className="text-sm font-medium text-[#888] mb-4">
            期間指定一括取得
          </h2>

          <div className="grid grid-cols-2 md:grid-cols-4 gap-4 mb-4">
            <div>
              <label className="block text-xs text-[#666] mb-2">開始年</label>
              <input
                type="number"
                value={startYear}
                onChange={(e) => setStartYear(parseInt(e.target.value))}
                min={2000}
                max={new Date().getFullYear()}
                className="w-full px-4 py-3 bg-[#0a0a0a] border border-[#1e1e1e] rounded-lg text-white focus:outline-none focus:border-[#333] transition-colors"
              />
            </div>
            
            <div>
              <label className="block text-xs text-[#666] mb-2">開始月</label>
              <select
                value={startMonth}
                onChange={(e) => setStartMonth(parseInt(e.target.value))}
                className="w-full px-4 py-3 bg-[#0a0a0a] border border-[#1e1e1e] rounded-lg text-white focus:outline-none focus:border-[#333] transition-colors"
              >
                {Array.from({ length: 12 }, (_, i) => i + 1).map(m => (
                  <option key={m} value={m}>{m}月</option>
                ))}
              </select>
            </div>
            
            <div>
              <label className="block text-xs text-[#666] mb-2">終了年</label>
              <input
                type="number"
                value={endYear}
                onChange={(e) => setEndYear(parseInt(e.target.value))}
                min={2000}
                max={new Date().getFullYear()}
                className="w-full px-4 py-3 bg-[#0a0a0a] border border-[#1e1e1e] rounded-lg text-white focus:outline-none focus:border-[#333] transition-colors"
              />
            </div>
            
            <div>
              <label className="block text-xs text-[#666] mb-2">終了月</label>
              <select
                value={endMonth}
                onChange={(e) => setEndMonth(parseInt(e.target.value))}
                className="w-full px-4 py-3 bg-[#0a0a0a] border border-[#1e1e1e] rounded-lg text-white focus:outline-none focus:border-[#333] transition-colors"
              >
                {Array.from({ length: 12 }, (_, i) => i + 1).map(m => (
                  <option key={m} value={m}>{m}月</option>
                ))}
              </select>
            </div>
          </div>

          <p className="text-sm text-[#888] mb-6">
            取得期間: {startYear}年{startMonth}月 〜 {endYear}年{endMonth}月
          </p>

          {/* 実行ボタン */}
          <button
            onClick={handlePeriodBatchScrape}
            disabled={batchLoading}
            className={`w-full py-4 rounded-lg font-medium transition-colors ${
              batchLoading
                ? 'bg-[#222] text-[#555] cursor-not-allowed'
                : 'bg-white text-black hover:bg-[#eee]'
            }`}
          >
            {batchLoading ? (
              <span className="flex items-center justify-center gap-2">
                <svg className="animate-spin h-4 w-4" viewBox="0 0 24 24">
                  <circle className="opacity-25" cx="12" cy="12" r="10" stroke="currentColor" strokeWidth="4" fill="none" />
                  <path className="opacity-75" fill="currentColor" d="M4 12a8 8 0 018-8V0C5.373 0 0 5.373 0 12h4zm2 5.291A7.962 7.962 0 014 12H0c0 3.042 1.135 5.824 3 7.938l3-2.647z" />
                </svg>
                取得中...
              </span>
            ) : 'データ取得開始'}
          </button>

          {/* 進捗バー */}
          {batchLoading && (
            <div className="mt-4 bg-[#161616] border border-[#1e1e1e] rounded-lg p-4">
              <div className="flex justify-between text-xs text-[#888] mb-2">
                <span>{batchProgress.message}</span>
                <span>{batchProgress.current}%</span>
              </div>
              <div className="w-full bg-[#1e1e1e] rounded-full h-2 overflow-hidden">
                <div
                  className="bg-white h-2 rounded-full transition-all duration-500"
                  style={{ width: `${batchProgress.current}%` }}
                />
              </div>
            </div>
          )}
        </div>

        {/* バッチ結果表示（期間指定でも使用） */}
        {batchResult && batchResult.stats && batchResult.stats.period && (
          <div className="bg-[#111] border border-[#1e1e1e] rounded-lg p-6 mb-6">
            <h3 className="text-lg font-semibold text-white mb-4">
              取得完了
            </h3>
            <div className="grid grid-cols-2 md:grid-cols-4 gap-4">
              <div className="bg-[#161616] border border-[#1e1e1e] rounded-lg p-4">
                <p className="text-[#aaa] text-xs mb-1">期間</p>
                <p className="text-base font-bold text-white">{batchResult.stats.period}</p>
              </div>
              <div className="bg-[#161616] border border-[#1e1e1e] rounded-lg p-4">
                <p className="text-[#aaa] text-xs mb-1">対象日数</p>
                <p className="text-base font-bold text-white">{batchResult.stats.days}日</p>
              </div>
              <div className="bg-[#161616] border border-[#1e1e1e] rounded-lg p-4">
                <p className="text-[#aaa] text-xs mb-1">取得レース数</p>
                <p className="text-base font-bold text-[#4ade80]">{batchResult.stats.success}/{batchResult.stats.total_races}</p>
              </div>
              <div className="bg-[#161616] border border-[#1e1e1e] rounded-lg p-4">
                <p className="text-[#aaa] text-xs mb-1">所要時間</p>
                <p className="text-base font-bold text-white">{batchResult.stats.elapsed_seconds}秒</p>
              </div>
            </div>
          </div>
        )}

        {/* 取得済みデータ確認 */}
        <div className="bg-[#111] border border-[#1e1e1e] rounded-lg p-6">
          <div className="flex justify-between items-center mb-6">
            <h2 className="text-sm font-medium text-[#888]">
              取得済みデータ
            </h2>
            <button
              onClick={() => setShowCollectedData(!showCollectedData)}
              className="text-sm bg-[#222] text-[#aaa] px-4 py-2 rounded-lg border border-[#333] hover:bg-[#2a2a2a] transition-colors"
            >
              {showCollectedData ? '閉じる' : 'データを表示'}
            </button>
          </div>

          {/* 統計情報 */}
          <div className="grid grid-cols-1 md:grid-cols-3 gap-4 mb-6">
            <div className="bg-[#161616] border border-[#1e1e1e] rounded-lg p-5">
              <div className="text-sm text-[#aaa] mb-2">総レース数</div>
              <div className="text-4xl font-extrabold text-white">
                {dataStats.totalRaces}
              </div>
            </div>
            <div className="bg-[#161616] border border-[#1e1e1e] rounded-lg p-5">
              <div className="text-sm text-[#aaa] mb-2">総出走馬数</div>
              <div className="text-4xl font-extrabold text-white">
                {dataStats.totalResults}
              </div>
            </div>
            <div className="bg-[#161616] border border-[#1e1e1e] rounded-lg p-5">
              <div className="text-sm text-[#aaa] mb-2">最終取得日時</div>
              <div className="text-lg font-bold text-[#ddd]">
                {dataStats.latestDate ? new Date(dataStats.latestDate).toLocaleString('ja-JP') : '未取得'}
              </div>
            </div>
          </div>

          {/* レース一覧 */}
          {showCollectedData && (
            <div className="animate-fade-in">
              <div className="flex justify-between items-center mb-4">
                <h3 className="text-sm font-medium text-[#888]">
                  最近取得したレース（最新50件）
                </h3>
                <button
                  onClick={() => fetchCollectedData()}
                  className="text-xs bg-[#222] text-[#888] px-3 py-2 rounded-lg border border-[#333] hover:bg-[#2a2a2a] transition"
                >
                  更新
                </button>
              </div>

              <div className="space-y-3 max-h-[600px] overflow-y-auto">
                {collectedRaces.map(race => (
                  <div
                    key={race.race_id}
                    className="bg-[#161616] border border-[#1e1e1e] rounded-lg p-5 hover:border-[#333] transition-colors"
                  >
                    <div className="flex justify-between items-start mb-3">
                      <div className="flex-1">
                        <div className="font-bold text-white text-xl mb-2">
                          {race.race_name}
                        </div>
                        <div className="flex flex-wrap gap-3 text-sm text-[#aaa]">
                          <span className="bg-[#1a1a1a] px-3 py-1 rounded-full">🏟️ {race.venue}</span>
                          <span className="bg-[#1a1a1a] px-3 py-1 rounded-full">📏 {race.distance}m</span>
                          <span className="bg-[#1a1a1a] px-3 py-1 rounded-full">🌱 {race.track_type}</span>
                          <span className="bg-[#1a1a1a] px-3 py-1 rounded-full">☀️ {race.weather}</span>
                          <span className="bg-[#1a1a1a] px-3 py-1 rounded-full">🏇 {race.field_condition}</span>
                        </div>
                      </div>
                      <button
                        onClick={() => fetchRaceDetail(race.race_id)}
                        className="ml-4 bg-[#222] text-white px-4 py-2 rounded-lg font-medium border border-[#333] hover:bg-[#2a2a2a] transition-colors"
                      >
                        📋 詳細
                      </button>
                    </div>
                    <div className="text-xs text-[#555] mt-2">
                      {race.race_id} &nbsp;|&nbsp; {new Date(race.created_at).toLocaleString('ja-JP')}
                    </div>
                  </div>
                ))}

                {collectedRaces.length === 0 && (
                  <div className="text-center py-12 text-[#888]">
                    <div className="hidden">📭</div>
                    <p className="text-lg font-semibold">データがまだ取得されていません</p>
                    <p className="text-sm mt-2">上記の機能を使ってレースデータを取得してください</p>
                  </div>
                )}
              </div>
            </div>
          )}
        </div>

        {/* レース詳細モーダル */}
        {selectedRaceDetail && (
          <div className="fixed inset-0 bg-black/50 flex items-center justify-center z-50 p-4" onClick={() => setSelectedRaceDetail(null)}>
            <div className="bg-[#111] rounded-lg max-w-5xl w-full max-h-[90vh] overflow-hidden" onClick={(e) => e.stopPropagation()}>
              <div className="bg-white text-black p-6">
                <div className="flex justify-between items-center">
                  <h3 className="text-2xl font-bold">レース詳細</h3>
                  <button
                    onClick={() => setSelectedRaceDetail(null)}
                    className="text-white hover:bg-[#111]/20 rounded-full w-10 h-10 flex items-center justify-center font-bold text-2xl transition"
                  >
                    ×
                  </button>
                </div>
                <div className="text-xs mt-1 opacity-70">{selectedRaceDetail.raceId}</div>
              </div>

              <div className="p-6 overflow-y-auto max-h-[calc(90vh-120px)]">
                <div className="mb-4 text-lg font-semibold text-[#ddd]">
                  出走馬一覧 <span className="text-blue-600">({selectedRaceDetail.results.length}頭)</span>
                </div>

                <div className="overflow-x-auto">
                  <table className="w-full text-sm">
                    <thead className="bg-[#1a1a1a] sticky top-0">
                      <tr>
                        <th className="px-3 py-3 text-left font-bold text-[#ddd]">着順</th>
                        <th className="px-3 py-3 text-left font-bold text-[#ddd]">枠</th>
                        <th className="px-3 py-3 text-left font-bold text-[#ddd]">馬番</th>
                        <th className="px-3 py-3 text-left font-bold text-[#ddd]">馬名</th>
                        <th className="px-3 py-3 text-left font-bold text-[#ddd]">性齢</th>
                        <th className="px-3 py-3 text-left font-bold text-[#ddd]">斤量</th>
                        <th className="px-3 py-3 text-left font-bold text-[#ddd]">騎手</th>
                        <th className="px-3 py-3 text-left font-bold text-[#ddd]">タイム</th>
                        <th className="px-3 py-3 text-left font-bold text-[#ddd]">オッズ</th>
                        <th className="px-3 py-3 text-left font-bold text-[#ddd]">人気</th>
                      </tr>
                    </thead>
                    <tbody>
                      {selectedRaceDetail.results.map((result: any, index: number) => (
                        <tr key={index} className={`border-b border-[#1a1a1a] hover:bg-[#161616] transition ${
                          result.finish_position === 1 ? 'bg-[#1a1600]' :
                          result.finish_position === 2 ? 'bg-[#161616]' :
                          result.finish_position === 3 ? 'bg-[#1a1000]' : ''
                        }`}>
                          <td className="px-3 py-3 font-bold text-white">
                            {result.finish_position}
                          </td>
                          <td className="px-3 py-3">{result.bracket_number}</td>
                          <td className="px-3 py-3 font-semibold">{result.horse_number}</td>
                          <td className="px-3 py-3 font-semibold text-white">{result.horse_name}</td>
                          <td className="px-3 py-3">{result.sex}{result.age}</td>
                          <td className="px-3 py-3">{result.jockey_weight}kg</td>
                          <td className="px-3 py-3">{result.jockey_name}</td>
                          <td className="px-3 py-3 font-mono">{result.finish_time?.toFixed(1)}s</td>
                          <td className="px-3 py-3 font-semibold">{result.odds?.toFixed(1)}</td>
                          <td className="px-3 py-3">{result.popularity}</td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
              </div>
            </div>
          </div>
        )}

        <div className="p-5 bg-[#111] border border-[#1e1e1e] rounded-lg flex items-center justify-between gap-4">
          <div>
            <div className="text-xs text-[#666] mb-0.5">次のステップ — 02</div>
            <div className="text-sm font-medium">モデル学習</div>
            <div className="text-xs text-[#555] mt-0.5">収集したデータでAIモデルをトレーニングします</div>
          </div>
          <Link
            href="/train"
            className="shrink-0 flex items-center gap-1.5 bg-white text-black text-sm font-medium px-5 py-2.5 rounded hover:bg-[#eee] transition-colors"
          >
            モデル学習へ
            <svg className="w-3.5 h-3.5" fill="none" viewBox="0 0 24 24" stroke="currentColor">
              <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M9 5l7 7-7 7" />
            </svg>
          </Link>
        </div>
      </main>
    </div>
  )
}
