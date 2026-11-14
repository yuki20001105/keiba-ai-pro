"""Local read-only analysis of a completed, independently verified campaign."""
from collections import Counter
import argparse
from datetime import datetime
import html
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / "python-api"), str(ROOT / "keiba")]

import joblib
import numpy as np
import pandas as pd

from experiments.common import file_sha256, read_json, write_json
from experiments import pace_research_loop as loop
from training import pace_research as research

def mean(result, metric):
    return float(np.mean([fold["metrics"][metric] for fold in result["folds"]]))


def fmt(value, percent=False):
    return f"{value * 100:.2f}%" if percent else f"{value:.5f}"


def main(campaign, report):
    CAMPAIGN, REPORT = Path(campaign).resolve(), Path(report).resolve()
    verification = read_json(CAMPAIGN / "verification.json")
    if verification["verified"] is not True:
        raise ValueError("verified-completed-research-required")
    loop.assert_task(CAMPAIGN)
    state = loop.cli.store_for(CAMPAIGN).snapshot(CAMPAIGN.name)
    if state["status"] != "completed":
        raise ValueError("completed-research-ledger-required")
    for trial in state["trials"]:
        loop.validate_reuse(CAMPAIGN, trial)
    development = read_json(CAMPAIGN / "development-results.json")
    confirmation = read_json(CAMPAIGN / "confirmation-results.json")
    selection = read_json(CAMPAIGN / "selection.json")
    run = read_json(CAMPAIGN / "run-summary.json")
    task = read_json(CAMPAIGN / "task-ledger.json")
    budget = read_json(CAMPAIGN / "fit-budget.json")
    timing = read_json(CAMPAIGN / "run-budget.json")
    if timing["deadline"] - timing["started"] != task["budget"]["seconds"]:
        raise ValueError("fixed-runtime-budget-duration-mismatch")
    if budget["deadline"] != timing["deadline"] or budget["maximum"] != task["budget"]["maximum_fits"]:
        raise ValueError("physical-fit-budget-contract-mismatch")
    fitted_count, normalizers = 0, 0
    thread_counts = set()
    for path in (CAMPAIGN / "trials").glob("*/artifacts/*/research.joblib"):
        saved = joblib.load(path)
        fits = [saved["fit"]] + ([saved["auxiliary"]] if "auxiliary" in saved else [])
        for fitted in fits:
            fitted_count += 1
            normalizers += int(fitted["baseline"] is not None)
            thread_counts.add(fitted["model"].params.get("num_threads"))
    cached_count = 0
    for path in (CAMPAIGN / "head-cache").glob("*/heads.joblib"):
        heads = joblib.load(path)
        for name in ("pace", "position"):
            for key in ("model", "first600_model"):
                model = heads[name].get(key)
                if model is not None:
                    cached_count += 1
                    thread_counts.add(model.params.get("num_threads"))
    fit_counts = Counter(item["name"] for item in budget["records"])
    if (fitted_count + cached_count != fit_counts["lightgbm"]
            or normalizers != fit_counts["speed-normalization"] or thread_counts != {2}):
        raise ValueError("persisted-model-fit-or-thread-accounting-mismatch")
    model_audit = {"persisted_final_boosters": fitted_count, "persisted_head_boosters": cached_count,
                   "total_boosters": fitted_count + cached_count, "speed_normalizers": normalizers,
                   "native_threads": sorted(thread_counts), "all_accounted_for": True}
    write_json(CAMPAIGN / "model-fit-audit.json", model_audit)
    baseline = development[0]
    winner = next(item for item in development if item["id"] == selection["winner"])
    final_winner = next(item for item in confirmation if item["id"] == selection["winner"])
    by_id = {item["id"]: item for item in development}
    delta = mean(winner, "nll") - mean(baseline, "nll")
    elapsed = (run["finished"] - run["started"]) / 60
    folds = []
    for candidate in development:
        folds.append({"id": candidate["id"], "mean_nll": mean(candidate, "nll"),
                      "mean_brier": mean(candidate, "brier"), "mean_top1": mean(candidate, "top1"),
                      "mean_ece": mean(candidate, "ece"),
                      "feature_count": candidate["folds"][0]["features"],
                      "annual_nll": {str(fold["year"]): fold["metrics"]["nll"] for fold in candidate["folds"]}})
    ablations = []
    for group in ("condition_aptitude", "weight", "pedigree", "gate", "style_pace"):
        candidate = by_id.get("group-" + group)
        if candidate:
            ablations.append({"group": group, "nll_delta": mean(candidate, "nll") - mean(baseline, "nll"),
                              "brier_delta": mean(candidate, "brier") - mean(baseline, "brier"),
                              "top1_delta": mean(candidate, "top1") - mean(baseline, "top1")})
    incremental = None
    if "win80" in by_id:
        rng_increment = np.random.default_rng(42)
        annual_draws = []
        for year in (2022, 2023, 2024):
            left = pd.read_parquet(CAMPAIGN / "trials" / "dev-win80" / "artifacts" / str(year) / "race-records.parquet")
            right = pd.read_parquet(CAMPAIGN / "trials" / ("dev-" + selection["winner"]) /
                                    "artifacts" / str(year) / "race-records.parquet")
            paired_increment = left.merge(right, on=["race_id", "race_date"], suffixes=("_base", "_candidate"),
                                          validate="one_to_one")
            paired_increment["improvement"] = paired_increment.nll_base - paired_increment.nll_candidate
            days = paired_increment.groupby("race_date").improvement.agg(["sum", "count"])
            draws = rng_increment.integers(0, len(days), size=(1000, len(days)))
            annual_draws.append(days["sum"].to_numpy()[draws].sum(axis=1) /
                                days["count"].to_numpy()[draws].sum(axis=1))
        values = np.asarray(annual_draws).mean(axis=0)
        incremental = {"comparison": "selected versus win80 in development only",
                       "equal_year_nll_improvement": mean(by_id["win80"], "nll") - mean(winner, "nll"),
                       "date_bootstrap_stratified_by_year_ci95": np.quantile(values, [.025, .975]).tolist(),
                       "independent_confirmation": False, "selection_multiplicity_corrected": False}
    target, x, _ = loop.load_data(CAMPAIGN / "dataset", confirmation=True)
    test = target.race_date.str.startswith("2026")
    t, features = target.loc[test], x.loc[test]
    base_directory = CAMPAIGN / "trials" / "confirm-speed46" / "artifacts" / "2026"
    win_directory = CAMPAIGN / "trials" / ("confirm-" + selection["winner"]) / "artifacts" / "2026"
    importance = pd.read_csv(win_directory / "importance.csv")
    importance["gain_share"] = importance.gain / max(float(importance.gain.sum()), 1e-30)
    selected_bundle = joblib.load(win_directory / "research.joblib")
    selected_fit = selected_bundle["fit"]
    card = {"schema": "pace-research-selected-model-card-v2", "id": selection["winner"],
            "artifact_sha256": file_sha256(win_directory / "research.joblib"),
            "task_sha256": file_sha256(CAMPAIGN / "task-ledger.json"),
            "specification": selected_bundle["specification"],
            "objective": selected_fit["objective"],
            "lightgbm_parameters": selected_fit["model"].params,
            "boosting_iterations": selected_fit["model"].current_iteration(),
            "feature_columns": selected_fit["feature_columns"],
            "category_dictionaries": selected_fit["encoder"].categories,
            "calibration": selected_fit["calibration"],
            "training_date_to": selected_bundle["training_date_to"],
            "head_probability_calibrated": False,
            "head_input_columns": selected_bundle["heads"]["base_columns"] if selected_bundle["heads"] else [],
            "production_eligible": False, "deployed": False}
    write_json(CAMPAIGN / "selected-model-card.json", card)
    speed_importance = pd.read_csv(base_directory / "importance.csv")
    speed_class_share = float(speed_importance.loc[speed_importance.feature.eq("safe_race_class"), "gain"].sum()
                              / max(float(speed_importance.gain.sum()), 1e-30))
    selected_class_share = float(importance.loc[importance.feature.eq("safe_race_class"), "gain_share"].sum())
    base_records = pd.read_parquet(base_directory / "race-records.parquet")
    candidate_records = pd.read_parquet(win_directory / "race-records.parquet")
    paired = base_records.merge(candidate_records, on=["race_id", "race_date"], suffixes=("_base", "_candidate"))
    subgroup = []
    for dimension in ("surface", "venue"):
        for value, frame in paired.groupby(dimension + "_base"):
            if len(frame) >= 100:
                subgroup.append({"dimension": dimension, "value": str(value), "races": len(frame),
                                 "nll_improvement": float((frame.nll_base - frame.nll_candidate).mean()),
                                 "top1_change": float((frame.top1_candidate - frame.top1_base).mean())})
    old_bundle = joblib.load(CAMPAIGN / "trials" / "confirm-legacy90" / "artifacts" / "2026" / "research.joblib")
    predictions = research.predict_head_features(old_bundle["heads"], t, features, "legacy")
    pace = predictions.groupby(t.race_id)[["pace_slow_probability", "pace_middle_probability",
                                          "pace_fast_probability"]].first().max(axis=1)
    strata = pd.DataFrame({"race_id": pace.index, "pace_confidence": pace.values})
    strata["stratum"] = pd.cut(strata.pace_confidence, [-np.inf, .5, .7, np.inf],
                               labels=["<=0.5", "0.5-0.7", ">0.7"]).astype(str)
    old_records = pd.read_parquet(CAMPAIGN / "trials" / "confirm-legacy90" / "artifacts" / "2026" / "race-records.parquet")
    pace_pair = base_records.merge(old_records, on=["race_id", "race_date"], suffixes=("_base", "_pace"))
    pace_pair = pace_pair.merge(strata, on="race_id")
    pace_strata = []
    for name, frame in pace_pair.groupby("stratum"):
        pace_strata.append({"stratum": name, "races": len(frame),
                            "nll_delta": float((frame.nll_pace - frame.nll_base).mean()),
                            "top1_change": float((frame.top1_pace - frame.top1_base).mean())})
    # Read-only conditional permutation keeps each race's pace constant and only
    # exchanges forecasts among the same observed surface/distance/venue.
    from training import pace_forecast as legacy
    original_matrix = loop.feature_matrix(t, features, predictions, old_bundle["specification"])
    original_fit = old_bundle["fit"]
    original_probability = loop.predict_bundle(old_bundle, t, features)[1]
    original_nll = research.summary_metrics(research.race_records(t, original_probability))["nll"]
    condition = t.drop_duplicates("race_id")[["race_id", "surface", "distance", "venue"]]
    race_predictions = predictions.groupby(t.race_id).first()
    rng = np.random.default_rng(42)
    permutation = []
    for repeat in range(5):
        swapped = race_predictions.copy()
        changed_races = 0
        columns = list(legacy.FORECAST_COLUMNS[:4])
        for _, group in condition.groupby(["surface", "distance", "venue"], dropna=False):
            ids = group.race_id.to_numpy()
            donor = rng.permutation(ids)
            changed_races += int((ids != donor).sum())
            swapped.loc[ids, columns] = race_predictions.loc[donor, columns].to_numpy()
        matrix = original_matrix.copy()
        for name in columns:
            matrix[name] = t.race_id.map(swapped[name])
        score = original_fit["model"].predict(original_fit["encoder"].transform(matrix), num_threads=2)
        p = research.race_probabilities(score, t.race_id, original_fit["calibration"]["temperature"])
        nll = research.summary_metrics(research.race_records(t, p))["nll"]
        permutation.append({"repeat": repeat, "nll_increase": nll - original_nll, "changed_races": changed_races})
    analysis = {"schema": "pace-research-posthoc-analysis-v2",
                "script_sha256": file_sha256(Path(__file__)), "selection_unchanged": True,
                "uses_confirmation_only_after_selection": True,
                "multiple_subgroups_not_independent_confirmation": True,
                "development": folds, "single_group_ablations": ablations,
                "incremental_vs_direct_win80": incremental,
                "training_race_class_gain_share": {"speed46": speed_class_share,
                                                   "selected": selected_class_share,
                                                   "causal_feature_importance": False},
                "confirmation_subgroups": subgroup, "legacy_pace_confidence_strata": pace_strata,
                "conditional_pace_permutation": permutation}
    write_json(CAMPAIGN / "posthoc-analysis.json", analysis)
    body = [
        "# 展開予測の深掘り研究・自動改善ループ",
        "",
        f"実行日: {datetime.fromtimestamp(run['finished']).strftime('%Y-%m-%d')}。"
        "学習・評価・考察・次候補選定・再学習を実データで実行した。",
        "",
        "## 1. 結論",
        "",
        f"研究用の最終選定は **`{selection['winner']}`**。"
        f"開発3年の平均勝率NLLは **{fmt(mean(baseline, 'nll'))} → {fmt(mean(winner, 'nll'))}**"
        f"（差 {delta:+.5f}、低いほど良い）。",
        "",
        f"独立再計算の検証: **合格**。固定後の既知2026年での比較改善判定: "
        f"**{'改善を確認' if verification['model_improved'] else '改善確定には至らず'}**。",
        "**本番モデルの切替は行っていない。** この結果は研究候補の選定であり、"
        "未知期間の優位性、回収率改善、発走前の公開API整合性を証明しない。",
        "",
        "**一番大きな成果は、速度を当てる学習から、勝ち馬へ確率を配る学習へ目的を合わせたこと。**"
        f" 選定した{final_winner['folds'][0]['features']}特徴量版に対しても、"
        "展開ヘッドなし80特徴量版との差は以下の区間推定で別に判定する。"
        "主モデルの改善を、そのまま展開追加の効果とは扱わない。",
        "",
        "選定版は固定した数値選定規則で最小になったため保存したもので、"
        "単純な80特徴量版より複雑にする価値まで確定した、という意味ではない。",
        "",
        f"実行は **{run['steps']}ステップ・{run['configurations']}構成・"
        f"{run['fits_reserved']} fit予約・{elapsed:.1f}分**。"
        f"終了理由は `{run['stop_reason']}`。1ワーカー・LightGBM 2スレッドを維持した。",
        "",
        "## 2. 比較方法とデータの境界",
        "",
        "- 2016年からの固定スナップショットと、出所を検証済みの600mラップだけを再利用。新規スクレイプなし。",
        "- TRAIN / CAL / VALID は、2016–2020 / 2021 / 2022、"
        "2016–2021 / 2022 / 2023、2016–2022 / 2023 / 2024の3組。",
        "- 開発候補を固定した後だけ、2016–2024で再学習、2025で温度校正、2026で既知期間の確認。",
        "- 展開入力は過去年だけで学習したヘッドの予測。今回の実ラップ・実通過順位は教師だけに使用。",
        "- 同日結果は使用しない。前処理・条件基準は各TRAINだけで適合。CALでは温度1個だけを適合。",
        "- 同着レースは丸ごと確率評価・勝率目的の学習から除外。候補間の評価集団を一致させる。",
        "- 主指標は年を等重みにしたレース平均NLL。Brier・本命的中率の年別非劣化も要求。",
        "",
        "46/80/90の対照は**今回の条件で再学習したモデル**であり、稼働中の本番モデルではない。"
        "今回は全候補を基本200ラウンドで比較し、前回のTRAIN内2候補選択を行う方式とは"
        "学習条件が異なる。前回の表と今回の表を混ぜて効果を計算しない。",
        "",
        "基本設定: 最終モデルはLightGBM・200ラウンド・31葉・学習率0.05・"
        "最小葉30行・L2=1・max_bin=63・seed=42。"
        "展開ヘッドは80ラウンド・15葉・学習率0.05・L2=1。"
        "温度は各CALだけで0.02–10の範囲に適合。選定モデルの最終温度は"
        f"{final_winner['folds'][0]['calibration']['temperature']:.6f}。",
        "",
        "## 3. 開発期間で実際に比較した全構成",
        "",
        "| 構成 | 特徴量数 | 2022 NLL | 2023 NLL | 2024 NLL | 平均NLL↓ | Brier↓ | 本命的中率↑ |",
        "|---|---:|---:|---:|---:|---:|---:|---:|",
    ]
    for item in folds:
        year_values = item["annual_nll"]
        body.append(f"| `{item['id']}` | {item['feature_count']} | "
                    f"{year_values['2022']:.5f} | {year_values['2023']:.5f} | {year_values['2024']:.5f} | "
                    f"{item['mean_nll']:.5f} | {item['mean_brier']:.5f} | {fmt(item['mean_top1'], True)} |")
    body += [
        "",
        "構成名: speedは速度偏差回帰、winはレース内勝率NLLの直接最適化。"
        "46/80は入力プロファイル。legacy90は従来の10展開予測を追加した対照。"
        "groupは46に対象グループだけを追加。hierは条件階層化＋予測600m時間、"
        "fieldは競合の最大値・ばらつき・先行候補数等、interactは馬固有の脚質との交互作用。",
        "",
        "blendは速度46モデルと開発期間で選んだwinモデルの固定比率合成であり、"
        "別の学習済み残差モデルではない。葉数15・L2=5の正則化候補も用意したが、"
        "3ステップ連続で所定のNLL改善幅0.001を満たさず停止したため、今回は未実行。",
        "",
        "## 4. 悪化原因をどう切り分けたか",
        "",
        "### 学習目的のずれ",
        "",
        "| 同じ入力 | 速度回帰NLL | 勝率直接学習NLL | 差（直接学習－速度） |",
        "|---|---:|---:|---:|",
    ]
    for speed, win, label in (("speed46", "win46", "46"), ("speed80", "win80", "80"), ("legacy90", "win90", "90")):
        if win in by_id:
            body.append(f"| {label} | {mean(by_id[speed], 'nll'):.5f} | {mean(by_id[win], 'nll'):.5f} | "
                        f"{mean(by_id[win], 'nll') - mean(by_id[speed], 'nll'):+.5f} |")
    if incremental:
        low_increment, high_increment = incremental["date_bootstrap_stratified_by_year_ci95"]
        body += [
            "",
            f"**展開の追加価値は別に判定する。** 選定候補と、展開ヘッドを使わない`win80`の"
            f"開発NLL差は **{incremental['equal_year_nll_improvement']:+.7f}** "
            f"（正なら選定候補が良い）。年別に日を再標本化した95%区間は"
            f" **[{low_increment:+.5f}, {high_increment:+.5f}]**。",
            "この区間が0を含むなら、数値上の最小値を選べても、展開追加の有効性を"
            "確認したとは言えない。主な改善を学習目的の見直しと展開の効果に混同しない。"
            "この比較も選定済み開発期間内であり、多重選択バイアスは残る。",
        ]
    body += [
        "",
        "これは同じ特徴量で学習目的を置き換えた比較。速度をうまく当てることと、"
        "同一レース内で勝ち馬へ確率を配ることは同じではない。"
        "winモデルのスコアを物理的な速度予測とは呼ばない。",
        "",
        f"学習内のレースクラスGain比率も、速度46対照の{speed_class_share * 100:.2f}%から、"
        f"選定モデルの{selected_class_share * 100:.2f}%へ変わった。"
        "同一レースの全馬に共通な加点は、レース内softmaxで相殺される。"
        "したがって、レース全体の速さを表す情報を増やすだけでは、"
        "その中のどの馬が勝つかの改善には直結しない。"
        "このGain比較は学習時の使い方の説明であり、未使用期間の因果効果ではない。",
        "",
        "### 文脈グループを1つずつ追加した影響",
        "",
        "| 追加グループ | NLL差↓ | Brier差↓ | 本命的中率差 |",
        "|---|---:|---:|---:|",
    ]
    for item in ablations:
        body.append(f"| {item['group']} | {item['nll_delta']:+.5f} | {item['brier_delta']:+.5f} | "
                    f"{item['top1_delta'] * 100:+.2f}ポイント |")
    body += [
        "",
        "グループ追加は再学習アブレーション。Gainの大きさだけを効果の根拠にはしていない。"
        "それぞれを単独で追加した結果であり、組合せでの相互作用まで否定・保証しない。",
        "",
        "### 展開の条件標準化・利用可能性",
        "",
        "旧方式は小標本の細条件を全体の絶対秒数基準へ戻していた。"
        "新方式は芝/ダート×距離の十分な標本を親として、競馬場×馬場の中央値を縮約し、"
        "条件残差でクラスを定義した。親条件が不足する場合は不明とし、"
        "未知条件を全体の秒数だけでハイ・スローに決めない。",
        "",
        "旧クラスと新クラスは教師の定義が違うため、分類正解率を横並びで優劣判定しない。"
        "hierでは予測600m時間の入力も追加しているため、改善・悪化を"
        "階層化だけの因果効果とは断定できない。",
        "",
        "| 構成・検証年 | TRAINペース可用率 | CAL | VALID | TRAIN位置可用率 |",
        "|---|---:|---:|---:|---:|",
    ]
    for name in ("legacy90", "hier-both", "field-win80", "interact-win46"):
        for fold in by_id.get(name, {}).get("folds", []):
            rates = fold["forecast_availability"]
            if rates:
                body.append(f"| {name}・{fold['year']} | {fmt(rates['TRAIN']['pace_model_available'], True)} | "
                            f"{fmt(rates['CAL']['pace_model_available'], True)} | "
                            f"{fmt(rates['VALID']['pace_model_available'], True)} | "
                            f"{fmt(rates['TRAIN']['position_model_available'], True)} |")
    body += [
        "",
        "初期年の教師不足による学習側と予測側の分布差は、この可用率で確認する。"
        "欠損を架空の脚質や未来モデルの予測で埋めてはいない。"
        "可用率差を測定したことと、その悪影響を単独で因果同定・完全解消したことは区別する。",
        "",
        "### 展開ヘッドの条件だけの対照",
        "",
        "同じ階層化教師を使うhier/conditionだけを比較する。conditionはレース条件のみ、"
        "hierは出走メンバーの過去情報も使用。これは分類しやすいラベルへ変えただけの"
        "見かけの精度改善と、メンバー情報の寄与を区別するための対照。",
        "",
        "| ヘッド入力 | 検証年 | ラップのあるレース | ペースNLL↓ | 正解率↑ | 600m MAE秒↓ |",
        "|---|---:|---:|---:|---:|---:|",
    ]
    for name in ("condition-pace", "hier-both", "field-win80"):
        for fold in by_id.get(name, {}).get("folds", []):
            pace_metrics = (fold.get("head_metrics") or {}).get("pace", {})
            if pace_metrics.get("nll") is not None:
                body.append(f"| {name} | {fold['year']} | {pace_metrics['race_count']} | "
                            f"{pace_metrics['nll']:.5f} | {fmt(pace_metrics['accuracy'], True)} | "
                            f"{pace_metrics['first600_mae_seconds']:.3f} |")
    body += [
        "",
        "**ここは改善しきれていない。** メンバー情報を加えたペースヘッドのNLLは、"
        "条件だけの対照より3年すべてで悪かった。豊富な入力を増やしただけでは"
        "ペース確率が良くならないことを確認した。ラップ教師の少なさ・偏りや"
        "確率未校正が残るが、この比較だけで悪化原因を一つに断定はしない。",
        "",
        "## 5. 固定後の既知2026年確認",
        "",
        "| 構成 | NLL↓ | Brier↓ | 本命的中率↑ | 本命ECE↓ |",
        "|---|---:|---:|---:|---:|",
    ]
    for item in confirmation:
        values = item["folds"][0]["metrics"]
        body.append(f"| `{item['id']}` | {values['nll']:.5f} | {values['brier']:.5f} | "
                    f"{fmt(values['top1'], True)} | {values['ece']:.5f} |")
    interval = verification["comparisons"]["2026"]
    low, high = interval["nll_improvement_ci95"]
    body += [
        "",
        f"選定候補の46対照に対するNLL改善は **{interval['nll_improvement']:+.5f}**。"
        f"開催日を対にしたbootstrap 95%区間は **[{low:+.5f}, {high:+.5f}]**"
        f"（{interval['paired_dates']}日、{interval['races']}レース、1,000回）。",
        "",
        "2026年は過去の研究でも見た期間であり、未知の将来テストではない。"
        "多数の候補を選んだことによるバイアスも、この区間では補正されない。",
        "",
        "## 6. 使えそうな傾向と、使ってはいけない解釈",
        "",
        "以下は選定を固定した後の事後的な条件別分析。条件ごとの採用・買い目調整には使用していない。"
        "100レース以上ある条件だけを表示し、小標本・多重比較・期間依存を残す。",
        "",
        "| 条件 | レース数 | 選定候補のNLL改善↑ | 本命的中率差 |",
        "|---|---:|---:|---:|",
    ]
    for item in subgroup:
        body.append(f"| {item['dimension']}={item['value']} | {item['races']} | "
                    f"{item['nll_improvement']:+.5f} | {item['top1_change'] * 100:+.2f}ポイント |")
    body += [
        "",
        "### 従来90特徴量モデルは、ペースに自信がある条件で有効か",
        "",
        "| ペース最大クラス確率 | レース数 | 90－46 NLL差↓ | 本命的中率差 |",
        "|---|---:|---:|---:|",
    ]
    for item in pace_strata:
        body.append(f"| {item['stratum']} | {item['races']} | {item['nll_delta']:+.5f} | "
                    f"{item['top1_change'] * 100:+.2f}ポイント |")
    changes = [item["nll_increase"] for item in permutation]
    body += [
        "",
        "ペースヘッドの最大確率は校正済みの信頼度ではない。高確率だから買い増す根拠にはしない。",
        "",
        f"同じ芝/ダート・距離・競馬場のレース間でペース4入力をまとめて交換した5回の"
        f"条件付き置換では、90モデルのNLL変化は平均 **{np.mean(changes):+.6f}**、"
        f"範囲 **[{min(changes):+.6f}, {max(changes):+.6f}]**。",
        "これは固定モデルがその入力に依存しているかを見る診断であり、"
        "再学習アブレーションや因果効果ではない。相手の構成や条件を完全に一致させた置換でもない。",
        "",
        "### 選定モデルが使った特徴量",
        "",
        "| 特徴量 | 学習内Gain比率 | Split回数 |",
        "|---|---:|---:|",
    ]
    for row in importance.head(12).itertuples():
        basic_labels = {"safe_prev_finish": "前走着順", "safe_past5_avg_finish": "過去5走の平均着順",
                        "safe_past3_avg_finish": "過去3走の平均着順", "safe_past10_top3_rate": "過去10走の複勝率",
                        "safe_past10_avg_finish": "過去10走の平均着順", "safe_horse_weight": "馬体重",
                        "safe_prior_starts": "確認済み過去走数", "safe_days_since_last": "前走からの日数",
                        "safe_past10_avg_last3f_time": "過去10走の上がり平均", "safe_past3_top3_rate": "過去3走の複勝率"}
        label = loop.context.FEATURE_LABELS.get(row.feature, basic_labels.get(row.feature, row.feature))
        body.append(f"| {label} (`{row.feature}`) | {fmt(row.gain_share, True)} | {row.split} |")
    body += [
        "",
        "Gainは学習時に使われた分岐の指標であり、未使用期間での因果的な有効性や"
        "単独除去時の改善幅ではない。blendの場合はwin側の構成モデルの重要度を表示する。",
        "",
        "## 7. 自動化と監査",
        "",
        "有限の仮説群を順に実行し、開発期間だけのNLLと年別ガードから次の候補を選ぶ。"
        "固定比率補正と正則化だけを追加探索できる。任意のコード自己書換え、無制限Optuna、"
        "失敗・unknownワーカーの自動再投入、予算の自動増額はない。",
        "",
        f"予約内訳: `{dict(Counter(item['name'] for item in budget['records']))}`。"
        "fit予約はLightGBMだけでなく、条件閾値・速度正規化・温度校正も数える。"
        "キャッシュ再利用はデータ・ソース・cutoff・モードの一致と成果物SHAを確認する。",
        f" 保存済みモデルから数え直したLightGBMは{model_audit['total_boosters']}個"
        f"（最終/構成モデル{fitted_count}、展開ヘッド{cached_count}）。"
        "すべての実モデルのnum_threads=2を確認し、`model-fit-audit.json`に保存した。",
        "",
        "独立検証は、元評価集団と保存予測を照合し、別実装の確率指標でNLL・Brier・ECEを再計算。"
        "確認用全スコアを保存モデルから再実行し、実レース3件へ当該着順・時計・通過順位を"
        "注入しても予測が変わらないことを確認した。スナップショット・本番ポインタ・"
        "本番モデル本体・受入/購入ポリシーのSHA一致も確認した。",
        "",
        "これは保存済みデータでの推論整合性。発走前に取得した実際の出馬表・オッズ、"
        "公開API、本番受入、Kelly、購入履歴の再検証とは別。",
        "",
        "## 8. 保存先・再確認",
        "",
        f"- キャンペーン: `reports\\generated\\model-experiments\\{CAMPAIGN.name}`",
        "- `task-ledger.json` / `approval.json`: 固定条件・承認・データ/ソースのハッシュ",
        "- `attempts` / `decisions` / `progress`: 各サイクルの仮説・評価・採否・ハッシュ連鎖",
        "- `development-results.json` / `confirmation-results.json`: 全比較",
        "- `selection.json`: 2025/2026を見る前に固定した候補",
        "- `verification.json`: 独立再計算・区間・改変除外・本番不変の結果",
        "- `posthoc-analysis.json`: 条件別傾向と置換診断",
        "- `selected-model-card.json`: 全入力特徴量・カテゴリ辞書・実パラメータ・校正・モデルSHA",
        f"- 選定研究バンドル: `trials\\confirm-{selection['winner']}\\artifacts\\2026\\research.joblib`",
        "- `sources`: 実行時コードの保存版",
        "",
        "再確認:",
        "",
        "```powershell",
        ".\\python-api\\.venv\\Scripts\\python.exe -X utf8 scripts\\run_pace_research.py verify "
        f"--root reports\\generated\\model-experiments\\{CAMPAIGN.name}",
        "```",
        "",
        "レポート・事後分析の再生成:",
        "",
        "```powershell",
        ".\\python-api\\.venv\\Scripts\\python.exe -X utf8 scripts\\analyze_pace_research.py "
        f"--root reports\\generated\\model-experiments\\{CAMPAIGN.name} "
        "--report docs\\reports\\pace_research_20261114.md",
        "```",
        "",
        "## 9. 到達点と残る限界",
        "",
        "この有限探索で比較した候補の中から、事前に固定した基準に合うものを選んだ。"
        "あらゆる特徴量・全パラメータ空間での大域的最適性や、展開の完全予測を主張しない。",
        "ラップは限られた取得済みHTMLからの非無作為標本で、少数コース・出遅れ・騎手判断・"
        "接触・実際の走行レーンや馬間距離は十分には観測できていない。",
        "",
        "本番に進める条件は、以後の未使用期間・発走前入力による並走評価と、"
        "独立した受入手順。予測時点のオッズ証跡がないため、本研究では回収率・期待利益を"
        "最適化・保証していない。",
        "",
    ]
    REPORT.parent.mkdir(parents=True, exist_ok=True)
    REPORT.write_text("\n".join(body), encoding="utf-8")
    # Offline, no CDN, telemetry, external fonts or network dependencies.
    try:
        import markdown
        content = markdown.markdown("\n".join(body), extensions=["tables", "fenced_code"])
    except ImportError:
        content = "<pre>" + html.escape("\n".join(body)) + "</pre>"
    css = """body{font-family:system-ui,sans-serif;max-width:1180px;margin:36px auto;padding:0 24px;color:#17233b;
line-height:1.75;background:#f7f9fc}h1,h2{color:#102a52}h2{border-top:2px solid #dfe7f3;padding-top:24px}
table{border-collapse:collapse;width:100%;font-size:14px;background:white;margin:20px 0}
th,td{border:1px solid #dfe5ef;padding:8px 10px;text-align:right}th:first-child,td:first-child{text-align:left}
th{background:#e8effa}pre{white-space:pre-wrap;background:#e8eef8;padding:18px;border-radius:8px}
code{overflow-wrap:anywhere}strong{color:#124a77}p,li{max-width:1100px}"""
    labels = {"speed46": "速度回帰46（再学習対照）", "speed80": "速度回帰80",
              "legacy90": "従来の展開90", selection["winner"]: f"選定研究モデル{final_winner['folds'][0]['features']}"}
    bars = ['<svg role="img" aria-label="既知2026年のモデル比較" viewBox="0 0 1100 360" '
            'style="width:100%;background:white;border-radius:12px">',
            '<text x="20" y="30" font-size="18">既知2026年：NLLは低いほど良い・本命的中率は高いほど良い</text>',
            '<text x="300" y="65">勝率NLL（0始点、上限2.5）</text>',
            '<text x="740" y="65">本命的中率（0始点、上限40%）</text>']
    for index, result in enumerate(confirmation):
        y = 105 + index * 65
        metric = result["folds"][0]["metrics"]
        color = "#127e83" if result["id"] == selection["winner"] else "#8a9db7"
        bars += [f'<text x="20" y="{y + 17}" font-size="15">{html.escape(labels[result["id"]])}</text>',
                 f'<rect x="300" y="{y}" width="{360 * metric["nll"] / 2.5:.2f}" height="26" fill="{color}"/>',
                 f'<text x="670" y="{y + 19}" font-size="14">{metric["nll"]:.4f}</text>',
                 f'<rect x="740" y="{y}" width="{230 * metric["top1"] / .4:.2f}" height="26" fill="{color}"/>',
                 f'<text x="990" y="{y + 19}" font-size="14">{100 * metric["top1"]:.2f}%</text>']
    bars.append("</svg>")
    content = "".join(bars) + content
    REPORT.with_suffix(".html").write_text(
        '<!doctype html><html lang="ja"><meta charset="utf-8"><meta name="viewport" content="width=device-width">'
        "<title>展開予測・自動改善研究</title><style>" + css + "</style><body>" + content + "</body></html>",
        encoding="utf-8")
    print(json.dumps({"report": str(REPORT), "selected": selection["winner"],
                      "development_nll": [mean(baseline, "nll"), mean(winner, "nll")],
                      "verification": verification["model_improved"], "elapsed_minutes": elapsed,
                      "conditional_permutation": changes}, ensure_ascii=False))


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", required=True, type=Path)
    parser.add_argument("--report", required=True, type=Path)
    args = parser.parse_args()
    main(args.root, args.report)
