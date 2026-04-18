'use client'

import { useState, useEffect } from 'react'
import Link from 'next/link'
import { Logo } from '@/components/Logo'
import { Toast } from '@/components/Toast'
import { useJobPoller } from '@/hooks/useJobPoller'
import { useBatchScrape } from '@/hooks/useBatchScrape'

export default function DataCollectionPage() {
  // 期間指定用
  const now = new Date()
  const [startPeriod, setStartPeriod] = useState(`${now.getFullYear() - 1}-01`)
  const [endPeriod, setEndPeriod] = useState(
    `${now.getFullYear()}-${String(now.getMonth() + 1).padStart(2, '0')}`
  )
  const [forceRescrape, setForceRescrape] = useState(false)
  const [toast, setToast] = useState({ visible: false, message: '', type: 'success' as 'success' | 'error' })
  const showToast = (message: string, type: 'success' | 'error' = 'success') =>
    setToast({ visible: true, message, type })

  // バッチスクレイピング（月単位ループ + ポーリングをフックが担当）
  const { loading: batchLoading, progress: batchProgress, result: batchResult, start: startBatchScrape } = useBatchScrape()

  // データ統計と表示
  const [dataStats, setDataStats] = useState({ totalRaces: 0, totalResults: 0, latestDate: '' })
  const [showCollectedData, setShowCollectedData] = useState(false)
  const [collectedRaces, setCollectedRaces] = useState<any[]>([])
  const [selectedRaceDetail, setSelectedRaceDetail] = useState<any>(null)

  // プロファイリング
  const [showProfiling, setShowProfiling] = useState(false)
  const [profilingJobId, setProfilingJobId] = useState<string | null>(null)
  const [useOptimized, setUseOptimized] = useState(true)

  // useJobPoller でプロファイリングのポーリングを管理
  const { status: profilingStatus, progress: profilingMessage } = useJobPoller({
    jobId: profilingJobId,
    getStatusUrl: id => `/api/profiling/status/${id}`,
    intervalMs: 5000,
  })

  const inferProgress = (msg: string): number => {
    if (msg.includes('読み込み')) return 15
    if (msg.includes('エンジニアリング')) return 35
    if (msg.includes('最適化')) return 55
    if (msg.includes('ydata-profiling') || msg.includes('生成中')) return 75
    if (msg.includes('完了')) return 100
    return 5
  }
  const profilingProgress =
    profilingStatus === 'completed' ? 100
    : profilingStatus === 'error' ? 0
    : profilingMessage ? inferProgress(profilingMessage) : 0

  // ローカルAPI稼働チェック
  const [localApiStatus, setLocalApiStatus] = useState<'checking' | 'online' | 'offline'>('checking')

  const checkLocalApi = async () => {
    setLocalApiStatus('checking')
    try {
      const res = await fetch('/api/scrape/status/__health_check__', { method: 'GET', signal: AbortSignal.timeout(3000) })
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
      const res = await fetch('/api/data-stats?ultimate=true')
      if (!res.ok) return
      const stats = await res.json()
      setDataStats({
        totalRaces: stats.total_races || 0,
        totalResults: stats.total_horses || 0,
        latestDate: stats.latest_date || ''
      })
    } catch (error) {
      console.error('統計取得エラー:', error)
    }
  }

  const fetchCollectedData = async () => {
    try {
      const res = await fetch('/api/races/recent?limit=50')
      if (!res.ok) return
      const data = await res.json()
      setCollectedRaces(data.races || [])
    } catch (error) {
      console.error('データ取得エラー:', error)
    }
  }

  const fetchRaceDetail = async (raceId: string) => {
    try {
      const res = await fetch(`/api/races/${raceId}/horses`)
      if (!res.ok) {
        const err = await res.json().catch(() => ({}))
        throw new Error(err.error || `HTTP ${res.status}`)
      }
      const data = await res.json()
      setSelectedRaceDetail({ raceId, results: data.horses || [] })
    } catch (error) {
      console.error('レース詳細取得エラー:', error)
    }
  }

  // 🚀 期間指定バッチスクレイピング（バリデーション・確認ダイアログのみ担当）
  const handlePeriodBatchScrape = async () => {
    const [startYearStr, startMonthStr] = startPeriod.split('-')
    const [endYearStr, endMonthStr] = endPeriod.split('-')
    const startYear = parseInt(startYearStr, 10)
    const startMonth = parseInt(startMonthStr, 10)
    const endYear = parseInt(endYearStr, 10)
    const endMonth = parseInt(endMonthStr, 10)

    if (new Date(startYear, startMonth - 1) > new Date(endYear, endMonth - 1)) {
      alert('開始年月が終了年月より後になっています')
      return
    }

    // 月数カウント（確認ダイアログ用）
    let totalMonths = 0
    let y = startYear, m = startMonth
    while (y < endYear || (y === endYear && m <= endMonth)) {
      totalMonths++; m++; if (m > 12) { m = 1; y++ }
    }

    if (!confirm(`${startYear}年${startMonth}月 ～ ${endYear}年${endMonth}月（${totalMonths}ヶ月分）を月単位で順次取得します。\n中断するにはページをリロードしてください。\n\n続行しますか？`)) return

    try {
      const result = await startBatchScrape(startPeriod, endPeriod, forceRescrape)
      showToast(`取得完了 — ${result.stats.total_months}ヶ月 / ${result.races_collected}レース / 所要: ${result.elapsed_time}秒`)
      loadStats()
    } catch (error: any) {
      showToast(`取得エラー: ${error.message}`, 'error')
    }
  }

  const handleStartProfiling = async () => {
    setProfilingJobId(null)
    try {
      const res = await fetch('/api/profiling', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ use_optimized: useOptimized }),
      })
      if (!res.ok) throw new Error(`HTTP ${res.status}`)
      const { job_id } = await res.json()
      setProfilingJobId(job_id)
    } catch (e: any) {
      showToast(e.message, 'error')
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
          {/* コンパクトAPI状態 */}
          <div className="flex items-center gap-2 px-3 py-1.5 bg-[#111] border border-[#1e1e1e] rounded-full">
            {localApiStatus === 'checking' && <span className="w-1.5 h-1.5 rounded-full bg-[#555] animate-pulse" />}
            {localApiStatus === 'online'   && <span className="w-1.5 h-1.5 rounded-full bg-[#4ade80]" />}
            {localApiStatus === 'offline'  && <span className="w-1.5 h-1.5 rounded-full bg-[#f87171]" />}
            <span className={`text-xs font-medium ${localApiStatus === 'online' ? 'text-[#4ade80]' : localApiStatus === 'offline' ? 'text-[#f87171]' : 'text-[#555]'}`}>
              ローカルAPI {localApiStatus === 'online' ? '起動中' : localApiStatus === 'offline' ? '停止中' : '確認中'}
            </span>
            <button onClick={checkLocalApi} className="text-[#444] hover:text-[#888] transition-colors ml-1">
              <svg className="w-3 h-3" fill="none" viewBox="0 0 24 24" stroke="currentColor">
                <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M4 4v5h.582m15.356 2A8.001 8.001 0 004.582 9m0 0H9m11 11v-5h-.581m0 0a8.003 8.003 0 01-15.357-2m15.357 2H15" />
              </svg>
            </button>
          </div>
          <span className="text-sm text-[#888]">データ取得</span>
        </div>
      </header>

      <main className="max-w-3xl mx-auto px-6 py-10 space-y-5">
        {/* API停止中の案内 */}
        {localApiStatus === 'offline' && (
          <div className="bg-[#111] border border-[#332200] rounded-lg px-4 py-3 flex items-center gap-3">
            <span className="w-1.5 h-1.5 rounded-full bg-[#f87171] shrink-0" />
            <div className="flex-1 min-w-0">
              <p className="text-xs text-[#f87171]">ローカルFastAPIが停止しています</p>
              <p className="text-xs text-[#555] mt-0.5">VS Code タスク「Start FastAPI」を実行するか、<code className="text-[#7dd3fc] font-mono">cd python-api; python main.py</code> を実行してください</p>
            </div>
          </div>
        )}

        {/* データ取得フォーム */}
        <div className="bg-[#111] border border-[#1e1e1e] rounded-lg p-6 space-y-4">
          <div className="flex items-center justify-between">
            <h2 className="text-sm font-medium text-white">期間指定一括取得</h2>
            <p className="text-xs text-[#555]">月単位で自動分割して順次取得</p>
          </div>

          <div className="grid grid-cols-2 gap-4">
            <div>
              <label className="block text-xs text-[#666] mb-2">開始年月</label>
              <input
                type="month"
                value={startPeriod}
                onChange={e => setStartPeriod(e.target.value)}
                max={endPeriod}
                className="w-full px-4 py-3 bg-[#0a0a0a] border border-[#1e1e1e] rounded-lg text-white focus:outline-none focus:border-[#333] transition-colors"
              />
            </div>
            <div>
              <label className="block text-xs text-[#666] mb-2">終了年月</label>
              <input
                type="month"
                value={endPeriod}
                onChange={e => setEndPeriod(e.target.value)}
                min={startPeriod}
                className="w-full px-4 py-3 bg-[#0a0a0a] border border-[#1e1e1e] rounded-lg text-white focus:outline-none focus:border-[#333] transition-colors"
              />
            </div>
          </div>

          {/* 期間情報 */}
          {(() => {
            const [sy, sm] = startPeriod.split('-').map(Number)
            const [ey, em] = endPeriod.split('-').map(Number)
            if (!sy || !ey) return null
            const months = (ey - sy) * 12 + (em - sm) + 1
            if (months <= 2) return null
            const days = Math.ceil((new Date(ey, em, 0).getTime() - new Date(sy, sm - 1, 1).getTime()) / 86400000)
            return (
              <div className={`text-xs px-3 py-2 rounded border ${days > 90 ? 'text-yellow-400 bg-[#1a1500] border-[#5a4200]' : 'text-[#60a5fa] bg-[#001433] border-[#1e3a5f]'}`}>
                {days > 90 ? '⚠ ' : ''}{days}日分（{months}ヶ月）を月単位で順次取得します。
                {days > 90 ? '  長期間取得はIPブロック対策のため段階的に実行されます。' : ''}
              </div>
            )
          })()}

          <div className="flex items-center justify-between pt-1">
            <label className="flex items-center gap-2 cursor-pointer select-none">
              <input
                type="checkbox"
                checked={forceRescrape}
                onChange={e => setForceRescrape(e.target.checked)}
                className="w-3.5 h-3.5 accent-white"
              />
              <span className="text-xs text-[#888]">強制再取得（取得済みを上書き）</span>
            </label>

            <button
              onClick={handlePeriodBatchScrape}
              disabled={batchLoading || localApiStatus === 'offline'}
              className={`flex items-center gap-2 px-6 py-2.5 rounded-lg font-medium text-sm transition-colors ${
                batchLoading || localApiStatus === 'offline'
                  ? 'bg-[#222] text-[#555] cursor-not-allowed'
                  : 'bg-white text-black hover:bg-[#eee]'
              }`}
            >
              {batchLoading ? (
                <>
                  <svg className="animate-spin h-3.5 w-3.5" viewBox="0 0 24 24">
                    <circle className="opacity-25" cx="12" cy="12" r="10" stroke="currentColor" strokeWidth="4" fill="none" />
                    <path className="opacity-75" fill="currentColor" d="M4 12a8 8 0 018-8V0C5.373 0 0 5.373 0 12h4z" />
                  </svg>
                  取得中...
                </>
              ) : localApiStatus === 'offline' ? 'API停止中' : '取得開始'}
            </button>
          </div>

          {/* 進捗バー */}
          {batchLoading && (
            <div className="space-y-1.5">
              <div className="flex justify-between text-xs text-[#888]">
                <span>{batchProgress.message}</span>
                <span className="flex gap-3">
                  {batchProgress.eta && <span className="text-yellow-400">{batchProgress.eta}</span>}
                  <span>{batchProgress.current}%</span>
                </span>
              </div>
              <div className="w-full bg-[#1e1e1e] rounded-full h-1.5 overflow-hidden">
                <div className="bg-white h-1.5 rounded-full transition-all duration-500" style={{ width: `${batchProgress.current}%` }} />
              </div>
            </div>
          )}
        </div>

        {/* 取得完了サマリー */}
        {batchResult && batchResult.stats?.period && (
          <div className="bg-[#0a1a0a] border border-[#1a3a1a] rounded-lg px-5 py-4 flex flex-wrap gap-5 items-center">
            <span className="text-xs text-[#4ade80] font-medium">✓ 取得完了</span>
            <span className="text-xs text-[#888]">{batchResult.stats.period} · {batchResult.stats.total_months}ヶ月</span>
            <span className="text-xs text-white font-medium">{batchResult.races_collected}レース</span>
            <span className="text-xs text-[#555]">{batchResult.elapsed_time}秒</span>
          </div>
        )}

        {/* 取得済みデータ統計 */}
        <div className="bg-[#111] border border-[#1e1e1e] rounded-lg p-5">
          <div className="flex items-center justify-between mb-4">
            <h2 className="text-sm font-medium text-white">取得済みデータ</h2>
            <button
              onClick={() => { setShowCollectedData(v => !v); if (!showCollectedData) fetchCollectedData() }}
              className="text-xs text-[#555] hover:text-[#888] transition-colors"
            >
              {showCollectedData ? '閉じる ▲' : 'レース一覧を見る ▼'}
            </button>
          </div>

          <div className="grid grid-cols-3 gap-3">
            <div className="bg-[#0a0a0a] border border-[#1e1e1e] rounded-lg p-3">
              <div className="text-xs text-[#666] mb-1">総レース数</div>
              <div className="text-xl font-bold text-white">{dataStats.totalRaces.toLocaleString()}</div>
            </div>
            <div className="bg-[#0a0a0a] border border-[#1e1e1e] rounded-lg p-3">
              <div className="text-xs text-[#666] mb-1">総出走馬数</div>
              <div className="text-xl font-bold text-white">{dataStats.totalResults.toLocaleString()}</div>
            </div>
            <div className="bg-[#0a0a0a] border border-[#1e1e1e] rounded-lg p-3">
              <div className="text-xs text-[#666] mb-1">最終取得日</div>
              <div className="text-sm font-medium text-[#aaa]">
                {dataStats.latestDate ? new Date(dataStats.latestDate).toLocaleDateString('ja-JP') : '未取得'}
              </div>
            </div>
          </div>

          {/* レース一覧（折りたたみ） */}
          {showCollectedData && (
            <div className="mt-4 border-t border-[#1e1e1e] pt-4">
              <div className="flex justify-between items-center mb-3">
                <span className="text-xs text-[#555]">最近取得したレース（最新50件）</span>
                <button onClick={fetchCollectedData} className="text-xs text-[#555] hover:text-[#888] transition-colors">更新</button>
              </div>
              <div className="space-y-1 max-h-96 overflow-y-auto">
                {collectedRaces.length === 0 ? (
                  <div className="py-8 text-center text-xs text-[#444]">データがまだ取得されていません</div>
                ) : collectedRaces.map(race => (
                  <div key={race.race_id} className="flex items-center justify-between px-3 py-2.5 rounded bg-[#0a0a0a] hover:bg-[#161616] transition-colors group">
                    <div className="min-w-0 flex-1">
                      <div className="flex items-center gap-2 flex-wrap">
                        <span className="text-xs font-medium text-white">{race.race_name || `${race.venue} ${race.race_no}R`}</span>
                        <span className="text-xs text-[#555]">{race.venue}</span>
                        <span className="text-xs text-[#444]">{race.track_type} {race.distance}m</span>
                      </div>
                      <div className="text-[10px] text-[#333] mt-0.5">{race.date || race.created_at?.slice(0, 10) || ''}</div>
                    </div>
                    <button
                      onClick={() => fetchRaceDetail(race.race_id)}
                      className="text-xs text-[#444] hover:text-[#888] transition-colors opacity-0 group-hover:opacity-100 ml-3 shrink-0"
                    >
                      詳細
                    </button>
                  </div>
                ))}
              </div>
            </div>
          )}
        </div>

        {/* レース詳細モーダル */}
        {selectedRaceDetail && (
          <div className="fixed inset-0 bg-black/60 flex items-center justify-center z-50 p-4" onClick={() => setSelectedRaceDetail(null)}>
            <div className="bg-[#111] border border-[#1e1e1e] rounded-lg max-w-4xl w-full max-h-[85vh] overflow-hidden" onClick={e => e.stopPropagation()}>
              <div className="flex items-center justify-between px-5 py-4 border-b border-[#1e1e1e]">
                <h3 className="text-sm font-medium">レース詳細</h3>
                <button onClick={() => setSelectedRaceDetail(null)} className="text-[#555] hover:text-white text-xl leading-none">×</button>
              </div>
              <div className="overflow-auto max-h-[calc(85vh-56px)]">
                <table className="w-full text-xs">
                  <thead className="bg-[#0a0a0a] sticky top-0">
                    <tr>
                      {['着', '枠', '馬番', '馬名', '性齢', '斤量', '騎手', 'タイム', 'オッズ', '人気'].map(h => (
                        <th key={h} className="px-3 py-2.5 text-left font-medium text-[#555] first:pl-5">{h}</th>
                      ))}
                    </tr>
                  </thead>
                  <tbody>
                    {selectedRaceDetail.results.map((result: any, i: number) => (
                      <tr key={i} className={`border-b border-[#1a1a1a] hover:bg-[#161616] transition-colors ${result.finish_position === 1 ? 'bg-[#0a1500]' : ''}`}>
                        <td className={`px-3 py-2.5 pl-5 font-bold ${result.finish_position <= 3 ? 'text-[#4ade80]' : 'text-[#888]'}`}>{result.finish_position}</td>
                        <td className="px-3 py-2.5 text-[#666]">{result.bracket_number}</td>
                        <td className="px-3 py-2.5 font-bold">{result.horse_number}</td>
                        <td className="px-3 py-2.5 font-medium text-white">{result.horse_name}</td>
                        <td className="px-3 py-2.5 text-[#888]">{result.sex_age || `${result.sex || ''}${result.age || ''}`}</td>
                        <td className="px-3 py-2.5 text-[#888]">{result.jockey_weight}kg</td>
                        <td className="px-3 py-2.5 text-[#888]">{result.jockey_name}</td>
                        <td className="px-3 py-2.5 font-mono text-[#aaa]">{result.finish_time?.toFixed(1)}</td>
                        <td className="px-3 py-2.5 font-medium">{result.odds?.toFixed(1)}</td>
                        <td className="px-3 py-2.5 text-[#666]">{result.popularity}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            </div>
          </div>
        )}

        {/* プロファイリング（折りたたみ） */}
        <div className="border border-[#1e1e1e] rounded-lg overflow-hidden">
          <button
            onClick={() => setShowProfiling(v => !v)}
            className="w-full flex items-center justify-between px-5 py-3.5 bg-[#111] hover:bg-[#161616] transition-colors"
          >
            <span className="text-xs text-[#555]">特徴量プロファイリングレポート（オプション）</span>
            <svg className={`w-3 h-3 text-[#444] transition-transform ${showProfiling ? 'rotate-180' : ''}`} fill="none" viewBox="0 0 24 24" stroke="currentColor">
              <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M19 9l-7 7-7-7" />
            </svg>
          </button>
          {showProfiling && (
            <div className="px-5 pb-5 pt-4 bg-[#0d0d0d] border-t border-[#1e1e1e] space-y-3">
              <label className="flex items-center gap-2 text-xs text-[#888] cursor-pointer select-none">
                <input type="checkbox" checked={useOptimized} onChange={e => setUseOptimized(e.target.checked)} className="w-3.5 h-3.5 accent-white" />
                LightGBM最適化済み（リーク除去・変換適用）
              </label>
              <div className="flex items-center gap-3">
                <button
                  onClick={handleStartProfiling}
                  disabled={profilingStatus === 'running'}
                  className={`px-4 py-2 rounded text-xs font-medium transition-colors ${profilingStatus === 'running' ? 'bg-[#1a1a1a] text-[#555] cursor-not-allowed' : 'bg-white text-black hover:bg-[#eee]'}`}
                >
                  {profilingStatus === 'running' ? '生成中...' : 'レポート生成'}
                </button>
                {profilingStatus === 'completed' && profilingJobId && (
                  <a href={`/api/profiling/html/${profilingJobId}`} target="_blank" rel="noopener noreferrer" className="text-xs text-[#4ade80] hover:underline">レポートを開く →</a>
                )}
              </div>
              {profilingStatus === 'running' && (
                <div className="space-y-1">
                  <div className="flex justify-between text-xs text-[#555]">
                    <span>{profilingMessage}</span>
                    <span>{profilingProgress}%</span>
                  </div>
                  <div className="w-full bg-[#1e1e1e] rounded-full h-1 overflow-hidden">
                    <div className="bg-[#555] h-1 rounded-full transition-all duration-700" style={{ width: `${profilingProgress}%` }} />
                  </div>
                </div>
              )}
              {profilingStatus === 'error' && <p className="text-xs text-red-400">{profilingMessage}</p>}
            </div>
          )}
        </div>

        {/* 次のステップ */}
        <div className="p-5 bg-[#111] border border-[#1e1e1e] rounded-lg flex items-center justify-between gap-4">
          <div>
            <div className="text-xs text-[#666] mb-0.5">次のステップ — 02</div>
            <div className="text-sm font-medium">モデル学習</div>
            <div className="text-xs text-[#555] mt-0.5">収集したデータでAIモデルをトレーニング</div>
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

      <Toast
        message={toast.message}
        type={toast.type}
        isVisible={toast.visible}
        onClose={() => setToast(t => ({ ...t, visible: false }))}
      />
    </div>
  )
}

