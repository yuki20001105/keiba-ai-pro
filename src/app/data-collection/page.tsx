'use client'

import { useState, useEffect } from 'react'
import { supabase } from '@/lib/supabase'
import { useRouter } from 'next/navigation'
import { useUltimateMode } from '@/contexts/UltimateModeContext'
import { AdminOnly } from '@/components/AdminOnly'

export default function DataCollectionPage() {
  const router = useRouter()
  const [loading, setLoading] = useState(false)
  const [authLoading, setAuthLoading] = useState(true)
  const [userId, setUserId] = useState<string | null>(null)
  const { ultimateMode, setUltimateMode, includeDetails, setIncludeDetails } = useUltimateMode()
  
  // 環境チェック（ブラウザ環境でも安全）
  const isProduction = typeof window !== 'undefined' && window.location.hostname !== 'localhost'

  // 単一レースID取得
  const [raceId, setRaceId] = useState('')
  const [scrapeResult, setScrapeResult] = useState<any>(null)

  // 🚀 v2.0 バッチスクレイピング用
  const [batchRaceIds, setBatchRaceIds] = useState('')
  const [batchMaxWorkers, setBatchMaxWorkers] = useState(7)
  const [batchResult, setBatchResult] = useState<any>(null)
  const [batchLoading, setBatchLoading] = useState(false)
  const [batchProgress, setBatchProgress] = useState({ current: 0, total: 0, message: '' })

  // 期間指定用
  const [startYear, setStartYear] = useState(2024)
  const [startMonth, setStartMonth] = useState(1)
  const [endYear, setEndYear] = useState(new Date().getFullYear())
  const [endMonth, setEndMonth] = useState(new Date().getMonth() + 1)
  const [bulkProgress, setBulkProgress] = useState<string>('')
  const [bulkMode, setBulkMode] = useState(false)
  const [bulkStats, setBulkStats] = useState({ 
    totalMonths: 0, 
    completedMonths: 0, 
    completedRaces: 0 
  })

  // データ統計と表示
  const [dataStats, setDataStats] = useState({ totalRaces: 0, totalResults: 0, latestDate: '', dbPath: '' })
  const [showCollectedData, setShowCollectedData] = useState(false)
  const [collectedRaces, setCollectedRaces] = useState<any[]>([])
  const [selectedRaceDetail, setSelectedRaceDetail] = useState<any>(null)


  useEffect(() => {
    const getUser = async () => {
      setAuthLoading(true)
      if (!supabase) {
        console.error('Supabase client not initialized')
        setAuthLoading(false)
        return
      }
      const { data: { user } } = await supabase.auth.getUser()
      if (!user) {
        router.push('/auth/login')
        return
      }
      setUserId(user.id)
      await loadStats()
      setAuthLoading(false)
    }
    getUser()
  }, [router, ultimateMode])

  const loadStats = async () => {
    try {
      const dbPath = ultimateMode ? 'keiba_ultimate.db' : 'keiba.db'
      
      // Supabaseから統計情報を取得
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
          latestDate: resultsData && resultsData.length > 0 ? resultsData[0].created_at : '',
          dbPath
        })
      }
    } catch (error) {
      console.error('統計取得エラー:', error)
    }
  }

  const fetchCollectedData = async (userId: string) => {
    try {
      const { data, error } = await supabase
        .from('races')
        .select('*')
        .eq('user_id', userId)
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

  const handleScrapeRace = async () => {
    if (!raceId.trim()) {
      alert('レースIDを入力してください')
      return
    }

    setLoading(true)
    setScrapeResult(null)
    try {
      const port = ultimateMode ? 8001 : 8000
      const endpoint = ultimateMode ? '/scrape/ultimate' : `/scrape/${raceId}`
      
      let response
      if (ultimateMode) {
        // Ultimate版はPOSTでinclude_detailsを送信
        response = await fetch(`http://localhost:${port}${endpoint}`, {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({
            race_id: raceId,
            include_details: includeDetails
          })
        })
      } else {
        response = await fetch(`http://localhost:${port}${endpoint}`)
      }
      
      if (!response.ok) throw new Error(`HTTP ${response.status}`)
      
      const data = await response.json()
      setScrapeResult(data)
      alert(`データ取得完了！${includeDetails ? '（詳細情報含む）' : '（高速モード）'}`)
      loadStats()
    } catch (error: any) {
      alert(`取得エラー: ${error.message}`)
    } finally {
      setLoading(false)
    }
  }

  // 🚀 v2.0 バッチスクレイピング実行
  const handleBatchScrape = async () => {
    if (!batchRaceIds.trim()) {
      alert('レースIDを入力してください（複数の場合はカンマ区切り）')
      return
    }

    // カンマまたは改行で分割
    const raceIdArray = batchRaceIds
      .split(/[\n,]+/)
      .map(id => id.trim())
      .filter(id => id.length > 0)

    if (raceIdArray.length === 0) {
      alert('有効なレースIDが入力されていません')
      return
    }

    if (raceIdArray.length > 20) {
      if (!confirm(`${raceIdArray.length}レースを取得します。時間がかかる可能性がありますが続行しますか？`)) {
        return
      }
    }

    setBatchLoading(true)
    setBatchResult(null)
    
    try {
      const response = await fetch('http://localhost:8001/scrape/ultimate/batch', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          race_ids: raceIdArray,
          include_details: includeDetails,
          max_workers: batchMaxWorkers
        })
      })
      
      if (!response.ok) {
        const errorData = await response.json()
        throw new Error(errorData.detail || `HTTP ${response.status}`)
      }
      
      const data = await response.json()
      setBatchResult(data)
      
      const stats = data.stats
      alert(
        `バッチ取得完了！\n\n` +
        `成功: ${stats.success}/${stats.total}レース\n` +
        `所要時間: ${stats.elapsed_seconds}秒\n` +
        `1レースあたり: ${stats.avg_seconds_per_race}秒\n` +
        `高速化率: 約${stats.speedup_vs_sequential}倍`
      )
      
      loadStats()
    } catch (error: any) {
      alert(`バッチ取得エラー: ${error.message}`)
      console.error('Batch scrape error:', error)
    } finally {
      setBatchLoading(false)
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
      include_details: includeDetails,
      max_workers: batchMaxWorkers
    })

    const confirmMsg = `${startYear}年${startMonth}月 ～ ${endYear}年${endMonth}月のデータを並列バッチ取得します。\n\n⚡ 新機能: 期間内の全レースを高速並列取得\n並列数: ${batchMaxWorkers}\n\n続行しますか？`
    if (!confirm(confirmMsg)) return

    setBatchLoading(true)
    setBatchResult(null)
    setBatchProgress({ current: 0, total: 100, message: 'Phase 1: レース一覧取得中...' })
    
    const startTime = Date.now()
    
    try {
      console.log('[Phase 1] レース一覧取得開始...')
      
      // Phase 1の進捗シミュレーション
      const phase1Timer = setInterval(() => {
        setBatchProgress(prev => {
          if (prev.current < 20) {
            return { ...prev, current: prev.current + 5 }
          }
          return prev
        })
      }, 500)
      
      console.log('APIリクエスト送信:', 'http://localhost:8001/scrape/ultimate/batch_by_period')
      
      // タイムアウト設定（10分）- 完全モード対応
      const controller = new AbortController()
      const timeoutId = setTimeout(() => controller.abort(), 600000) // 10分
      
      const response = await fetch('http://localhost:8001/scrape/ultimate/batch_by_period', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          start_date: startDateStr,
          end_date: endDateStr,
          include_details: includeDetails,
          max_workers: batchMaxWorkers
        }),
        signal: controller.signal
      })
      
      clearTimeout(timeoutId)
      
      clearInterval(phase1Timer)
      
      console.log('APIレスポンス受信:', response.status, response.statusText)
      
      if (!response.ok) {
        const errorData = await response.json()
        console.error('APIエラーレスポンス:', errorData)
        throw new Error(errorData.detail || `HTTP ${response.status}`)
      }
      
      // Phase 2開始
      console.log('[Phase 2] 並列スクレイピング開始...')
      setBatchProgress({ current: 20, total: 100, message: 'Phase 2: 並列スクレイピング中...' })
      
      const data = await response.json()
      
      console.log('取得完了！データサマリー:', {
        resultsKeys: Object.keys(data.results || {}),
        resultsCount: Object.keys(data.results || {}).length,
        stats: data.stats
      })
      
      // 各レースの詳細をログ出力
      if (data.results) {
        Object.keys(data.results).forEach((raceId, index) => {
          const race = data.results[raceId]
          console.log(`レース ${index + 1} (${raceId}):`, {
            race_name: race.race_info?.race_name,
            place: race.race_info?.place,
            distance: race.race_info?.distance,
            results_count: race.results?.length || 0,
            first_horse: race.results?.[0] ? {
              horse_name: race.results[0].horse_name,
              horse_id: race.results[0].horse_id,
              jockey_id: race.results[0].jockey_id,
              trainer_id: race.results[0].trainer_id,
              weight_kg: race.results[0].weight_kg,
              columns: Object.keys(race.results[0]).length
            } : 'データなし'
          })
        })
      }
      
      setBatchResult(data)
      
      const stats = data.stats
      const elapsed = (Date.now() - startTime) / 1000
      
      console.log('=== 完了統計 ===')
      console.log('期間:', stats.period)
      console.log('対象日数:', stats.days, '日')
      console.log('総レース数:', stats.total_races)
      console.log('成功:', stats.success)
      console.log('失敗:', stats.failed)
      console.log('所要時間:', stats.elapsed_seconds, '秒')
      console.log('フロントエンド所要時間:', elapsed.toFixed(2), '秒')
      console.log('1レースあたり:', stats.avg_seconds_per_race, '秒')
      console.log('高速化率:', stats.speedup_vs_sequential, '倍')
      
      setBatchProgress({ 
        current: 100, 
        total: 100, 
        message: `✅ 完了: ${stats.success}/${stats.total_races}レース` 
      })
      
      alert(
        `期間バッチ取得完了！\n\n` +
        `期間: ${stats.period}\n` +
        `対象日数: ${stats.days}日\n` +
        `成功: ${stats.success}/${stats.total_races}レース\n` +
        `所要時間: ${stats.elapsed_seconds}秒\n` +
        `1レースあたり: ${stats.avg_seconds_per_race}秒\n` +
        `高速化率: 約${stats.speedup_vs_sequential}倍`
      )
      
      loadStats()
    } catch (error: any) {
      console.error('=== エラー発生 ===')
      console.error('エラー詳細:', error)
      console.error('エラーメッセージ:', error.message)
      console.error('エラースタック:', error.stack)
      
      alert(`期間バッチ取得エラー: ${error.message}`)
      console.error('Period batch scrape error:', error)
      setBatchProgress({ current: 0, total: 100, message: '❌ エラーが発生しました' })
    } finally {
      setBatchLoading(false)
    }
  }

  // 期間指定で一括取得（旧バージョン - 後方互換性のため保持）
  const bulkScrapeByPeriod = async () => {
    if (!userId) {
      alert('ユーザー情報が取得できません')
      return
    }

    // 期間の妥当性チェック
    const startDate = new Date(startYear, startMonth - 1)
    const endDate = new Date(endYear, endMonth - 1)
    
    if (startDate > endDate) {
      alert('開始年月が終了年月より後になっています')
      return
    }

    // 月数を計算
    const totalMonths = (endYear - startYear) * 12 + (endMonth - startMonth) + 1

    const confirmMsg = `${startYear}年${startMonth}月 ～ ${endYear}年${endMonth}月のデータを一括取得します。\n\n対象期間: ${totalMonths}ヶ月\n※大量のデータ取得となる可能性があるため、時間がかかります。\n\n続行しますか？`
    if (!confirm(confirmMsg)) return

    setLoading(true)
    setBulkMode(true)
    setBulkProgress('開始準備中...')
    setBulkStats({ totalMonths, completedMonths: 0, completedRaces: 0 })

    let totalRacesScraped = 0
    let currentYear = startYear
    let currentMonth = startMonth
    let completedMonthsCount = 0

    try {
      while (currentYear < endYear || (currentYear === endYear && currentMonth <= endMonth)) {
        setBulkProgress(`${currentYear}年${currentMonth}月 のレースを検索中...`)
        
        const yearMonth = `${currentYear}${String(currentMonth).padStart(2, '0')}`
        let successCount = 0
        
        // 月の各日（01-31）について、レース一覧を取得
        for (let day = 1; day <= 31; day++) {
          const dayStr = String(day).padStart(2, '0')
          const kaisaiDate = `${yearMonth}${dayStr}`  // YYYYMMDD形式
          
          setBulkProgress(`${kaisaiDate}: レース一覧取得中...`)
          
          try {
            // ステップ1: その日のレースID一覧を取得（正しい方法）
            const raceListRes = await fetch('/api/netkeiba/race-list', {
              method: 'POST',
              headers: { 'Content-Type': 'application/json' },
              body: JSON.stringify({ date: `${currentYear}-${String(currentMonth).padStart(2, '0')}-${dayStr}` }),
            })
            
            if (!raceListRes.ok) {
              continue  // この日はレースなし
            }
            
            const raceListData = await raceListRes.json()
            
            if (!raceListData.raceIds || raceListData.raceIds.length === 0) {
              continue  // この日はレースなし
            }
            
            setBulkProgress(`${kaisaiDate}: ${raceListData.raceIds.length}レース発見`)
            
            // ステップ2: 取得したレースIDでデータを取得
            for (const raceId of raceListData.raceIds) {
              try {
                const scrapeRes = await fetch('/api/netkeiba/race', {
                  method: 'POST',
                  headers: { 'Content-Type': 'application/json' },
                  body: JSON.stringify({ 
                    raceId, 
                    userId: userId || '00000000-0000-0000-0000-000000000000'
                  }),
                })
                
                if (scrapeRes.ok) {
                  const scrapeData = await scrapeRes.json()
                  
                  if (scrapeData.success) {
                    successCount++
                    totalRacesScraped++
                    setBulkStats(prev => ({ ...prev, completedRaces: totalRacesScraped }))
                    setBulkProgress(`✅ [${totalRacesScraped}] ${raceId} 取得完了`)
                    
                    // 成功時のみ3秒待機
                    await new Promise(resolve => setTimeout(resolve, 3000))
                  }
                }
              } catch (error) {
                console.error(`レース取得エラー (${raceId}):`, error)
              }
            }
            
            // レース一覧取得後に少し待機
            await new Promise(resolve => setTimeout(resolve, 2000))
            
          } catch (error) {
            // レース一覧取得エラーは無視（その日はレースなし）
            continue
          }
        }
        
        // 月完了
        completedMonthsCount++
        setBulkStats(prev => ({ ...prev, completedMonths: completedMonthsCount }))
        setBulkProgress(`${currentYear}年${currentMonth}月: ${successCount}レース取得完了`)
        
        // 次の月へ
        currentMonth++
        if (currentMonth > 12) {
          currentMonth = 1
          currentYear++
        }
        
        // 月の間に少し待機
        await new Promise(resolve => setTimeout(resolve, 2000))
      }
      
      setBulkProgress(`✅ 完了！合計 ${totalRacesScraped} レースのデータを取得しました`)
      alert(`データ取得完了！\n\n合計 ${totalRacesScraped} レースを取得しました`)
      
      // データを再読み込み
      if (userId) {
        fetchCollectedData(userId)
      }
      
    } catch (error) {
      console.error('一括取得エラー:', error)
      alert('エラーが発生しました: ' + error)
    } finally {
      setLoading(false)
      setBulkMode(false)
    }
  }

  return (
    <AdminOnly>
      <div className="min-h-screen bg-gradient-to-br from-indigo-50 via-purple-50 to-pink-50">
        <header className="bg-white/80 backdrop-blur-md shadow-sm sticky top-0 z-50">
          <div className="container mx-auto px-4 py-4 flex justify-between items-center">
            <h1 className="text-2xl font-bold bg-gradient-to-r from-indigo-600 to-purple-600 bg-clip-text text-transparent">
              📥 データ取得 (管理者専用)
            </h1>
            <a href="/dashboard" className="text-indigo-600 hover:text-indigo-700 font-medium transition flex items-center">
              ← ダッシュボード
            </a>
          </div>
        </header>

      <main className="container mx-auto px-4 py-8">
        {/* 本番環境警告 */}
        {isProduction && (
          <div className="mb-6 bg-red-50 border-2 border-red-300 rounded-xl p-6">
            <div className="flex items-start gap-3">
              <span className="text-3xl">⚠️</span>
              <div>
                <h3 className="text-xl font-bold text-red-800 mb-2">本番環境では動作しません</h3>
                <p className="text-red-700 mb-3">
                  データ収集機能は<strong>ローカル環境専用</strong>です。Vercelのサーバーレス環境では、スクレイピングサービス（localhost:8001）にアクセスできません。
                </p>
                <div className="bg-white/70 p-4 rounded-lg">
                  <p className="font-semibold text-gray-800 mb-2">✅ 正しい使い方：</p>
                  <ol className="list-decimal list-inside text-gray-700 space-y-1 ml-2">
                    <li>開発環境（localhost:3000）でこのページにアクセス</li>
                    <li>スクレイピングサービス（localhost:8001）を起動</li>
                    <li>データ収集を実行</li>
                    <li>収集したデータはSupabaseに自動保存</li>
                    <li>本番環境では保存されたデータを閲覧・予測に使用</li>
                  </ol>
                </div>
              </div>
            </div>
          </div>
        )}
        
        {/* Ultimate版設定 */}
        <div className="bg-gradient-to-r from-purple-50 to-pink-50 p-6 rounded-2xl shadow-xl mb-6 border-2 border-purple-200">
          <h2 className="text-xl font-bold mb-4 text-gray-800 flex items-center">
            <span className="bg-gradient-to-r from-purple-600 to-pink-600 text-white rounded-full w-8 h-8 flex items-center justify-center mr-3 text-sm">⚡</span>
            Ultimate版設定
          </h2>
          
          <div className="space-y-4">
            {/* Ultimate版 ON/OFF */}
            <div className="flex items-start space-x-4 bg-white/70 p-4 rounded-lg">
              <input
                type="checkbox"
                id="ultimateMode"
                checked={ultimateMode}
                onChange={(e) => setUltimateMode(e.target.checked)}
                className="mt-1 w-5 h-5 text-purple-600 border-gray-300 rounded focus:ring-purple-500"
              />
              <div className="flex-1">
                <label htmlFor="ultimateMode" className="text-sm font-semibold text-gray-800 cursor-pointer flex items-center">
                  Ultimate版を使用する
                  <span className="ml-2 bg-purple-100 text-purple-700 text-xs px-2 py-1 rounded">推奨</span>
                </label>
                <p className="text-xs text-gray-600 mt-1">
                  horse_id, jockey_id, trainer_idを含む拡張データを取得します（スクレイピングAPI port 8001使用）
                </p>
              </div>
            </div>
            
            {/* 詳細情報取得（Ultimate版のみ） */}
            {ultimateMode && (
              <div className="flex items-start space-x-4 bg-white/70 p-4 rounded-lg border-l-4 border-purple-400">
                <input
                  type="checkbox"
                  id="includeDetails"
                  checked={includeDetails}
                  onChange={(e) => setIncludeDetails(e.target.checked)}
                  className="mt-1 w-5 h-5 text-pink-600 border-gray-300 rounded focus:ring-pink-500"
                />
                <div className="flex-1">
                  <label htmlFor="includeDetails" className="text-sm font-semibold text-gray-800 cursor-pointer flex items-center">
                    詳細情報を取得する（完全モード）
                    <span className="ml-2 bg-pink-100 text-pink-700 text-xs px-2 py-1 rounded">遅い</span>
                  </label>
                  <p className="text-xs text-gray-600 mt-1">
                    OFF: 高速モード（15-30秒、27列、ID含む） | ON: 完全モード（60-120秒、94列、全特徴量）
                  </p>
                  <p className="text-xs text-purple-600 mt-1 font-semibold">
                    ✨ 並列処理により3-5倍高速化済み！
                  </p>
                </div>
              </div>
            )}
            
            {/* 現在の設定表示 */}
            <div className="bg-gradient-to-r from-purple-100 to-pink-100 p-3 rounded-lg">
              <p className="text-sm font-semibold text-gray-700">
                📊 現在の設定: 
                <span className={`ml-2 ${ultimateMode ? 'text-purple-700' : 'text-gray-600'}`}>
                  {ultimateMode ? 'Ultimate版' : '標準版'} 
                </span>
                {ultimateMode && (
                  <span className={`ml-2 ${includeDetails ? 'text-pink-700' : 'text-green-700'}`}>
                    | {includeDetails ? '完全モード（94列）' : '高速モード（27列）'}
                  </span>
                )}
              </p>
              {ultimateMode && (
                <p className="text-xs text-gray-600 mt-1">
                  💡 学習用データ収集には高速モードで十分です。完全モードは予測時に使用してください。
                </p>
              )}
            </div>
          </div>
        </div>
        
        {/* 🚀 v2.0 バッチスクレイピング（Ultimate版のみ） */}
        {ultimateMode && (
          <div className="bg-gradient-to-r from-blue-50 to-cyan-50 p-6 rounded-2xl shadow-xl mb-6 border-2 border-blue-200">
            <h2 className="text-xl font-bold mb-4 text-gray-800 flex items-center">
              <span className="bg-gradient-to-r from-blue-600 to-cyan-600 text-white rounded-full w-8 h-8 flex items-center justify-center mr-3 text-sm">🚀</span>
              バッチスクレイピング（v2.0 - 超高速）
              <span className="ml-3 bg-gradient-to-r from-yellow-400 to-orange-400 text-white text-xs px-3 py-1 rounded-full font-bold shadow-lg">NEW</span>
            </h2>
            
            <div className="space-y-4">
              {/* 説明 */}
              <div className="bg-white/70 p-4 rounded-lg border-l-4 border-blue-400">
                <p className="text-sm font-semibold text-gray-800 mb-2">⚡ 複数レースを並列取得して超高速化！</p>
                <ul className="text-xs text-gray-600 space-y-1 list-disc list-inside">
                  <li>従来の逐次処理より<strong className="text-blue-600">最大6倍高速</strong></li>
                  <li>5レースを16秒で取得（従来: 75-150秒）</li>
                  <li>並列数2-3推奨（メモリ使用量とのバランス）</li>
                </ul>
              </div>
              
              {/* レースID入力 */}
              <div className="bg-white/70 p-4 rounded-lg">
                <label className="block text-sm font-semibold text-gray-700 mb-2">
                  レースID（複数指定可）
                </label>
                <textarea
                  value={batchRaceIds}
                  onChange={(e) => setBatchRaceIds(e.target.value)}
                  placeholder="202406010101&#10;202406010201&#10;202406010301&#10;（カンマまたは改行区切り）"
                  className="w-full px-4 py-3 border-2 border-gray-300 rounded-lg focus:border-blue-500 focus:outline-none font-mono text-sm"
                  rows={5}
                  disabled={batchLoading}
                />
                <p className="text-xs text-gray-500 mt-1">
                  💡 複数のレースIDをカンマ区切りまたは改行区切りで入力してください
                </p>
              </div>
              
              {/* 並列数設定 */}
              <div className="bg-white/70 p-4 rounded-lg">
                <label className="block text-sm font-semibold text-gray-700 mb-2">
                  並列数（max_workers）
                </label>
                <div className="flex items-center space-x-4">
                  <input
                    type="range"
                    min="1"
                    max="3"
                    value={batchMaxWorkers}
                    onChange={(e) => setBatchMaxWorkers(Number(e.target.value))}
                    className="flex-1"
                    disabled={batchLoading}
                  />
                  <span className="text-lg font-bold text-blue-600 w-16 text-center">
                    {batchMaxWorkers}
                  </span>
                </div>
                <div className="flex justify-between text-xs text-gray-600 mt-2">
                  <span>🐢 安全（1並列）</span>
                  <span>⚖️ バランス（2並列）推奨</span>
                  <span>🚀 最速（3並列）</span>
                </div>
                <p className="text-xs text-gray-500 mt-2">
                  ⚠️ 並列数が多いほど高速ですが、メモリ使用量も増加します（約1.5GB/並列）
                </p>
              </div>
              
              {/* 実行ボタン */}
              <button
                onClick={handleBatchScrape}
                disabled={batchLoading || !batchRaceIds.trim()}
                className={`w-full py-4 rounded-lg font-bold text-lg transition-all ${
                  batchLoading || !batchRaceIds.trim()
                    ? 'bg-gray-300 text-gray-500 cursor-not-allowed'
                    : 'bg-gradient-to-r from-blue-600 to-cyan-600 text-white hover:from-blue-700 hover:to-cyan-700 shadow-lg hover:shadow-xl'
                }`}
              >
                {batchLoading ? (
                  <span className="flex items-center justify-center">
                    <svg className="animate-spin h-5 w-5 mr-3" viewBox="0 0 24 24">
                      <circle className="opacity-25" cx="12" cy="12" r="10" stroke="currentColor" strokeWidth="4" fill="none"></circle>
                      <path className="opacity-75" fill="currentColor" d="M4 12a8 8 0 018-8V0C5.373 0 0 5.373 0 12h4zm2 5.291A7.962 7.962 0 014 12H0c0 3.042 1.135 5.824 3 7.938l3-2.647z"></path>
                    </svg>
                    バッチ取得中...
                  </span>
                ) : (
                  '🚀 バッチ取得開始'
                )}
              </button>
              
              {/* 結果表示 */}
              {batchResult && (
                <div className="bg-white p-4 rounded-lg border-2 border-blue-300">
                  <h3 className="font-bold text-gray-800 mb-3 flex items-center">
                    <span className="text-green-600 mr-2">✅</span>
                    バッチ取得完了
                  </h3>
                  <div className="grid grid-cols-2 gap-3 text-sm">
                    <div className="bg-blue-50 p-3 rounded">
                      <p className="text-gray-600 text-xs">成功</p>
                      <p className="text-2xl font-bold text-blue-600">
                        {batchResult.stats.success}/{batchResult.stats.total}
                      </p>
                    </div>
                    <div className="bg-green-50 p-3 rounded">
                      <p className="text-gray-600 text-xs">所要時間</p>
                      <p className="text-2xl font-bold text-green-600">
                        {batchResult.stats.elapsed_seconds}秒
                      </p>
                    </div>
                    <div className="bg-purple-50 p-3 rounded">
                      <p className="text-gray-600 text-xs">1レースあたり</p>
                      <p className="text-2xl font-bold text-purple-600">
                        {batchResult.stats.avg_seconds_per_race}秒
                      </p>
                    </div>
                    <div className="bg-orange-50 p-3 rounded">
                      <p className="text-gray-600 text-xs">高速化率</p>
                      <p className="text-2xl font-bold text-orange-600">
                        {batchResult.stats.speedup_vs_sequential}倍
                      </p>
                    </div>
                  </div>
                  
                  {batchResult.failed && batchResult.failed.length > 0 && (
                    <div className="mt-3 p-3 bg-red-50 rounded">
                      <p className="text-sm font-semibold text-red-700 mb-1">
                        ⚠️ 失敗: {batchResult.failed.length}レース
                      </p>
                      <ul className="text-xs text-red-600 space-y-1">
                        {batchResult.failed.slice(0, 5).map((fail: any, idx: number) => (
                          <li key={idx}>• {fail.race_id}: {fail.error}</li>
                        ))}
                      </ul>
                    </div>
                  )}
                </div>
              )}
            </div>
          </div>
        )}
        
        {/* 期間指定一括取得 */}
        <div className="bg-gradient-to-r from-green-50 to-emerald-50 p-8 rounded-2xl shadow-xl mb-6 border-2 border-green-200">
          <h2 className="text-2xl font-bold mb-6 text-gray-800 flex items-center">
            <span className="bg-gradient-to-r from-green-600 to-emerald-600 text-white rounded-full w-10 h-10 flex items-center justify-center mr-3 font-extrabold">🎯</span>
            期間指定で一括取得（学習用データ収集）
          </h2>
          
          <div className="mb-6 bg-white/70 p-4 rounded-lg border border-green-300">
            <p className="text-sm text-gray-700 mb-2">
              💡 <strong>学習用データの効率的な収集方法</strong>
            </p>
            <p className="text-sm text-gray-600">
              開始年月から終了年月までの全レースデータを自動的に取得します。<br />
              大量のデータ取得には時間がかかりますが、モデル学習に必要な十分なデータを確保できます。
            </p>
          </div>

          <div className="grid grid-cols-2 md:grid-cols-4 gap-4 mb-6">
            <div>
              <label className="block text-sm font-semibold text-gray-700 mb-2">開始年</label>
              <input
                type="number"
                value={startYear}
                onChange={(e) => setStartYear(parseInt(e.target.value))}
                min={2000}
                max={new Date().getFullYear()}
                className="w-full border-2 border-gray-200 rounded-lg px-4 py-3 focus:border-green-500 focus:ring focus:ring-green-200 transition text-gray-900 font-medium"
              />
            </div>
            
            <div>
              <label className="block text-sm font-semibold text-gray-700 mb-2">開始月</label>
              <select
                value={startMonth}
                onChange={(e) => setStartMonth(parseInt(e.target.value))}
                className="w-full border-2 border-gray-200 rounded-lg px-4 py-3 focus:border-green-500 focus:ring focus:ring-green-200 transition text-gray-900 font-medium"
              >
                {Array.from({ length: 12 }, (_, i) => i + 1).map(m => (
                  <option key={m} value={m}>{m}月</option>
                ))}
              </select>
            </div>
            
            <div>
              <label className="block text-sm font-semibold text-gray-700 mb-2">終了年</label>
              <input
                type="number"
                value={endYear}
                onChange={(e) => setEndYear(parseInt(e.target.value))}
                min={2000}
                max={new Date().getFullYear()}
                className="w-full border-2 border-gray-200 rounded-lg px-4 py-3 focus:border-green-500 focus:ring focus:ring-green-200 transition text-gray-900 font-medium"
              />
            </div>
            
            <div>
              <label className="block text-sm font-semibold text-gray-700 mb-2">終了月</label>
              <select
                value={endMonth}
                onChange={(e) => setEndMonth(parseInt(e.target.value))}
                className="w-full border-2 border-gray-200 rounded-lg px-4 py-3 focus:border-green-500 focus:ring focus:ring-green-200 transition text-gray-900 font-medium"
              >
                {Array.from({ length: 12 }, (_, i) => i + 1).map(m => (
                  <option key={m} value={m}>{m}月</option>
                ))}
              </select>
            </div>
          </div>

          <div className="mb-6 bg-yellow-50 p-4 rounded-lg border border-yellow-300">
            <p className="text-sm text-gray-700">
              📊 <strong>取得期間:</strong> {startYear}年{startMonth}月 ～ {endYear}年{endMonth}月
            </p>
            <p className="text-sm text-gray-600 mt-2">
              ⚠️ 期間が長いほど取得時間が長くなります（1ヶ月あたり約20-50レース、1レース3秒）
            </p>
          </div>

          {bulkMode && bulkProgress && (
            <div className="mb-6 bg-white p-6 rounded-lg border-2 border-green-400 animate-fade-in">
              <div className="flex items-center mb-4">
                <div className="animate-spin mr-3 text-2xl">🔄</div>
                <h3 className="font-bold text-lg text-gray-800">取得中...</h3>
              </div>

              {/* 月別進捗バー */}
              <div className="mb-4">
                <div className="flex justify-between text-sm mb-2 font-semibold text-gray-700">
                  <span>月別進捗</span>
                  <span className="text-green-600">{bulkStats.completedMonths} / {bulkStats.totalMonths} ヶ月</span>
                </div>
                <div className="w-full bg-gray-200 rounded-full h-6 overflow-hidden">
                  <div
                    className="bg-gradient-to-r from-green-500 via-emerald-500 to-teal-500 h-6 rounded-full transition-all duration-500 flex items-center justify-center text-white text-xs font-bold"
                    style={{ width: `${bulkStats.totalMonths > 0 ? (bulkStats.completedMonths / bulkStats.totalMonths) * 100 : 0}%` }}
                  >
                    {bulkStats.totalMonths > 0 ? Math.round((bulkStats.completedMonths / bulkStats.totalMonths) * 100) : 0}%
                  </div>
                </div>
              </div>

              {/* レース取得数 */}
              <div className="mb-4 bg-gradient-to-r from-green-50 to-emerald-50 p-4 rounded-lg border border-green-200">
                <div className="flex justify-between items-center">
                  <span className="text-sm font-semibold text-gray-700">取得完了レース数</span>
                  <span className="text-3xl font-extrabold bg-gradient-to-r from-green-600 to-emerald-600 bg-clip-text text-transparent">
                    {bulkStats.completedRaces}
                  </span>
                </div>
              </div>

              {/* 詳細ログ */}
              <div className="bg-gray-900 p-4 rounded-lg font-mono text-sm text-green-400 max-h-40 overflow-y-auto">
                {bulkProgress}
              </div>
            </div>
          )}

          {/* 並列数設定（Ultimate版のみ） */}
          {ultimateMode && (
            <div className="mb-6 bg-white/70 p-4 rounded-lg border border-green-300">
              <label className="block text-sm font-semibold text-gray-700 mb-2">
                並列数（max_workers）
              </label>
              <div className="flex items-center space-x-4">
                <input
                  type="range"
                  min="1"
                  max="3"
                  value={batchMaxWorkers}
                  onChange={(e) => setBatchMaxWorkers(Number(e.target.value))}
                  className="flex-1"
                  disabled={batchLoading || loading}
                />
                <span className="text-lg font-bold text-green-600 w-16 text-center">
                  {batchMaxWorkers}
                </span>
              </div>
              <div className="flex justify-between text-xs text-gray-600 mt-2">
                <span>🐢 安全（1並列）</span>
                <span>⚖️ バランス（2並列）推奨</span>
                <span>🚀 最速（3並列）</span>
              </div>
              <p className="text-xs text-gray-500 mt-2">
                ⚠️ 並列数が多いほど高速ですが、メモリ使用量も増加します（約1.5GB/並列）
              </p>
            </div>
          )}

          {/* 実行ボタン - Ultimate版は2つのオプション */}
          {ultimateMode ? (
            <div className="space-y-4">
              <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
                <button
                  onClick={handlePeriodBatchScrape}
                  disabled={authLoading || batchLoading}
                  className={`py-5 rounded-2xl text-xl font-bold transition-all duration-300 ${
                    authLoading || batchLoading
                      ? 'bg-gray-300 text-gray-500 cursor-not-allowed'
                      : 'bg-gradient-to-r from-blue-600 to-cyan-600 text-white hover:shadow-2xl hover:scale-105'
                  }`}
                >
                  {batchLoading ? '🔄 取得中...' : '🚀 並列バッチ取得（高速）'}
                </button>
                
                <button
                  onClick={bulkScrapeByPeriod}
                  disabled={authLoading || loading}
                  className={`py-5 rounded-2xl text-xl font-bold transition-all duration-300 ${
                    authLoading || loading
                      ? 'bg-gray-300 text-gray-500 cursor-not-allowed'
                      : 'bg-gradient-to-r from-green-600 to-emerald-600 text-white hover:shadow-2xl hover:scale-105'
                  }`}
                >
                  {loading ? '🔄 取得中...' : '📅 逐次取得（安全）'}
                </button>
              </div>
              
              {/* 🆕 進捗バー */}
              {batchLoading && (
                <div className="bg-gradient-to-r from-blue-50 to-cyan-50 p-6 rounded-xl border-2 border-blue-200">
                  <div className="mb-3">
                    <div className="flex justify-between items-center mb-2">
                      <span className="text-sm font-semibold text-blue-800">
                        {batchProgress.message}
                      </span>
                      <span className="text-sm font-bold text-blue-600">
                        {batchProgress.current}%
                      </span>
                    </div>
                    <div className="w-full bg-blue-200 rounded-full h-4 overflow-hidden shadow-inner">
                      <div
                        className="bg-gradient-to-r from-blue-500 to-cyan-500 h-4 rounded-full transition-all duration-500 ease-out flex items-center justify-center"
                        style={{ width: `${batchProgress.current}%` }}
                      >
                        <span className="text-xs font-bold text-white drop-shadow">
                          {batchProgress.current}%
                        </span>
                      </div>
                    </div>
                  </div>
                </div>
              )}
            </div>
          ) : (
            <button
              onClick={bulkScrapeByPeriod}
              disabled={authLoading || loading}
              className="w-full bg-gradient-to-r from-green-600 to-emerald-600 text-white px-8 py-5 rounded-2xl text-xl font-bold hover:shadow-2xl hover:scale-105 disabled:opacity-50 disabled:cursor-not-allowed transition-all duration-300"
            >
              {authLoading ? '認証確認中...' : loading ? '🔄 一括取得中... しばらくお待ちください' : '🚀 期間指定で一括取得開始'}
            </button>
          )}
          
          <div className="mt-4 grid grid-cols-1 md:grid-cols-2 gap-4 text-xs text-gray-600">
            {ultimateMode && (
              <>
                <div className="bg-blue-50 p-3 rounded-lg border border-blue-200">
                  <p className="font-semibold text-blue-800 mb-1">🚀 並列バッチ取得（推奨）</p>
                  <ul className="space-y-1 text-blue-700">
                    <li>• 最大3並列で超高速取得</li>
                    <li>• 100レース約5-10分</li>
                    <li>• 期間内の全レースを自動取得</li>
                  </ul>
                </div>
                <div className="bg-green-50 p-3 rounded-lg border border-green-200">
                  <p className="font-semibold text-green-800 mb-1">📅 逐次取得（安全）</p>
                  <ul className="space-y-1 text-green-700">
                    <li>• 1レースずつ順次取得</li>
                    <li>• サーバー負荷最小</li>
                    <li>• エラー時も続行可能</li>
                  </ul>
                </div>
              </>
            )}
          </div>
        </div>

        {/* バッチ結果表示（期間指定でも使用） */}
        {batchResult && batchResult.stats && batchResult.stats.period && (
          <div className="bg-gradient-to-r from-purple-50 to-pink-50 p-8 rounded-2xl shadow-xl mb-6 border-2 border-purple-200">
            <h3 className="text-2xl font-bold text-gray-800 mb-4 flex items-center">
              <span className="text-green-600 mr-2">✅</span>
              期間バッチ取得完了
            </h3>
            <div className="grid grid-cols-2 md:grid-cols-3 gap-4">
              <div className="bg-white p-4 rounded-lg shadow">
                <p className="text-gray-600 text-sm mb-1">期間</p>
                <p className="text-lg font-bold text-purple-600">{batchResult.stats.period}</p>
              </div>
              <div className="bg-white p-4 rounded-lg shadow">
                <p className="text-gray-600 text-sm mb-1">対象日数</p>
                <p className="text-lg font-bold text-blue-600">{batchResult.stats.days}日</p>
              </div>
              <div className="bg-white p-4 rounded-lg shadow">
                <p className="text-gray-600 text-sm mb-1">取得レース数</p>
                <p className="text-lg font-bold text-green-600">{batchResult.stats.success}/{batchResult.stats.total_races}</p>
              </div>
              <div className="bg-white p-4 rounded-lg shadow">
                <p className="text-gray-600 text-sm mb-1">所要時間</p>
                <p className="text-lg font-bold text-orange-600">{batchResult.stats.elapsed_seconds}秒</p>
              </div>
              <div className="bg-white p-4 rounded-lg shadow">
                <p className="text-gray-600 text-sm mb-1">1レースあたり</p>
                <p className="text-lg font-bold text-pink-600">{batchResult.stats.avg_seconds_per_race}秒</p>
              </div>
              <div className="bg-white p-4 rounded-lg shadow">
                <p className="text-gray-600 text-sm mb-1">高速化率</p>
                <p className="text-lg font-bold text-red-600">{batchResult.stats.speedup_vs_sequential}倍</p>
              </div>
            </div>
          </div>
        )}

        {/* 取得済みデータ確認 */}
        <div className="bg-white/80 backdrop-blur-sm p-8 rounded-2xl shadow-xl border border-blue-100">
          <div className="flex justify-between items-center mb-6">
            <h2 className="text-2xl font-bold text-gray-800 flex items-center">
              <span className="bg-gradient-to-r from-blue-600 to-cyan-600 text-white rounded-full w-10 h-10 flex items-center justify-center mr-3 font-extrabold">📊</span>
              取得済みデータ
            </h2>
            <button
              onClick={() => setShowCollectedData(!showCollectedData)}
              className="bg-gradient-to-r from-blue-600 to-cyan-600 text-white px-6 py-3 rounded-xl font-bold hover:shadow-lg hover:scale-105 transition-all duration-300"
            >
              {showCollectedData ? '📁 閉じる' : '📂 データを表示'}
            </button>
          </div>

          {/* 統計情報 */}
          <div className="grid grid-cols-1 md:grid-cols-3 gap-4 mb-6">
            <div className="bg-gradient-to-br from-blue-50 to-cyan-50 p-6 rounded-xl border-2 border-blue-200">
              <div className="text-sm text-gray-600 mb-2">総レース数</div>
              <div className="text-4xl font-extrabold bg-gradient-to-r from-blue-600 to-cyan-600 bg-clip-text text-transparent">
                {dataStats.totalRaces}
              </div>
            </div>
            <div className="bg-gradient-to-br from-purple-50 to-pink-50 p-6 rounded-xl border-2 border-purple-200">
              <div className="text-sm text-gray-600 mb-2">総出走馬数</div>
              <div className="text-4xl font-extrabold bg-gradient-to-r from-purple-600 to-pink-600 bg-clip-text text-transparent">
                {dataStats.totalResults}
              </div>
            </div>
            <div className="bg-gradient-to-br from-green-50 to-emerald-50 p-6 rounded-xl border-2 border-green-200">
              <div className="text-sm text-gray-600 mb-2">最終取得日時</div>
              <div className="text-lg font-bold text-gray-700">
                {dataStats.latestDate ? new Date(dataStats.latestDate).toLocaleString('ja-JP') : '未取得'}
              </div>
            </div>
          </div>

          {/* レース一覧 */}
          {showCollectedData && (
            <div className="animate-fade-in">
              <div className="flex justify-between items-center mb-4">
                <h3 className="font-bold text-lg text-gray-800">
                  最近取得したレース <span className="text-blue-600">(最新50件)</span>
                </h3>
                <button
                  onClick={() => userId && fetchCollectedData(userId)}
                  className="text-sm bg-blue-100 text-blue-700 px-4 py-2 rounded-lg font-semibold hover:bg-blue-200 transition"
                >
                  🔄 更新
                </button>
              </div>

              <div className="space-y-3 max-h-[600px] overflow-y-auto">
                {collectedRaces.map(race => (
                  <div
                    key={race.race_id}
                    className="bg-white p-5 border-2 border-gray-200 rounded-xl hover:border-blue-300 hover:shadow-md transition-all duration-300"
                  >
                    <div className="flex justify-between items-start mb-3">
                      <div className="flex-1">
                        <div className="font-bold text-gray-800 text-xl mb-2">
                          {race.race_name}
                        </div>
                        <div className="flex flex-wrap gap-3 text-sm text-gray-600">
                          <span className="bg-blue-100 px-3 py-1 rounded-full">🏟️ {race.venue}</span>
                          <span className="bg-green-100 px-3 py-1 rounded-full">📏 {race.distance}m</span>
                          <span className="bg-purple-100 px-3 py-1 rounded-full">🌱 {race.track_type}</span>
                          <span className="bg-yellow-100 px-3 py-1 rounded-full">☀️ {race.weather}</span>
                          <span className="bg-orange-100 px-3 py-1 rounded-full">🏇 {race.field_condition}</span>
                        </div>
                      </div>
                      <button
                        onClick={() => fetchRaceDetail(race.race_id)}
                        className="ml-4 bg-gradient-to-r from-blue-500 to-cyan-500 text-white px-4 py-2 rounded-lg font-semibold hover:shadow-lg transition-all duration-300"
                      >
                        📋 詳細
                      </button>
                    </div>
                    <div className="text-xs text-gray-500 mt-2">
                      🆔 {race.race_id} | 📅 {new Date(race.created_at).toLocaleString('ja-JP')}
                    </div>
                  </div>
                ))}

                {collectedRaces.length === 0 && (
                  <div className="text-center py-12 text-gray-500">
                    <div className="text-6xl mb-4">📭</div>
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
            <div className="bg-white rounded-2xl shadow-2xl max-w-5xl w-full max-h-[90vh] overflow-hidden" onClick={(e) => e.stopPropagation()}>
              <div className="bg-gradient-to-r from-blue-600 to-cyan-600 text-white p-6">
                <div className="flex justify-between items-center">
                  <h3 className="text-2xl font-bold">レース詳細</h3>
                  <button
                    onClick={() => setSelectedRaceDetail(null)}
                    className="text-white hover:bg-white/20 rounded-full w-10 h-10 flex items-center justify-center font-bold text-2xl transition"
                  >
                    ×
                  </button>
                </div>
                <div className="text-sm mt-2 opacity-90">🆔 {selectedRaceDetail.raceId}</div>
              </div>

              <div className="p-6 overflow-y-auto max-h-[calc(90vh-120px)]">
                <div className="mb-4 text-lg font-semibold text-gray-700">
                  出走馬一覧 <span className="text-blue-600">({selectedRaceDetail.results.length}頭)</span>
                </div>

                <div className="overflow-x-auto">
                  <table className="w-full text-sm">
                    <thead className="bg-gray-100 sticky top-0">
                      <tr>
                        <th className="px-3 py-3 text-left font-bold text-gray-700">着順</th>
                        <th className="px-3 py-3 text-left font-bold text-gray-700">枠</th>
                        <th className="px-3 py-3 text-left font-bold text-gray-700">馬番</th>
                        <th className="px-3 py-3 text-left font-bold text-gray-700">馬名</th>
                        <th className="px-3 py-3 text-left font-bold text-gray-700">性齢</th>
                        <th className="px-3 py-3 text-left font-bold text-gray-700">斤量</th>
                        <th className="px-3 py-3 text-left font-bold text-gray-700">騎手</th>
                        <th className="px-3 py-3 text-left font-bold text-gray-700">タイム</th>
                        <th className="px-3 py-3 text-left font-bold text-gray-700">オッズ</th>
                        <th className="px-3 py-3 text-left font-bold text-gray-700">人気</th>
                      </tr>
                    </thead>
                    <tbody>
                      {selectedRaceDetail.results.map((result: any, index: number) => (
                        <tr key={index} className={`border-b hover:bg-blue-50 transition ${
                          result.finish_position === 1 ? 'bg-yellow-50' :
                          result.finish_position === 2 ? 'bg-gray-50' :
                          result.finish_position === 3 ? 'bg-orange-50' : ''
                        }`}>
                          <td className="px-3 py-3 font-bold text-gray-800">
                            {result.finish_position <= 3 && result.finish_position === 1 && '🥇'}
                            {result.finish_position <= 3 && result.finish_position === 2 && '🥈'}
                            {result.finish_position <= 3 && result.finish_position === 3 && '🥉'}
                            {result.finish_position}
                          </td>
                          <td className="px-3 py-3">{result.bracket_number}</td>
                          <td className="px-3 py-3 font-semibold">{result.horse_number}</td>
                          <td className="px-3 py-3 font-semibold text-gray-800">{result.horse_name}</td>
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
      </main>
    </div>
    </AdminOnly>
  )
}
