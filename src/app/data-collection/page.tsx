'use client'

import { useState, useEffect } from 'react'
import { supabase } from '@/lib/supabase'
import { useRouter } from 'next/navigation'
import { useUltimateMode } from '@/contexts/UltimateModeContext'

export default function DataCollectionPage() {
  const router = useRouter()
  const [loading, setLoading] = useState(false)
  const [userId, setUserId] = useState<string | null>(null)
  const { ultimateMode, setUltimateMode, includeDetails, setIncludeDetails } = useUltimateMode()

  // 単一レースID取得
  const [raceId, setRaceId] = useState('')
  const [scrapeResult, setScrapeResult] = useState<any>(null)

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
      if (!supabase) {
        console.error('Supabase client not initialized')
        return
      }
      const { data: { user } } = await supabase.auth.getUser()
      if (!user) {
        router.push('/auth/login')
        return
      }
      setUserId(user.id)
      loadStats()
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
        .from('collected_races')
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

  // 期間指定で一括取得
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
        setBulkProgress(`${currentYear}年${currentMonth}月 の開催日を取得中...`)
        
        // 1. 開催日取得
        const calendarRes = await fetch(`/api/netkeiba/calendar?year=${currentYear}&month=${currentMonth}`)
        const calendarData = await calendarRes.json()
        
        if (calendarData.error) {
          console.error(`${currentYear}/${currentMonth} の開催日取得失敗:`, calendarData.error)
        } else {
          const datesInMonth = calendarData.dates || []
          
          if (datesInMonth.length > 0) {
            setBulkProgress(`${currentYear}年${currentMonth}月: ${datesInMonth.length}日の開催日を発見`)
            
            // 2. 各開催日について、race_list.htmlから実際のrace_idを取得
            for (const date of datesInMonth) {
              setBulkProgress(`${date} のレース一覧を取得中...`)
              
              // race_list.htmlからその日のrace_id一覧を取得
              try {
                const raceListRes = await fetch('/api/netkeiba/race-list', {
                  method: 'POST',
                  headers: { 'Content-Type': 'application/json' },
                  body: JSON.stringify({ date }),
                })
                
                if (!raceListRes.ok) {
                  setBulkProgress(`${date}: レース一覧取得失敗`)
                  continue
                }
                
                const raceListData = await raceListRes.json()
                
                if (!raceListData.raceIds || raceListData.raceIds.length === 0) {
                  setBulkProgress(`${date}: 開催なし`)
                  continue
                }
                
                const raceIds = raceListData.raceIds
                setBulkProgress(`${date}: ${raceIds.length}レース発見`)
                
                // 各race_idについてデータを取得
                for (let i = 0; i < raceIds.length; i++) {
                  const raceId = raceIds[i]
                  
                  try {
                    const scrapeRes = await fetch('/api/netkeiba/race', {
                      method: 'POST',
                      headers: { 'Content-Type': 'application/json' },
                      body: JSON.stringify({ raceId, userId }),
                    })
                    const scrapeData = await scrapeRes.json()
                    
                    if (scrapeData.success) {
                      totalRacesScraped++
                      setBulkStats(prev => ({ ...prev, completedRaces: totalRacesScraped }))
                      setBulkProgress(`✅ [${totalRacesScraped}件目] ${date} ${raceId} 取得完了`)
                      
                      // 成功時のみ3秒待機
                      await new Promise(resolve => setTimeout(resolve, 3000))
                    } else {
                      setBulkProgress(`⚠ ${date} ${raceId}: ${scrapeData.error || '取得失敗'}`)
                    }
                  } catch (error) {
                    console.error(`レース ${raceId} エラー:`, error)
                  }
                }
                
                setBulkProgress(`${date}: ${raceIds.length}レース処理完了`)
                
              } catch (error) {
                console.error(`${date} レース一覧取得エラー:`, error)
                setBulkProgress(`${date}: エラー`)
              }
            }
          } else {
            setBulkProgress(`${currentYear}年${currentMonth}月: 開催日なし`)
          }
        }
        
        // 月完了
        completedMonthsCount++
        setBulkStats(prev => ({ ...prev, completedMonths: completedMonthsCount }))
        
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
    <div className="min-h-screen bg-gradient-to-br from-indigo-50 via-purple-50 to-pink-50">
      <header className="bg-white/80 backdrop-blur-md shadow-sm sticky top-0 z-50">
        <div className="container mx-auto px-4 py-4 flex justify-between items-center">
          <h1 className="text-2xl font-bold bg-gradient-to-r from-indigo-600 to-purple-600 bg-clip-text text-transparent">
            📥 データ取得
          </h1>
          <a href="/dashboard" className="text-indigo-600 hover:text-indigo-700 font-medium transition flex items-center">
            ← ダッシュボード
          </a>
        </div>
      </header>

      <main className="container mx-auto px-4 py-8">
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

          <button
            onClick={bulkScrapeByPeriod}
            disabled={loading}
            className="w-full bg-gradient-to-r from-green-600 to-emerald-600 text-white px-8 py-5 rounded-2xl text-xl font-bold hover:shadow-2xl hover:scale-105 disabled:opacity-50 disabled:cursor-not-allowed transition-all duration-300"
          >
            {loading ? '🔄 一括取得中... しばらくお待ちください' : '🚀 期間指定で一括取得開始'}
          </button>
        </div>



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
  )
}
