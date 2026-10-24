import type { SelectionCalibrationMetrics, SelectionDiagnostics } from '@/lib/performance-records'

const percentage = (value: number | null) => value === null ? '未算出' : `${(value * 100).toFixed(1)}%`
const bias = (value: number | null) => value === null ? '未算出' : `${value > 0 ? '+' : ''}${(value * 100).toFixed(1)}pt`
const interval = (value: [number, number] | null) => value === null ? '未算出' : `${bias(value[0])}〜${bias(value[1])}`
const percentageInterval = (value: [number, number] | null) => value === null ? '未算出' : `${percentage(value[0])}〜${percentage(value[1])}`
const exclusions: Record<string, string> = {
  invalid_record: '記録形式が不正', no_selection: '見送り・買い目なし', pending: '結果待ち', payout_pending: '払戻待ち', review: '要確認', non_settled: '未確定',
  reference_only: '参考予測のみ', probability_unverified: '確率の検証が未確認', input_unverified: '入力の取得証拠が未確認', snapshot_unverified: '予測記録の照合が未確認',
  group_identity_missing: 'モデル・選定ルールが未確認', timing_unverified: '選定時点が未確認', invalid_ticket: '買い目が不正', frozen_ticket_mismatch: '選定時の買い目と不一致',
  outcome_missing_or_mismatched: '確定的中結果が不足・不一致', invalid_probability: '確率が不正', invalid_odds: 'オッズが不正', no_eligible_ticket: '診断できる買い目なし',
}

function CalibrationNumbers({ values }: { values: SelectionCalibrationMetrics }) {
  return (
    <dl className="grid grid-cols-2 gap-3 text-xs sm:grid-cols-4">
      <div><dt className="text-[#888]">対象</dt><dd className="mt-1 tabular-nums">{values.ticket_count}買い目 / {values.race_count}レース / {values.date_count}日</dd></div>
      <div><dt className="text-[#888]">予測的中率</dt><dd className="mt-1 tabular-nums">{percentage(values.mean_probability)}</dd><dd className="mt-1 text-[10px] text-[#888]">95%区間 {percentageInterval(values.mean_probability_ci95)}</dd></div>
      <div><dt className="text-[#888]">実績的中率</dt><dd className="mt-1 tabular-nums">{percentage(values.hit_rate)}</dd><dd className="mt-1 text-[10px] text-[#888]">95%区間 {percentageInterval(values.hit_rate_ci95)}</dd></div>
      <div><dt className="text-[#888]">予測 − 実際</dt><dd className="mt-1 tabular-nums">{bias(values.bias_predicted_minus_actual)}</dd><dd className="mt-1 text-[10px] text-[#888]">95%区間 {interval(values.bias_ci95)}</dd>{values.interval_status === 'insufficient_distinct_dates' && <dd className="mt-1 text-[10px] text-[#888]">観測日が少ないため区間未算出</dd>}</div>
    </dl>
  )
}

export function SelectedCalibrationDiagnostics({ diagnostics, loading = false }: { diagnostics: SelectionDiagnostics | null; loading?: boolean }) {
  return (
    <section aria-label="購入対象の校正診断" className="space-y-3 rounded-lg border border-[#252525] bg-[#111] p-4">
      <h2 className="text-sm font-medium">記録した買い目の校正</h2>
      {loading ? <p role="status" className="text-xs text-[#888]">診断を読み込み中...</p> : !diagnostics ? (
        <p className="text-xs text-[#888]">集計機能未反映／データ未取得</p>
      ) : (
        <>
          <p className="text-xs text-[#aaa]">対象 {diagnostics.sample.eligible_tickets}買い目 · {diagnostics.sample.eligible_races}レース · {diagnostics.sample.eligible_dates}日</p>
          <p className="text-[11px] leading-5 text-[#888]">{diagnostics.scope.mode === 'actual' ? '購入済み' : 'AI参考'}の記録を選定時の確率で確認します。観測上の診断で、未知期間の検証や購入の許可ではありません。</p>
          {diagnostics.groups.length === 0 ? <p className="text-xs text-[#aaa]">診断できる確定買い目はまだありません。</p> : (
            <details className="border-t border-[#252525] pt-3">
              <summary className="w-fit cursor-pointer text-xs text-[#7dd3fc] hover:text-white">モデル・選定ルール・オッズ帯を見る</summary>
              <div className="mt-4 space-y-5">
                {diagnostics.groups.map(group => (
                  <article key={`${group.model_id}:${group.policy_version}:${group.bet_type}`} className="space-y-3 border-b border-[#252525] pb-4 last:border-b-0">
                    <div className="text-xs"><p className="break-all text-[#aaa]">モデル: {group.model_id || '未確認'}</p><p className="mt-1 text-[#888]">{group.bet_type} · 選定 {group.policy_version || '未確認'}</p></div>
                    <CalibrationNumbers values={group} />
                    {group.ticket_count < 30 && <p className="text-[11px] text-[#fbbf24]">少数の記録です。区間が狭くても安定性や利益の根拠にはできません。</p>}
                    <details className="text-xs">
                      <summary className="w-fit cursor-pointer text-[#888] hover:text-white">オッズ帯別の詳細</summary>
                      <div className="mt-3 overflow-x-auto">
                        <table className="w-full min-w-[550px] text-left text-[11px]">
                          <thead className="text-[#888]"><tr><th className="pb-2 font-normal">オッズ</th><th className="pb-2 text-right font-normal">買い目 / レース / 日</th><th className="pb-2 text-right font-normal">予測的中率</th><th className="pb-2 text-right font-normal">実績的中率</th><th className="pb-2 text-right font-normal">差 (95%区間)</th></tr></thead>
                          <tbody className="divide-y divide-[#252525] text-[#ccc]">{group.odds_bands.map(band => <tr key={band.band}><td className="py-2">{band.upper === null ? `${band.lower}以上` : `${band.lower}〜${band.upper}未満`}</td><td className="py-2 text-right tabular-nums">{band.ticket_count} / {band.race_count} / {band.date_count}</td><td className="py-2 text-right tabular-nums">{percentage(band.mean_probability)}</td><td className="py-2 text-right tabular-nums">{percentage(band.hit_rate)}</td><td className="py-2 text-right tabular-nums">{bias(band.bias_predicted_minus_actual)}<span className="block text-[#888]">{interval(band.bias_ci95)}</span></td></tr>)}</tbody>
                        </table>
                      </div>
                    </details>
                  </article>
                ))}
                <p className="text-[10px] leading-5 text-[#777]">モデル・ルール・券種は混ぜません。正の差は予測過大です。95%区間は日単位で再標本化した観測上の区間です。複数比較の補正はなく、利益や校正合格の保証ではありません。</p>
              </div>
            </details>
          )}
          {(diagnostics.sample.excluded_records > 0 || diagnostics.sample.excluded_tickets > 0) && (
            <details className="border-t border-[#252525] pt-3 text-xs text-[#888]"><summary className="w-fit cursor-pointer hover:text-white">診断対象外 {diagnostics.sample.excluded_records}記録 / {diagnostics.sample.excluded_tickets}買い目</summary><p className="mt-2">確率やオッズの取得証拠、的中結果が揃わない買い目は補完せず除外します。</p><p className="mt-2 text-[#aaa]">記録の除外理由</p><ul className="mt-2 space-y-1">{Object.entries(diagnostics.excluded_counts).map(([reason, count]) => <li key={reason} className="break-all">{exclusions[reason] || `その他 (${reason})`}: {count}件</li>)}</ul>{Object.keys(diagnostics.excluded_ticket_counts).length > 0 && <><p className="mt-3 text-[#aaa]">買い目の除外理由</p><ul className="mt-2 space-y-1">{Object.entries(diagnostics.excluded_ticket_counts).map(([reason, count]) => <li key={reason} className="break-all">{exclusions[reason] || `その他 (${reason})`}: {count}件</li>)}</ul></>}</details>
          )}
        </>
      )}
    </section>
  )
}
