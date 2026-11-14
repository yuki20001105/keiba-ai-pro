import type { RacePredictResult } from '@/lib/race-analysis-types'

const japanTime = new Intl.DateTimeFormat('ja-JP', {
  timeZone: 'Asia/Tokyo', month: '2-digit', day: '2-digit', hour: '2-digit', minute: '2-digit',
  second: '2-digit', hour12: false,
})

function timestamp(value: string | null) {
  const parsed = value ? Date.parse(value) : NaN
  return Number.isFinite(parsed) ? japanTime.format(parsed) : '未確認'
}

export function ModelComparisonPanel({ result }: { result: RacePredictResult }) {
  const comparison = result.model_comparison
  if (!comparison) {
    return <p className="text-xs text-[#888]">新モデル比較は再予測後に表示します。</p>
  }
  const rows = comparison.predictions
  const validProbability = (value: number) => Number.isFinite(value) && value >= 0 && value <= 1
  const aligned = comparison.schema === 'pace74-shadow-comparison-v1'
    && comparison.reference_only === true && comparison.purchase_eligible === false
    && comparison.input_aligned && comparison.primary_model_id === result.model_id
    && comparison.candidate_model_id === 'pace74-c09-minleaf240' && comparison.candidate_feature_count === 74
    && rows.length === result.predictions.length && rows.length > 1
    && new Set(rows.map(row => row.horse_number)).size === rows.length
    && new Set(rows.map(row => row.candidate_rank)).size === rows.length
    && rows.every(row => {
      const primary = result.predictions.find(horse => horse.horse_number === row.horse_number)
      return primary && row.primary_rank === primary.predicted_rank
        && validProbability(row.primary_probability) && Math.abs(row.primary_probability - primary.p_norm) < 1e-8
        && validProbability(row.candidate_probability) && Number.isInteger(row.candidate_rank)
        && row.candidate_rank >= 1 && row.candidate_rank <= rows.length
    })
    && Math.abs(rows.reduce((sum, row) => sum + row.candidate_probability, 0) - 1) < 1e-6
  const available = comparison.status === 'compared' && aligned
  const observation = comparison.observation_kind === 'historical'
    ? '過去レースの比較です。前向き運用成績には数えません。'
    : comparison.observation_kind === 'pre_race'
      ? '発走前の比較記録です。正式採用・購入の承認ではありません。'
      : '発走前の入力・取得証跡は検証待ちです。'

  return (
    <section aria-label="新旧モデル比較（参考専用）" className="rounded border border-[#334155] bg-[#111820] p-3 text-xs text-[#bbb]">
      <h3 className="font-semibold text-[#bae6fd]">新モデル比較 · 74特徴量・勝率モデル（参考専用）</h3>
      <p className="mt-1">購入判断は現行モデルのままです。新モデルの値は購入・実運用の成績に反映しません。</p>
      <p className="mt-1 text-[#facc15]">正式切替未承認 · 発走前データ、確率校正、オッズ、運用成績と既存の承認基準の確認が必要です。</p>
      {!available ? (
        <p role="status" className="mt-2">比較を利用できません。入力・モデルを確認し、再予測してください。</p>
      ) : (
        <>
          <p className="mt-2">研究対照（80特徴量）との比較ではNLL・Brier改善、ECE悪化。現行本番に対する優位性・収益性は未確認です。</p>
          <p className="mt-2">{observation}</p>
          <p className="mt-1">比較時刻（日本時間）: {timestamp(comparison.captured_at)} · オッズ取得時刻: {timestamp(comparison.odds_observed_at)}</p>
          {!comparison.odds_verified && <p className="mt-1">発走前オッズの取得証跡は未検証です。参考期待値は保存・確定オッズを含み、将来の利益を示しません。</p>}
          {!comparison.recorded && <p role="status" className="mt-1 text-[#facc15]">比較台帳への保存に失敗しました。この表示は記録済みの検証証跡ではありません。</p>}
          <div className="mt-2 overflow-x-auto">
            <table aria-label="新旧勝率の比較（購入対象外）" className="w-full whitespace-nowrap text-right">
              <thead className="text-[#888]">
                <tr>
                  <th className="p-2 text-left">馬番・馬名</th><th className="p-2">現行順位</th>
                  <th className="p-2">現行勝率</th><th className="p-2">新モデル順位</th>
                  <th className="p-2">新モデル勝率</th><th className="p-2">単勝オッズ</th><th className="p-2">新モデル参考期待値</th>
                </tr>
              </thead>
              <tbody>
                {[...rows].sort((a, b) => a.candidate_rank - b.candidate_rank).map(row => {
                  const oddsValid = row.odds !== null && Number.isFinite(row.odds) && row.odds > 1
                  const evValid = oddsValid && row.expected_value !== null && Number.isFinite(row.expected_value)
                    && Math.abs(row.expected_value - row.candidate_probability * row.odds!) < 1e-6
                  return (
                    <tr key={row.horse_number} className="border-t border-[#263241]">
                      <td className="p-2 text-left">{row.horse_number} {row.horse_name}</td>
                      <td className="p-2">{row.primary_rank}</td><td className="p-2">{(row.primary_probability * 100).toFixed(1)}%</td>
                      <td className="p-2">{row.candidate_rank}</td><td className="p-2">{(row.candidate_probability * 100).toFixed(1)}%</td>
                      <td className="p-2">{oddsValid ? row.odds!.toFixed(1) : '—'}</td>
                      <td className="p-2">{evValid ? row.expected_value!.toFixed(2) : '—'}</td>
                    </tr>
                  )
                })}
              </tbody>
            </table>
          </div>
          <p className="mt-2 break-all text-[#888]">現行: {comparison.primary_model_id} / 参考: {comparison.candidate_model_id} · {comparison.recorded ? '比較台帳に記録済み' : '未記録'}</p>
        </>
      )}
    </section>
  )
}
