import type { PredictionInputDiagnostics } from '@/lib/race-analysis-types'
import { predictionInputVerified, sharedInputFormulaMatchedCaptureUnverified } from '@/lib/prediction-input-contract'

export function PredictionInputNotice({ diagnostics }: { diagnostics?: PredictionInputDiagnostics | null }) {
  const known = diagnostics?.schema === 'prediction-input-contract-v1'
    && ['aligned', 'degraded', 'unverified'].includes(diagnostics.status)
    && Number.isInteger(diagnostics.expected_count) && diagnostics.expected_count >= 0
    && Number.isInteger(diagnostics.actual_count) && diagnostics.actual_count >= 0
    && Array.isArray(diagnostics.missing_columns) && diagnostics.missing_columns.every(item => typeof item === 'string')
    && Array.isArray(diagnostics.blocked_columns) && diagnostics.blocked_columns.every(item => typeof item === 'string')
    ? diagnostics : null
  if (predictionInputVerified(known)) return null
  const missing = known?.missing_columns ?? []
  const candidateFormulaMatched = known?.scope === 'local-safe-candidate-input-v1'
    && known.feature_pipeline === undefined
    && known.semantic_parity_verified === true && known.source_hash_matches === true
  const sharedFormulaMatched = sharedInputFormulaMatchedCaptureUnverified(known)
  const winNllReference = known?.feature_pipeline === 'local-win-nll-reference-v1'
    && known.reference_only === true
  const completeWinNllReference = winNllReference
    && known.expected_count === 50 && known.actual_count === 50
    && missing.length === 0 && known.blocked_columns.length === 0
  const contextReference = known?.feature_pipeline === 'local-db-context-v1'
    && known.reference_only === true
  const completeContextReference = contextReference
    && known.expected_count === 80 && known.actual_count === 80
    && missing.length === 0 && known.blocked_columns.length === 0
    && known.source_hash_matches === true && known.semantic_parity_verified === true
  return (
    <div role="status" className="rounded border border-[#665120] bg-[#211b0d] px-4 py-3 text-xs text-[#fde68a]">
      <p>{completeContextReference ? '条件・展開の比較候補 · 購入判断未承認'
        : completeWinNllReference ? '参考運用：発走前オッズ・未知期間の検証待ち'
        : candidateFormulaMatched || sharedFormulaMatched ? '入力式は一致 · 取得時点未確認 · 参考予測'
        : known?.status === 'degraded' ? '入力に不足あり · 参考予測' : '入力互換性未確認 · 参考予測'}</p>
      <details className="mt-2 text-[#aaa]">
        <summary className="cursor-pointer">入力の詳細</summary>
        <div className="mt-2 space-y-1">
          {known && <p>特徴量 {known.actual_count}/{known.expected_count} · 未計算 {missing.length}件</p>}
          {missing.length > 0 && <p className="break-words">未計算: {missing.join('、')}</p>}
          {!!known?.blocked_columns.length && <p>予測時に使えない結果項目を含む旧モデルです。</p>}
          {known?.source_hash_matches === false && <p>学習時から入力生成処理が変わっています。</p>}
          {known?.history_available === false && <p>過去成績を読み込めませんでした。</p>}
          <p>{contextReference ? '保存済みの過去情報から、条件適性・馬体重推移・枠・脚質／展開・血統を追加した比較候補です。欠損や支持件数も記録し、校正値を購入判断の根拠には使用しません。'
            : winNllReference ? '保存済み候補の校正値を参考表示しています。過去の最終オッズから発走前オッズへの適用と未知期間の検証が未完了です。購入判断未承認のため、買い目の根拠には使用しません。'
            : sharedFormulaMatched ? '学習時の生成式と一致。発走前の取得証拠が未確認です。買い目の根拠には使用しません。'
            : candidateFormulaMatched ? '学習時の生成式と一致。発走前の取得証拠と勝率校正は未確認です。購入推奨には使用しません。'
            : '学習時の入力との同等性は未確認です。買い目の根拠には使用しません。'}</p>
        </div>
      </details>
    </div>
  )
}
