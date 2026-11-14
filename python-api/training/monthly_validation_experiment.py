"""Offline monthly/forward comparison; artifacts cannot enter the serving registry."""
from __future__ import annotations

from collections import Counter
import hashlib
import json
from pathlib import Path
import platform

import joblib
import numpy as np
import pandas as pd

from training import db_shared_features as shared, db_shared_fit as legacy, db_shared_optimized as optimized
from training.db_shared_grouped import _bind
from training import monthly_validation as monthly

SCHEMA = "monthly-validation-research-model-v1"
SEED = 42


def _sha(path):
    with Path(path).open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def _source_hashes():
    return {**optimized.source_hashes(), "training.monthly_validation": _sha(monthly.__file__),
            "training.monthly_validation_experiment": _sha(__file__),
            **{name: _sha(Path(__file__).parent / relative) for name, relative in (
                ("training.db_shared_grouped", "db_shared_grouped.py"),
                ("training.target_comparison", "target_comparison.py"),
                ("betting.performance", Path("..") / "betting" / "performance.py"),
                ("betting.research_opponent_pairs", Path("..") / "betting" / "research_opponent_pairs.py"),
            )}}


def _membership(roles):
    return hashlib.sha256(json.dumps(roles.sort_values("race_id")[
        ["race_id", "race_date", "partition"]].to_dict("records"),
        sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def _positions(target, roles):
    mapping = roles.set_index("race_id").partition
    labels = target.race_id.map(mapping)
    if labels.isna().any():
        raise ValueError("monthly-research-unassigned-race")
    return {name: np.flatnonzero(labels.eq(name)) for name in ("TRAIN", "CAL", "TEST")}


def fit_candidate(target, features, roles, *, kind):
    if (roles.race_id.duplicated().any() or set(roles.race_id) != set(target.race_id)
            or features.attrs.get("current_outcomes_consumed") is not False
            or features.attrs.get("same_day_or_future_history_used") is not False):
        raise ValueError("monthly-research-split-or-feature-contract")
    if kind == "monthly_random":
        expected = monthly.stratified_partitions(target, seed=SEED)
        expected_sources = {**shared.source_hashes(), "training.monthly_validation": _sha(monthly.__file__)}
        if (features.attrs.get("heldout_outcomes_consumed") is not False
                or features.attrs.get("feature_source_hashes") != expected_sources
                or set(features.attrs.get("withheld_races", [])) != set(
                    roles.loc[roles.partition.isin(["CAL", "TEST"])].race_id)):
            raise ValueError("monthly-research-heldout-history-provenance")
    elif kind == "forward":
        years = roles.loc[roles.partition.eq("TEST")].race_date.str[:4].unique()
        if len(years) != 1 or features.attrs.get("source_hashes") != shared.source_hashes():
            raise ValueError("monthly-research-forward-feature-provenance")
        expected = monthly.forward_partitions(target, year=int(years[0]))
    else:
        raise ValueError("monthly-research-kind-invalid")
    if _membership(expected) != _membership(roles):
        raise ValueError("monthly-research-fixed-seed-or-temporal-split-mismatch")
    normalized, raw = legacy._prepare(target, features)
    split = _positions(normalized, roles)
    if any(len(part) == 0 for part in split.values()):
        raise ValueError("monthly-research-empty-partition")
    sources = _source_hashes()
    fit = _bind(optimized.fit_shared_model, {
        "date_partitions": lambda frame, **_: _positions(frame, roles),
        "source_hashes": _source_hashes, "_resource_limits": lambda *_: (1, 1),
        "_train_booster": optimized._train_booster,
    })
    bundle, result = fit(normalized, raw, n_threads=1)
    if _source_hashes() != sources:
        raise ValueError("monthly-research-source-changed-during-fit")
    bundle.update(schema=SCHEMA, feature_pipeline=SCHEMA, reference_only=True,
                  feature_source_hashes=sources, seed=SEED, split_kind=kind,
                  partition_sha256=_membership(roles),
                  partition_membership={name: sorted(roles.loc[roles.partition.eq(name)].race_id)
                                        for name in ("TRAIN", "CAL", "TEST")})
    bundle["model_input_contract"].update(version=SCHEMA, feature_pipeline=SCHEMA)
    bundle["probability_validation"].update(validated=False, eligible_for_ev=False, reference_only=True,
                                           status="research_only")
    result.update(schema="monthly-validation-research-fit-v1", feature_pipeline=SCHEMA,
                  seed=SEED, split_kind=kind, partition_sha256=_membership(roles),
                  promotion_eligible=False, active_model_changed=False, future_validation_required=True)
    bundle["evaluation"]["evaluation_label"] = (
        "年×月固定seed TEST（研究用・未来学習あり）" if kind == "monthly_random"
        else "過去のみで学習した年次前進TEST（月別集計）")
    return bundle, result


def evaluate_candidate(bundle, target, features, roles):
    if (bundle.get("schema") != SCHEMA or bundle["source_hashes"] != _source_hashes()
            or bundle["partition_sha256"] != _membership(roles)):
        raise ValueError("monthly-research-artifact-or-membership-changed")
    normalized, raw = legacy._prepare(target, features)
    if legacy._frame_digest(raw) != bundle["model_input_contract"]["raw_feature_sha256"]:
        raise ValueError("monthly-research-evaluation-features-changed")
    positions = _positions(normalized, roles)["TEST"]
    test, x = normalized.iloc[positions].copy(), optimized.select_features(raw.iloc[positions])
    if set(test.race_id) & set(bundle["partition_membership"]["TRAIN"] + bundle["partition_membership"]["CAL"]):
        raise ValueError("monthly-research-test-overlaps-development")
    scores = optimized._finite_predictions(bundle["model"], bundle["encoder"].transform(x), 1)
    if bundle["calibrator"] is None:
        raise ValueError("monthly-research-calibration-unavailable")
    test["raw_score"] = scores
    test["p_norm"] = bundle["calibrator"].predict_proba(scores, test.race_id)
    return test


def _settle_predictions(predictions, snapshot):
    from betting.performance import _encoded, _race_date, _settlement
    from betting.research_opponent_pairs import opponent_orders

    results = []
    for rid, group in predictions.groupby("race_id", sort=True):
        row = {"race_id": str(rid), "race_date": str(group.race_date.iloc[0]),
               "cost": 0, "returned": 0, "status": "unavailable"}
        try:
            axis, _, hole = opponent_orders([
                {"horse_number": int(p.horse_number), "probability": float(p.p_norm), "odds": float(p.odds)}
                for p in group.itertuples()])
            h2, h3 = hole[1:3]
            tickets = [{"bet_type": kind, "horse_numbers": numbers, "combination": "-".join(map(str, numbers)), "stake": 500}
                       for kind, numbers in [("ワイド", sorted([axis, h3])),
                                             ("三連単", [h2, axis, h3]), ("三連単", [h3, axis, h2])]]
            frozen = {"race_info": {"num_horses": len(group)},
                      "predictions": group[["horse_number", "horse_id"]].to_dict("records")}
            status, result = _settlement({"race_id": str(rid), "race_date": _race_date(row["race_date"]),
                "payload_json": _encoded(frozen), "tickets_json": _encoded(tickets)}, snapshot)
            row.update(status=status, reason=result.get("reason"))
            if status == "settled":
                row.update(cost=1500, returned=int(result["actual_return"]))
        except ValueError as error:
            row["reason"] = str(error)
        results.append(row)
    return pd.DataFrame(results)


def _summaries(predictions, settlement, features):
    from training.target_comparison import race_metrics

    overall, race_values = race_metrics(predictions, predictions.raw_score, predictions.p_norm)
    races = race_values.merge(settlement, on=["race_id", "race_date"], validate="one_to_one")
    summaries = []
    for month, part in races.groupby(races.race_date.str[:6], sort=True):
        cost, returned = int(part.cost.sum()), int(part.returned.sum())
        ids = set(part.race_id)
        inputs = features.loc[features.index.isin(predictions.loc[predictions.race_id.isin(ids)].index)]
        summaries.append({
            "month": month, "races": len(part),
            **{name: float(part[name].mean()) for name in ("nll", "brier", "spearman", "top1", "top3_recall")},
            "roi_races": int(part.status.eq("settled").sum()), "cost": cost, "returned": returned,
            "profit": returned - cost, "roi": round(100 * returned / cost, 1) if cost else None,
            "unsettled": dict(Counter(part.loc[~part.status.eq("settled")].status)),
            "small_sample": len(part) < 30,
            "feature_profile": {name: {
                "missing_rate": float(inputs[name].isna().mean()),
                "mean": float(inputs[name].mean()) if inputs[name].notna().any() else None}
                for name in ("safe_num_horses", "safe_distance", "safe_horse_weight", "safe_prior_starts", "safe_past3_avg_speed_mps")},
        })
    cost, returned = int(races.cost.sum()), int(races.returned.sum())
    overall.update(cost=cost, returned=returned, profit=returned-cost,
                   roi=round(100*returned/cost, 1) if cost else None,
                   roi_races=int(races.status.eq("settled").sum()),
                   unsettled=dict(Counter(races.loc[~races.status.eq("settled")].status)))
    return overall, summaries, races


def _write(path, value):
    path.write_text(json.dumps(value, ensure_ascii=False, allow_nan=False, indent=2), encoding="utf-8")


def _report(root, summary):
    encoded = json.dumps(summary, ensure_ascii=False, allow_nan=False).replace("<", "\\u003c")
    content = """<!doctype html><meta charset="utf-8"><title>KeibaAI 月別検証</title>
<style>body{font:14px system-ui;background:#0b0d12;color:#ddd;margin:28px}select,button{padding:8px;background:#202632;color:white}table{border-collapse:collapse;width:100%;margin-top:16px}th,td{text-align:right;border-bottom:1px solid #334;padding:8px}th:first-child,td:first-child{text-align:left}.note{color:#aaa;line-height:1.7}svg{width:100%;height:220px;background:#141923;margin-top:12px}</style>
<h1>KeibaAI：固定seed・月別モデル検証</h1>
<p class="note">既存モデルは変更していません。ランダム分割は年×月・レース単位でモデル作成80%／TEST20%。モデル作成80%の内側でTRAIN／CALを80:20に分けます（全体では約64%／16%／20%）。検証・校正レースの結果はランダム候補の過去走特徴にも入れません。<br>
時系列主評価は前年6月までTRAIN・前年7〜12月CAL・対象年TESTの年次前進方式です。各予測はそのモデルのTRAIN/CAL外だけ。過去の既知データによる再検証であり、将来利益の証明ではありません。<br>
固定②各500円・勝負候補の選別なし。保存オッズの発走前取得保証はありません。公式結果・払戻が不足するレースは回収率から除外し、0円の外れにしません。候補は研究専用・本番未昇格です。</p>
<select id="arm"></select> <select id="year"></select><p id="kpi"></p>
<h2>月別回収率（100%が損益分岐）</h2><svg id="chart" viewBox="0 0 1000 220"></svg>
<table><thead><tr><th>年月</th><th>検証レース</th><th>1位的中率</th><th>NLL ↓</th><th>順位相関</th><th>精算レース</th><th>投資</th><th>払戻</th><th>回収率</th></tr></thead><tbody id="body"></tbody></table>
<p class="note">月別特徴の平均・欠損率、正確なTRAIN/CAL/TEST所属、元予測、モデル・入力hashは同じフォルダーのJSON／CSV／Parquetに保存しています。回収率の対象件数は精度検証件数と別です。</p>
<script>const data=DATA;const arm=document.querySelector('#arm'),year=document.querySelector('#year');Object.entries(data.arms).forEach(([k,v])=>arm.add(new Option(v.label,k)));[...new Set(Object.values(data.arms).flatMap(v=>v.months.map(m=>m.month.slice(0,4))))].sort().reverse().forEach(y=>year.add(new Option(y,y)));const yen=v=>'¥'+v.toLocaleString('ja-JP');function draw(){const a=data.arms[arm.value],s=a.summary,rows=a.months.filter(m=>m.month.startsWith(year.value));document.querySelector('#kpi').textContent=`検証 ${s.races.toLocaleString()}レース｜1位的中 ${(100*s.top1).toFixed(1)}%｜NLL ${s.nll.toFixed(4)}｜精算 ${s.roi_races.toLocaleString()}レース｜投資 ${yen(s.cost)}｜払戻 ${yen(s.returned)}｜回収率 ${s.roi===null?'—':s.roi.toFixed(1)+'%'}`;document.querySelector('#body').replaceChildren();rows.forEach(r=>{const tr=document.createElement('tr');[r.month,r.races,(100*r.top1).toFixed(1)+'%',r.nll.toFixed(4),r.spearman.toFixed(3),r.roi_races,yen(r.cost),yen(r.returned),r.roi===null?'—':r.roi.toFixed(1)+'%'].forEach(v=>{const td=document.createElement('td');td.textContent=v;tr.append(td)});document.querySelector('#body').append(tr)});const svg=document.querySelector('#chart');svg.replaceChildren();const ns='http://www.w3.org/2000/svg',max=Math.max(150,...rows.map(r=>r.roi||0)),y=v=>190-170*v/max,x=m=>45+(m-1)*82;const line=document.createElementNS(ns,'line');Object.entries({x1:35,x2:970,y1:y(100),y2:y(100),stroke:'#aaa','stroke-dasharray':'5 5'}).forEach(([k,v])=>line.setAttribute(k,v));svg.append(line);let points=[];for(let m=1;m<=12;m++){const row=rows.find(r=>Number(r.month.slice(4))===m);if(row&&row.roi!==null)points.push(x(m)+','+y(row.roi));else{if(points.length){plot(points);points=[]}}const text=document.createElementNS(ns,'text');text.setAttribute('x',x(m));text.setAttribute('y',213);text.setAttribute('fill','#aaa');text.textContent=m+'月';svg.append(text)}plot(points);function plot(p){if(!p.length)return;const poly=document.createElementNS(ns,'polyline');poly.setAttribute('points',p.join(' '));poly.setAttribute('fill','none');poly.setAttribute('stroke','#60a5fa');poly.setAttribute('stroke-width','2');svg.append(poly);p.forEach(value=>{const [cx,cy]=value.split(',');const dot=document.createElementNS(ns,'circle');dot.setAttribute('cx',cx);dot.setAttribute('cy',cy);dot.setAttribute('r','3');dot.setAttribute('fill','#60a5fa');svg.append(dot)})}}arm.onchange=draw;year.onchange=draw;draw();</script>"""
    (root / "report.html").write_text(content.replace("DATA", encoded), encoding="utf-8")


def run_experiment(root, target, features, history, published, inputs):
    from training.reference_replay import score_batch

    root = Path(root)
    single = target.finish.eq(1).groupby(target.race_id).transform("sum").eq(1)
    target, features = legacy._prepare(
        target.loc[single].reset_index(drop=True), features.loc[single].reset_index(drop=True))
    roles = monthly.stratified_partitions(target, seed=SEED)
    withheld = set(roles.loc[roles.partition.isin(["CAL", "TEST"])].race_id)
    print("Building random-partition features without CAL/TEST outcome history", flush=True)
    random_features = monthly.build_holdout_features(target, history, withheld)
    random_features.to_parquet(root / "random-features.parquet")
    summary = {"schema": "keiba-monthly-validation-v1", "seed": SEED, "promotion_eligible": False,
               "active_model_changed": False, "source_inputs": inputs["files"],
               "source_hashes": _source_hashes(), "versions": {"python": platform.python_version(),
               "numpy": np.__version__, "pandas": pd.__version__}, "arms": {}}
    definitions = [("monthly_random", roles, random_features)]
    definitions += [(f"forward_{year}", monthly.forward_partitions(target, year=year), features)
                    for year in range(2021, int(target.race_date.max()[:4]) + 1)]
    all_forward, forward_metrics = [], []
    for name, split, raw in definitions:
        folder = root / name
        folder.mkdir(exist_ok=True)
        kind = "monthly_random" if name == "monthly_random" else "forward"
        print(f"Fitting {name}: fixed seed 42, one CPU worker/thread", flush=True)
        bundle, fitted = fit_candidate(target, raw, split, kind=kind)
        joblib.dump(bundle, folder / "model.joblib", compress=3)
        split.to_csv(folder / "membership.csv", index=False)
        _write(folder / "fit.json", fitted)
        predictions = evaluate_candidate(bundle, target, raw, split)
        predictions.to_parquet(folder / "predictions.parquet")
        settlement = _settle_predictions(predictions, root / "source.sqlite")
        overall, months, races = _summaries(predictions, settlement, raw)
        races.to_parquet(folder / "race-metrics.parquet")
        _write(folder / "summary.json", {"summary": overall, "months": months})
        if kind == "monthly_random":
            normalized, prepared = legacy._prepare(target, raw)
            positions = _positions(normalized, split)
            selected = next(p for p in optimized.PRESETS if p["id"] == fitted["selected_preset"])
            rerun = optimized._train_booster(
                bundle["encoder"].transform(optimized.select_features(prepared.iloc[positions["TRAIN"]])),
                legacy.apply_speed_deviation_baseline(normalized.iloc[positions["TRAIN"]], bundle["baseline"]).to_numpy(),
                selected, n_threads=1)
            matrix = bundle["encoder"].transform(optimized.select_features(prepared.iloc[positions["TEST"]]))
            first = optimized._finite_predictions(bundle["model"], matrix, 1)
            repeated = optimized._finite_predictions(rerun, matrix, 1)
            if not np.array_equal(first, repeated):
                raise ValueError("Fixed-seed real-data model reproduction failed")
            summary["reproducibility"] = {"predictions_exactly_equal": True,
                "score_sha256": hashlib.sha256(first.tobytes()).hexdigest(),
                "partition_sha256": fitted["partition_sha256"]}
            summary["arms"][name] = {"label": "年×月ランダム20% TEST（研究用）", "summary": overall, "months": months}
        else:
            all_forward.append(predictions)
            forward_metrics.append(races)
        _write(root / "study-progress.json", {"completed": name, "model_sha256": _sha(folder / "model.joblib")})
    forward = pd.concat(all_forward)
    if forward.race_id.nunique() != sum(frame.race_id.nunique() for frame in all_forward):
        raise ValueError("Forward evaluation races overlap between years")
    settlements = pd.concat(forward_metrics, ignore_index=True)[["race_id", "race_date", "cost", "returned", "status"]]
    overall, months, forward_races = _summaries(forward, settlements, features)
    summary["arms"]["forward"] = {"label": "年次前進TEST（月別集計・主評価）", "summary": overall, "months": months}
    available = target.race_date.gt(str(published["training_date_to"]).replace("-", ""))
    baseline = score_batch(published, target.loc[available], features.loc[available])
    baseline["finish"] = target.loc[baseline.index, "finish"]
    baseline["odds"] = target.loc[baseline.index, "odds"]
    settled = _settle_predictions(baseline, root / "source.sqlite")
    overall, months, baseline_races = _summaries(baseline, settled, features)
    summary["arms"]["published"] = {"label": "現行モデル（TRAIN外・校正/評価使用済み含む）", "summary": overall, "months": months}
    from training.target_comparison import paired_comparison
    summary["paired_comparisons"] = {}
    for name, cutoff in [
        ("after_published_calibration", published["probability_validation"]["calibration_period_end"]),
        ("after_published_evaluation", published["probability_validation"]["validation_period_end"]),
    ]:
        old = baseline_races.loc[baseline_races.race_date.gt(str(cutoff).replace("-", ""))]
        common = set(old.race_id) & set(forward_races.race_id)
        if common:
            summary["paired_comparisons"][name] = paired_comparison(
                old.loc[old.race_id.isin(common)], forward_races.loc[forward_races.race_id.isin(common)])
    summary["artifact_sha256"] = {str(path.relative_to(root)): _sha(path)
        for path in root.glob("*/*") if path.is_file() and path.suffix in {".joblib", ".json", ".csv", ".parquet"}}
    _write(root / "study-summary.json", summary)
    _report(root, summary)
    print(json.dumps({name: value["summary"] for name, value in summary["arms"].items()}, ensure_ascii=False), flush=True)
