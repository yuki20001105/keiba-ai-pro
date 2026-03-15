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
  const [forceRescrape, setForceRescrape] = useState(false)
  const [batchResult, setBatchResult] = useState<any>(null)
  const [batchLoading, setBatchLoading] = useState(false)
  const [batchProgress, setBatchProgress] = useState({ current: 0, total: 100, message: '' })

  // データ統計と表示
  const [dataStats, setDataStats] = useState({ totalRaces: 0, totalResults: 0, latestDate: '' })
  const [showCollectedData, setShowCollectedData] = useState(false)
  const [collectedRaces, setCollectedRaces] = useState<any[]>([])
  const [selectedRaceDetail, setSelectedRaceDetail] = useState<any>(null)

  // プロファイリング
  const [showProfiling, setShowProfiling] = useState(false)
  const [profilingJobId, setProfilingJobId] = useState<string | null>(null)
  const [profilingStatus, setProfilingStatus] = useState<'idle' | 'running' | 'completed' | 'error'>('idle')
  const [profilingMessage, setProfilingMessage] = useState('')
  const [profilingProgress, setProfilingProgress] = useState(0)
  const [useOptimized, setUseOptimized] = useState(true)

  // ローカルAPI稼働チェック
  const [localApiStatus, setLocalApiStatus] = useState<'checking' | 'online' | 'offline'>('checking')

  const checkLocalApi = async () => {
    setLocalApiStatus('checking')
    try {
      const res = await fetch('/api/scrape/status/__health_check__', { method: 'GET', signal: AbortSignal.timeout(3000) })
      // 200 or 422/404 はサーバーが起動している証拠
      setLocalApiStatus(res.status < 500 ? 'online' : 'offline')
    } catch {
      setLocalApiStatus('offline')
    }
  }

  useEffect(() => {
    loadStats()
    checkLocalApi()
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
        body: JSON.stringify({ start_date: startDateStr, end_date: endDateStr, force_rescrape: forceRescrape }),
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
      let failCount = 0  // 連続失敗カウンタ（Render再起動~30秒をカバー: 3s×10=30s）
      const MAX_FAIL = 10

      while (!completed) {
        await new Promise(resolve => setTimeout(resolve, pollInterval))

        const statusRes = await fetch(`/api/scrape/status/${job_id}`)
        if (!statusRes.ok) {
          failCount++
          console.warn(`ポーリング失敗 (${failCount}/${MAX_FAIL}) HTTP ${statusRes.status}`)
          if (failCount >= MAX_FAIL) {
            throw new Error(`サーバーが再起動しました (job_id: ${job_id})。\n取得済みデータはSupabaseに保存済みです。\n同じ期間で再実行すると取得済み日付は自動スキップされます。`)
          }
          continue
        }
        failCount = 0

        const status = await statusRes.json()
        const prog = status.progress || {}

        // Render再起動によるジョブ失厄を検知
        if (status.status === 'not_found') {
          failCount++
          console.warn(`ジョブが見つかりません (${failCount}/${MAX_FAIL})`)
          if (failCount >= MAX_FAIL) {
            throw new Error(`サーバーが再起動しジョブ情報が失われました (job_id: ${job_id})。\n取得済みデータはSupabaseに保存済みです。\n同じ期間で再実行すると取得済み日付は自動スキップされます。`)
          }
          continue
        }
        failCount = 0

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

  const handleStartProfiling = async () => {
    setProfilingStatus('running')
    setProfilingMessage('開始中...')
    setProfilingProgress(5)
    setProfilingJobId(null)

    const inferProgress = (msg: string): number => {
      if (msg.includes('読み込み')) return 15
      if (msg.includes('エンジニアリング')) return 35
      if (msg.includes('最適化')) return 55
      if (msg.includes('ydata-profiling') || msg.includes('生成中')) return 75
      if (msg.includes('完了')) return 100
      return 5
    }

    try {
      const res = await fetch('/api/profiling', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ use_optimized: useOptimized }),
      })
      if (!res.ok) throw new Error(`HTTP ${res.status}`)
      const { job_id } = await res.json()
      setProfilingJobId(job_id)
      // ポーリング（5秒間隔）
      const poll = setInterval(async () => {
        try {
          const sr = await fetch(`/api/profiling/status/${job_id}`)
          const st = await sr.json()
          const msg = st.message || ''
          setProfilingMessage(msg)
          setProfilingProgress(inferProgress(msg))
          if (st.status === 'completed') {
            clearInterval(poll)
            setProfilingStatus('completed')
            setProfilingProgress(100)
          } else if (st.status === 'error') {
            clearInterval(poll)
            setProfilingStatus('error')
            setProfilingProgress(0)
          }
        } catch { /* ignore poll error */ }
      }, 5000)
    } catch (e: any) {
      setProfilingStatus('error')
      setProfilingMessage(e.message)
      setProfilingProgress(0)
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
        {/* ローカル専用バナー */}
        <div className="bg-[#0d1117] border border-[#30363d] rounded-lg p-4 flex items-start gap-3">
          <span className="text-lg mt-0.5">🖥️</span>
          <div className="flex-1 min-w-0">
            <p className="text-sm font-medium text-[#e6edf3] mb-1">
              このページはローカル環境専用です
            </p>
            <p className="text-xs text-[#8b949e] leading-relaxed">
              netkeiba.comのIPブロック・Renderのタイムアウト回避のため、スクレイピングはローカルFastAPIのみ対応。<br />
              使用前に <code className="bg-[#161b22] px-1.5 py-0.5 rounded text-[#79c0ff] font-mono">python-api\main.py</code> を起動してください（ポート8000）。
            </p>
          </div>
          <div className="flex flex-col items-end gap-1.5 shrink-0">
            <div className="flex items-center gap-1.5">
              {localApiStatus === 'checking' && (
                <><svg className="animate-spin h-3.5 w-3.5 text-[#8b949e]" viewBox="0 0 24 24"><circle className="opacity-25" cx="12" cy="12" r="10" stroke="currentColor" strokeWidth="4" fill="none"/><path className="opacity-75" fill="currentColor" d="M4 12a8 8 0 018-8V0C5.373 0 0 5.373 0 12h4z"/></svg><span className="text-xs text-[#8b949e]">確認中...</span></>
              )}
              {localApiStatus === 'online' && (
                <><span className="w-2 h-2 rounded-full bg-[#3fb950] inline-block"></span><span className="text-xs text-[#3fb950] font-medium">起動中</span></>
              )}
              {localApiStatus === 'offline' && (
                <><span className="w-2 h-2 rounded-full bg-[#f85149] inline-block"></span><span className="text-xs text-[#f85149] font-medium">停止中</span></>
              )}
            </div>
            <button
              onClick={checkLocalApi}
              className="text-[10px] text-[#8b949e] hover:text-[#e6edf3] transition-colors underline underline-offset-2"
            >
              再確認
            </button>
          </div>
        </div>

        {/* 停止中の場合の警告 */}
        {localApiStatus === 'offline' && (
          <div className="bg-[#1a0a0a] border border-[#6e1c1c] rounded-lg p-4 flex items-center gap-3">
            <span className="text-yellow-400 text-lg shrink-0">⚠️</span>
            <div>
              <p className="text-sm text-[#f85149] font-medium mb-0.5">ローカルFastAPIが停止しています</p>
              <p className="text-xs text-[#8b949e]">
                VS Codeのタスク「Start FastAPI」を実行するか、<br/>
                <code className="bg-[#161b22] px-1 py-0.5 rounded text-[#79c0ff] font-mono text-[11px]">cd python-api; python main.py</code> を実行してください。
              </p>
            </div>
          </div>
        )}
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

          {/* 強制再取得オプション */}
          <label className="flex items-center gap-2 mb-4 cursor-pointer select-none">
            <input
              type="checkbox"
              checked={forceRescrape}
              onChange={e => setForceRescrape(e.target.checked)}
              className="w-4 h-4 accent-white"
            />
            <span className="text-sm text-[#aaa]">
              強制再取得（取得済みデータを上書き）
            </span>
          </label>

          {/* 実行ボタン */}
          <button
            onClick={handlePeriodBatchScrape}
            disabled={batchLoading || localApiStatus === 'offline'}
            className={`w-full py-4 rounded-lg font-medium transition-colors ${
              batchLoading || localApiStatus === 'offline'
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
            ) : localApiStatus === 'offline' ? 'ローカルAPI停止中（起動が必要）' : 'データ取得開始'}
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

        {/* プロファイリングレポート */}
        <div className="border border-[#1e1e1e] rounded-lg overflow-hidden">
          {/* ヘッダー（常時表示） */}
          <label className="flex items-center justify-between px-5 py-3.5 bg-[#111] cursor-pointer select-none">
            <span className="text-xs text-[#666]">特徴量プロファイリングレポート</span>
            <span className="flex items-center gap-2 text-xs text-[#555]">
              {!showProfiling ? '展開' : '閉じる'}
              <input
                type="checkbox"
                checked={showProfiling}
                onChange={e => setShowProfiling(e.target.checked)}
                className="w-3.5 h-3.5 accent-white"
              />
            </span>
          </label>

          {/* コンテンツ（折りたたみ） */}
          {showProfiling && (
            <div className="px-5 pb-5 pt-4 bg-[#0d0d0d] border-t border-[#1e1e1e] space-y-4">
              <label className="flex items-center gap-2 text-xs text-[#888] cursor-pointer select-none">
                <input
                  type="checkbox"
                  checked={useOptimized}
                  onChange={e => setUseOptimized(e.target.checked)}
                  className="w-3.5 h-3.5 accent-white"
                />
                LightGBM最適化済み（リーク除去・変換適用）
              </label>

              <div className="flex items-center gap-3">
                <button
                  onClick={handleStartProfiling}
                  disabled={profilingStatus === 'running'}
                  className={`px-4 py-2 rounded text-xs font-medium transition-colors ${
                    profilingStatus === 'running'
                      ? 'bg-[#1a1a1a] text-[#555] cursor-not-allowed'
                      : 'bg-white text-black hover:bg-[#eee]'
                  }`}
                >
                  {profilingStatus === 'running' ? (
                    <span className="flex items-center gap-1.5">
                      <svg className="animate-spin h-3 w-3" viewBox="0 0 24 24">
                        <circle className="opacity-25" cx="12" cy="12" r="10" stroke="currentColor" strokeWidth="4" fill="none" />
                        <path className="opacity-75" fill="currentColor" d="M4 12a8 8 0 018-8V0C5.373 0 0 5.373 0 12h4zm2 5.291A7.962 7.962 0 014 12H0c0 3.042 1.135 5.824 3 7.938l3-2.647z" />
                      </svg>
                      生成中...
                    </span>
                  ) : 'レポート生成'}
                </button>

                {profilingStatus === 'completed' && profilingJobId && (
                  <a
                    href={`/api/profiling/html/${profilingJobId}`}
                    target="_blank"
                    rel="noopener noreferrer"
                    className="text-xs text-[#4ade80] hover:underline"
                  >
                    レポートを開く →
                  </a>
                )}
              </div>

              {profilingStatus !== 'idle' && (
                <div className="space-y-1.5">
                  {(profilingStatus === 'running' || profilingStatus === 'completed') && (
                    <>
                      <div className="flex justify-between text-xs text-[#555]">
                        <span>{profilingMessage}</span>
                        <span>{profilingProgress}%</span>
                      </div>
                      <div className="w-full bg-[#1e1e1e] rounded-full h-1 overflow-hidden">
                        <div
                          className={`h-1 rounded-full transition-all duration-700 ${
                            profilingStatus === 'completed' ? 'bg-[#4ade80]' : 'bg-[#555]'
                          }`}
                          style={{ width: `${profilingProgress}%` }}
                        />
                      </div>
                    </>
                  )}
                  {profilingStatus === 'error' && (
                    <p className="text-xs text-red-400">{profilingMessage}</p>
                  )}
                </div>
              )}
            </div>
          )}
        </div>

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
