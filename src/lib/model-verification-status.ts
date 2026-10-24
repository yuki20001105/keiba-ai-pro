/** Display evidence only. These labels never grant EV or purchase authority. */
export type ModelVerificationEvidence = {
  status?: string
  calibrated?: boolean
  validated?: boolean
  market_edge_verified?: boolean
  profitability_verified?: boolean
}

export function modelVerificationStatus(evidence?: ModelVerificationEvidence | null) {
  const probabilityVerified = evidence?.status === 'validated'
    && evidence.calibrated === true && evidence.validated === true
  const marketVerified = evidence?.market_edge_verified === true
  const strategyVerified = evidence?.profitability_verified === true
  return [
    { key: 'probability', label: '勝率の検証', value: probabilityVerified ? '検証済み' : evidence?.calibrated === true ? '校正済み・未検証' : '未確認', verified: probabilityVerified },
    { key: 'market', label: '市場への追加価値', value: marketVerified ? '検証済み' : '未確認', verified: marketVerified },
    { key: 'strategy', label: '購入戦略の検証', value: strategyVerified ? '検証済み' : '未確認', verified: strategyVerified },
  ] as const
}
