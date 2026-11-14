"""Render verified ten-cycle evidence without fitting or selecting models."""

import argparse
from collections import Counter
import json
from pathlib import Path
import sys


def render(data):
    verification, summary, selection = (data[key] for key in ("verification", "summary", "selection"))
    if (verification.get("verified") is not True
            or verification.get("production_unchanged") is not True
            or verification.get("production_eligible") is not False):
        raise ValueError("verified-research-with-unchanged-production-required")
    selected = selection["winner"]
    if selected not in {r["id"] for r in data["confirmation"]}:
        raise ValueError("selected-confirmation-missing")

    def mean(result, key):
        return sum(f["metrics"][key] for f in result["folds"]) / len(result["folds"])

    def text(value):
        return " ".join(str(value).split()).replace("|", "\\|")

    def table(results):
        lines = ["| モデル | 特徴量数 | NLL↓ | Brier↓ | 本命的中率↑ | ECE↓ |",
                 "|---|---:|---:|---:|---:|---:|"]
        for result in sorted(results, key=lambda r: mean(r, "nll")):
            label = f"**{result['id']}**" if result["id"] == selected else result["id"]
            lines.append(f"| {label} | {max(f['features'] for f in result['folds'])} | "
                         f"{mean(result, 'nll'):.6f} | {mean(result, 'brier'):.6f} | "
                         f"{mean(result, 'top1'):.2%} | {mean(result, 'ece'):.6f} |")
        return lines

    improvement = "達成" if verification["model_improved"] else "未達"
    minutes = (summary["finished"] - summary["started"]) / 60
    final = verification["comparisons_vs_win80"]["2026"]
    low, high = final["nll_improvement_ci95"]
    base = next(r for r in data["confirmation"] if r["id"] == "win80")
    chosen = next(r for r in data["confirmation"] if r["id"] == selected)
    top1_difference = 100 * (mean(chosen, "top1") - mean(base, "top1"))
    base_ece, chosen_ece = mean(base, "ece"), mean(chosen, "ece")
    lines = ["# AIエージェントによる10サイクル研究", "", "## 結論", "",
             f"**{summary['cycles']}サイクル・新規{summary['new_specifications']}構成を実学習・評価した。** "
             f"研究用の選定モデルは **{selected}**。数値上の最良は "
             f"`{selection['numerical_minimum']}`。NLL差0.001以内では特徴量数、次にBrierを優先した。",
             f"確認期間での改善判定: **{improvement}**。本番モデル・購入条件は変更していない。", "",
             f"本命的中率の差は **{top1_difference:+.3f}ポイント**。"
             f"ECEは{base_ece:.6f}→{chosen_ece:.6f}。"
             + ("ECEの改善は確認できていない。" if chosen_ece >= base_ece
                else "ECEも改善した。")
             + "NLLの改善を、的中率の大幅上昇やすべての確率指標の改善と読み替えない。", "",
             "ここでの最良は、固定した評価方法と今回比較した範囲での選定を意味する。"
             "全構成の最適性、未知の将来精度、馬券の利益を保証しない。", "",
             "## 何を比較したか", "",
             "主指標は2022/2023/2024年のレース平均NLLを年ごとに等重みで平均した値。"
             "小さいほど、勝ち馬に妥当な確率を付けている。"
             "TRAINは各評価年の前々年まで、前年が温度校正用CAL。"
             "対照は、すでに強かった80特徴量の勝率直接学習 `win80`。", "",
             "対照より平均NLLを0.001以上改善し、年別NLL悪化0.005以内、"
             "Brier悪化0.001以内、本命的中率低下0.5ポイント以内、温度境界到達なしを要求した。"
             "適格候補の最良からNLL差0.001以内では特徴量数、Brier、NLL、IDの順で選定する。", "",
             "## 開発期間の全モデル比較", ""]
    lines += table(data["development"])
    lines += ["", "## AIが進めた10回のサイクル", "",
              "毎回の評価を読んで次の3候補を提案した。停滞による早期終了は使用していない。", "",
              "| 回 | 仮説 | 今回の数値上の最良 | 平均NLL | その時点の選定 |",
              "|---:|---|---|---:|---|"]
    for cycle in data["cycles"]:
        best = min(cycle["results"], key=lambda r: mean(r, "nll"))
        lines.append(f"| {cycle['cycle']} | {text(cycle['hypothesis'])} | {best['id']} | "
                     f"{mean(best, 'nll'):.6f} | {cycle['interim_selection']['winner']} |")
    lines += ["", "## 選定後に開いた2026年の確認", "",
              "2024年末までで学習、2025年だけで校正し、2026年を確認した。"
              "この期間は過去の研究でも使用済みで、未知の将来テストではない。"
              "確認結果を見た後でモデルを選び直していない。", ""]
    lines += table(data["confirmation"])
    lines += ["", f"対照からのNLL改善は **{final['nll_improvement']:.6f}** "
              f"（95%区間 **[{low:.6f}, {high:.6f}]**、{final['paired_dates']}開催日・"
              f"{final['races']}レース、開催日単位の対応ブートストラップ）。"
              "正なら改善。区間が0を含むなら改善を断定しない。"
              "多数候補探索の補正済み区間ではなく、既知期間の遡及的な比較である。", "",
              "## モデルと資源", "",
              f"- 実行時間: {minutes:.2f}分。新規開発評価: {summary['new_development_folds']}fold。",
              f"- fit予約: {summary['fit_reservations']}回（学習・温度校正・条件閾値を含む）。"
              "同時学習1件・CPU2スレッド。上限6時間・240予約。",
              f"- 選定仕様: `{json.dumps(selection.get('specification', {}), ensure_ascii=False)}`",
              f"- 研究モデル: `{summary['selected_research_bundle']}`",
              f"- モデルSHA-256: `{summary['selected_research_bundle_sha256']}`",
              f"- 証拠一式: `{data['root']}`",
              "- 全特徴量・実際のLightGBMパラメータ・校正条件は同研究ディレクトリの"
              "`selected-model-card.json`。", "",
              "## 各回の考察", ""]
    for cycle in data["cycles"]:
        lines += [f"### {cycle['cycle']}回目", "", text(cycle["reasoning"]), "",
                  f"事前の検証仮説: {text(cycle['expected_outcome'])}", ""]
    lines += ["## 限界と運用上の扱い", "",
              "過去データの繰り返し選定による過適合は残る。ラップ取得レースは一部で、"
              "通過順位から馬間距離・レーン・当日の騎手判断まで復元できない。"
              "展開追加の有用性は対照をそろえた差で判断し、勝率目的への変更の効果と混同しない。",
              "本番配備・実運用での発走前取得検証・予測時点オッズによる回収率検証は別段階。"
              "今回の成果物は研究用であり、本番モデル一覧や既定ポインタに登録しない。", ""]
    return "\n".join(lines)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", required=True, type=Path)
    parser.add_argument("--report", required=True, type=Path)
    args = parser.parse_args()
    repo = Path(__file__).resolve().parents[1]
    sys.path[:0] = [str(repo / "python-api"), str(repo / "keiba")]
    import joblib
    from experiments import agent_pace_loop as engine
    from experiments.common import file_sha256, json_sha256, read_json, write_json

    root = args.root.resolve()
    task = engine.assert_task(root)
    summary, selection, verification = (read_json(root / name) for name in
                                        ("run-summary.json", "selection.json", "verification.json"))
    engine._assert_finished(root, summary)
    if (verification["selected"] != summary["selected"] or selection["winner"] != summary["selected"]
            or verification["source_sha256"] != json_sha256(task["sources"])
            or file_sha256(Path(summary["selected_research_bundle"])) !=
            verification["selected_research_bundle_sha256"]):
        raise ValueError("verified-selected-model-evidence-mismatch")
    bundle = joblib.load(summary["selected_research_bundle"])
    parameters = {key: (f"{value.__module__}.{value.__name__}" if callable(value) else value)
                  for key, value in bundle["fit"]["model"].params.items()}
    fit_counts = Counter(r["name"] for r in read_json(root / "fit-budget.json")["records"])
    card = {"schema": "agent-pace-research-model-card-v1", "selected": selection["winner"],
            "numerical_minimum": selection["numerical_minimum"],
            "specification": selection["specification"], "parameters": parameters,
            "training_objective": bundle["fit"]["objective"],
            "custom_objective": "training.pace_research.race_objective",
            "booster_objective_note": "LightGBM records objective=none for the supplied custom gradient/hessian.",
            "actual_boosting_trees": bundle["fit"]["model"].num_trees(),
            "feature_columns": list(bundle["fit"]["feature_columns"]),
            "calibration": bundle["fit"]["calibration"], "training_date_to": bundle["training_date_to"],
            "fit_reservations_by_kind": dict(fit_counts), "reused_head_caches": len(task["reused_heads"]),
            "bundle_sha256": summary["selected_research_bundle_sha256"],
            "source_sha256": verification["source_sha256"], "production_eligible": False,
            "known_retrospective_only": True, "roi_measured": False}
    data = {"root": str(root), "summary": summary, "selection": selection, "verification": verification,
            "development": read_json(root / "development-results.json"),
            "confirmation": read_json(root / "confirmation-results.json"),
            "cycles": engine.decisions(root), "model_card": card}
    markdown = render(data)
    args.report.parent.mkdir(parents=True, exist_ok=True)
    args.report.write_text(markdown, encoding="utf-8")
    write_json(root / "selected-model-card.json", card)
    print(json.dumps({"report": str(args.report), "model_card": str(root / "selected-model-card.json"),
                      "selected": selection["winner"]}, ensure_ascii=False))


if __name__ == "__main__":
    main()
