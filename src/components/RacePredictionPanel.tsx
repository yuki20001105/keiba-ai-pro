'use client'
import type { RacePredictResult } from '@/lib/race-analysis-types'

function probColor(p: number): string {
  if (p > 0.3) return 'bg-[#86efac]'
  if (p > 0.2) return 'bg-[#a3e635]'
  if (p > 0.1) return 'bg-[#fbbf24]'
  return 'bg-[#555]'
}

type Props = { result: RacePredictResult }

export function RacePredictionPanel({ result }: Props) {
  const preds = result.predictions
  const rec = result.recommendation
  const maxPRaw = Math.max(...preds.map(p => p.p_raw), 0.001)

  return (
    <div className="flex-1 overflow-auto p-4 space-y-4">
      {/* 推奨カード */}
      {rec && (
        <div className={`p-4 rounded border ${rec.action === '見送り' ? 'bg-[#111] border-[#1e1e1e] text-[#666]' : 'bg-[#0a1a0a] border-[#1e3a1e]'}`}>
          <div className="flex items-start justify-between gap-4">
            <div>
              <div className="text-xs text-[#666] mb-1">推奨アクション</div>
              <div className={`text-base font-bold ${rec.action === '見送り' ? 'text-[#888]' : 'text-[#86efac]'}`}>
                {rec.action} · {result.best_bet_type || '—'}
              </div>
              <div className="text-xs text-[#555] mt-1">{rec.reason}</div>
            </div>
            {rec.action !== '見送り' && (
              <div className="flex gap-3 shrink-0 text-right">
                <div>
                  <div className="text-[10px] text-[#555]">購入点数</div>
                  <div className="text-sm font-bold">{rec.purchase_count}点</div>
                </div>
                <div>
                  <div className="text-[10px] text-[#555]">単価</div>
                  <div className="text-sm font-bold">¥{rec.unit_price?.toLocaleString()}</div>
                </div>
                <div>
                  <div className="text-[10px] text-[#555]">合計</div>
                  <div className="text-sm font-bold">¥{rec.total_cost?.toLocaleString()}</div>
                </div>
                <div>
                  <div className="text-[10px] text-[#555]">期待回収</div>
                  <div className="text-sm font-bold text-[#7dd3fc]">¥{rec.expected_return?.toLocaleString()}</div>
                </div>
              </div>
            )}
          </div>
          {/* 買い目 */}
          {rec.action !== '見送り' && (() => {
            const combos = result.best_bet_type && result.bet_types?.[result.best_bet_type]
              ? result.bet_types[result.best_bet_type].slice(0, rec.purchase_count)
              : []
            if (combos.length === 0) return null
            return (
              <div className="mt-3 pt-3 border-t border-[#1e2a1e]">
                <div className="text-[10px] text-[#555] mb-1.5">買い目（{result.best_bet_type}）</div>
                <div className="flex flex-wrap gap-2">
                  {combos.map((c, ci) => (
                    <span key={ci} className="text-xs px-2.5 py-1 bg-[#0a0a0a] border border-[#2a3a2a] rounded font-mono text-[#86efac]">
                      {c.combination}
                    </span>
                  ))}
                </div>
              </div>
            )
          })()}
        </div>
      )}

      {/* 馬一覧 */}
      <div className="space-y-2">
        {preds.map((p) => {
          const barW = maxPRaw > 0 ? Math.round((p.p_raw / maxPRaw) * 100) : 0
          const isTop = p.predicted_rank === 1
          const isTop3 = p.predicted_rank <= 3
          return (
            <div
              key={p.horse_number}
              className={`p-3 rounded border ${isTop ? 'bg-[#0d1a0d] border-[#2a4a2a]' : isTop3 ? 'bg-[#0f130f] border-[#1e2a1e]' : 'bg-[#0c0c0c] border-[#1a1a1a]'}`}
            >
              <div className="flex items-center gap-3">
                <div className={`w-7 h-7 rounded-full flex items-center justify-center text-xs font-bold shrink-0 ${isTop ? 'bg-[#fbbf24] text-black' : isTop3 ? 'bg-[#333] text-white' : 'bg-[#1a1a1a] text-[#555]'}`}>
                  {p.predicted_rank}
                </div>
                <div className="w-6 text-center text-xs text-[#666]">{p.horse_number}</div>
                <div className="flex-1 min-w-0">
                  <div className="font-medium text-sm truncate">{p.horse_name}</div>
                  <div className="text-[10px] text-[#555] truncate">{p.jockey_name}{p.age ? ` · ${p.sex}${p.age}` : ''}</div>
                </div>
                <div className="flex items-center gap-4 shrink-0 text-right">
                  <div>
                    <div className="text-[10px] text-[#555]">スコア</div>
                    <div className="text-sm font-mono font-bold text-[#7dd3fc]">{(p.p_raw * 100).toFixed(1)}</div>
                  </div>
                  <div>
                    <div className="text-[10px] text-[#555]">正規化</div>
                    <div className="text-sm font-mono">{(p.p_norm * 100).toFixed(1)}%</div>
                  </div>
                  <div>
                    <div className="text-[10px] text-[#555]">オッズ</div>
                    <div className="text-sm font-mono">{p.odds != null ? `${p.odds}倍` : '—'}</div>
                  </div>
                  <div>
                    <div className="text-[10px] text-[#555]">期待値</div>
                    <div className={`text-sm font-mono ${p.expected_value != null && p.expected_value >= 1 ? 'text-[#86efac]' : 'text-[#f87171]'}`}>
                      {p.expected_value != null ? p.expected_value.toFixed(2) : '—'}
                    </div>
                  </div>
                  <div>
                    <div className="text-[10px] text-[#555]">人気</div>
                    <div className="text-sm font-mono">{p.popularity ?? '—'}</div>
                  </div>
                </div>
              </div>
              {/* 確率バー */}
              <div className="mt-2 flex items-center gap-2">
                <div className="flex-1 bg-[#1a1a1a] rounded-full h-1.5 overflow-hidden">
                  <div
                    className={`h-full rounded-full transition-all ${probColor(p.p_norm)}`}
                    style={{ width: `${barW}%` }}
                  />
                </div>
                <span className="text-[10px] text-[#555] w-12 text-right">{(p.p_norm * 100).toFixed(1)}%</span>
              </div>
            </div>
          )
        })}
      </div>
    </div>
  )
}
