import { modelVerificationStatus, type ModelVerificationEvidence } from '@/lib/model-verification-status'

export function ModelVerificationStatus({ evidence }: { evidence?: ModelVerificationEvidence | null }) {
  return (
    <section aria-label="3つの検証状態" className="space-y-2">
      <dl className="grid grid-cols-1 gap-2 text-xs sm:grid-cols-3">
        {modelVerificationStatus(evidence).map(state => (
          <div key={state.key} className="rounded border border-[#252525] px-3 py-2" data-testid={`verification-${state.key}`}>
            <dt className="text-[#888]">{state.label}</dt>
            <dd className={`mt-1 ${state.verified ? 'text-[#86efac]' : 'text-[#aaa]'}`}>{state.value}</dd>
          </div>
        ))}
      </dl>
      <p className="text-[10px] leading-5 text-[#777]">勝率の検証だけでは、利益や購入戦略の優位性は確認できません。</p>
    </section>
  )
}
