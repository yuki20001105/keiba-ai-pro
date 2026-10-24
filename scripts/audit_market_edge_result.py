"""Supplemental uncertainty audit after the registered early stop, not a new search.

No new fits, thresholds, model choice, policy choice, or later-period access.
Original artifacts remain byte-for-byte unchanged; this report binds their hashes.
"""
import hashlib
import json
import os
from pathlib import Path
import sys

for name in ("OMP_NUM_THREADS", "OPENBLAS_NUM_THREADS", "MKL_NUM_THREADS", "NUMEXPR_NUM_THREADS"):
    os.environ[name] = "2"
REPO = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(REPO / "python-api"), str(REPO / "keiba")]
ROOT = REPO / "reports/generated/model-experiments/market-edge-v1"


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def main():
    import numpy as np
    import pandas as pd
    from market_edge.evaluation import maximum_ev, betting_summary, payout_for
    manifest = json.loads((ROOT / "artifact_manifest.json").read_text(encoding="utf-8"))
    for relative, expected in manifest["files"].items():
        if sha(ROOT / relative) != expected:
            raise ValueError("Sealed result changed: " + relative)
    decision = json.loads((ROOT / "research_result.json").read_text(encoding="utf-8"))
    if decision["status"] != "early_stopped" or decision["candidate"] != "logistic":
        raise ValueError("Supplement restricted to this already stopped campaign")
    benchmark = json.loads((ROOT / "cpu_gpu_benchmark.json").read_text(encoding="utf-8"))
    device = benchmark["chosen_validation_artifact_device"]
    data = pd.read_parquet(ROOT / "development.parquet")
    validation = data.loc[data.phase.eq("validation")].copy()
    joined = validation.merge(pd.read_parquet(ROOT / "jobs" / f"benchmark-{device}-0/predictions.parquet"),
                              on=["race_id", "horse_number"], validate="one_to_one")
    maxima = maximum_ev(joined)
    selected = maxima.loc[maxima.selection_ev.gt(1.30)].copy()
    summary = betting_summary(selected, race_count=joined.race_id.nunique())
    if summary["unknown_payouts"] or summary["bets"] != 18:
        raise ValueError("Unexpected diagnostic point cohort")
    selected["payout"] = payout_for(selected)
    days = pd.Index(sorted(joined.race_date.unique()))
    totals = selected.groupby("race_date").agg(payout=("payout", "sum"), bets=("payout", "size")).reindex(days, fill_value=0)
    rng = np.random.default_rng(42)
    estimates = []
    for _ in range(2000):
        draw = rng.integers(0, len(days), len(days))
        count = totals.bets.to_numpy()[draw].sum()
        if count:
            estimates.append(float(totals.payout.to_numpy()[draw].sum()/count))
    payouts = selected.payout.sort_values(ascending=False)
    diagnostic = {"model": "lightgbm", "kind": "ordinary", "threshold": 1.30,
                  "threshold_was_predeclared": True, "selected_for_extra_audit_after_outcomes": True,
                  "adopted_policy": False, **summary, "winning_tickets": int(selected.finish.eq(1).sum()),
                  "day_bootstrap_roi_ci95": [float(x) for x in np.quantile(estimates, [.025,.975])],
                  "maximum_payout_yen": float(payouts.max()),
                  "roi_excluding_largest_ticket": float(payouts.iloc[1:].sum()/(len(payouts)-1)),
                  "roi_excluding_top3_tickets": float(payouts.iloc[3:].sum()/(len(payouts)-3)),
                  "top3_share_of_total_payout": float(payouts.head(3).sum()/payouts.sum()),
                  "top3_share_of_positive_ticket_profit": float((payouts.head(3)-100).clip(lower=0).sum()/(payouts-100).clip(lower=0).sum())}
    annual = []
    for phase,path in [("development",ROOT / "jobs/lightgbm-dev/predictions.parquet"),
                       ("validation",ROOT / "jobs" / f"benchmark-{device}-0/predictions.parquet")]:
        part = data.loc[data.phase.eq(phase)].merge(pd.read_parquet(path), on=["race_id","horse_number"],validate="one_to_one")
        picks = maximum_ev(part)
        annual.append({"phase":phase,"year":str(part.race_date.iloc[0])[:4],"period_from":str(part.race_date.min()),
                       "period_to":str(part.race_date.max()), **betting_summary(picks.loc[picks.selection_ev.gt(1.30)],race_count=part.race_id.nunique())})
    diagnostic["annual_consistency"] = annual
    logistic = validation.merge(pd.read_parquet(ROOT / "jobs/logistic-val/predictions.parquet"),on=["race_id","horse_number"],validate="one_to_one")
    control = pd.read_parquet(ROOT / "jobs/market-control-val/predictions.parquet").rename(columns={"probability":"control_probability"})
    logistic = logistic.merge(control,on=["race_id","horse_number"],validate="one_to_one")
    wins = logistic.loc[logistic.finish.eq(1)].copy()
    paired = {}
    for label,column in [("raw_market","normalized_market_probability"),("market_only_logistic","control_probability")]:
        wins["delta"] = -np.log(wins.probability)+np.log(wins[column])
        daily = wins.groupby("race_date").agg(delta=("delta","sum"), races=("delta","size"))
        rng = np.random.default_rng(42)
        samples=[]
        for _ in range(2000):
            draw=rng.integers(0,len(daily),len(daily))
            samples.append(float(daily.delta.to_numpy()[draw].sum()/daily.races.to_numpy()[draw].sum()))
        paired[label]={"mean_nll_delta_candidate_minus_control":float(wins.delta.mean()),
                       "day_bootstrap_ci95":[float(x) for x in np.quantile(samples,[.025,.975])],
                       "negative_is_better":True,"not_selection_adjusted":True}
    output={"schema":"post-evaluation-uncertainty-audit-v1","purpose":"Contextualize tiny NLL improvements and the lone >100% diagnostic point, without revisiting selection.",
            "original_artifact_manifest_sha256":sha(ROOT/"artifact_manifest.json"),"source_sha256":sha(Path(__file__)),
            "registered_early_stop_unchanged":True,"further_trials":0,"later_periods_read":False,
            "bootstrap_repetitions":2000,"seed":42,"resampling_unit":"race_date including zero-bet days",
            "posthoc_inferential_audit_not_a_new_selection_rule":True,
            "positive_diagnostic_point":diagnostic,"paired_nll_uncertainty":paired,
            "conclusion":"No reproducible profitable policy demonstrated. Diagnostic ROI120% depends entirely on one winning ticket; NLL gains are small and require uncertainty qualification."}
    with (ROOT/"post_evaluation_audit.json").open("x",encoding="utf-8",newline="\n") as stream:
        json.dump(output,stream,ensure_ascii=False,indent=2,allow_nan=False);stream.write("\n")
    print(json.dumps(output,ensure_ascii=False,indent=2))


if __name__ == "__main__":
    main()
