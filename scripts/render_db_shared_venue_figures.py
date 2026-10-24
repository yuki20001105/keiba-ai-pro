"""Static scientific plots of a completed fixed venue study; no fitting."""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
from matplotlib import font_manager
import numpy as np
import pandas as pd


ROOT = Path(__file__).resolve().parents[1]
STUDY_ROOT = ROOT / "reports/generated/venue-model-evaluation"
ORDER = ["札幌", "函館", "福島", "新潟", "東京", "中山", "中京", "京都", "阪神", "小倉"]
NAMES = {"global": "全場共通", "venue_only": "競馬場別", "blend50": "50％混合"}
WINDOWS = {"expanding": "過去全期間で学習", "rolling5": "直近5年で学習"}
COLORS = {"global": "#2563eb", "venue_only": "#e8790d", "blend50": "#059669"}


def render(run: Path):
    run = run.resolve(strict=True)
    if not run.is_relative_to(STUDY_ROOT.resolve()):
        raise ValueError("venue-figure-output-must-stay-in-study")
    source = run / "results.json"
    manifest = json.loads((run / "artifact_manifest.json").read_text(encoding="utf-8"))
    if hashlib.sha256(source.read_bytes()).hexdigest() != manifest["files"]["results.json"]:
        raise ValueError("venue-result-artifact-hash-mismatch")
    result = json.loads(source.read_text(encoding="utf-8"))
    if result["status"] != "completed" or not result["preservation_unchanged"]:
        raise ValueError("completed-preserved-venue-result-required")
    font_path = Path("C:/Windows/Fonts/meiryo.ttc")
    if font_path.exists():
        font_manager.fontManager.addfont(str(font_path))
        plt.rcParams["font.family"] = font_manager.FontProperties(fname=str(font_path)).get_name()
    plt.rcParams.update({"font.size": 10, "axes.unicode_minus": False})
    rows = pd.DataFrame(result["yearly_summaries"])
    overall = rows.loc[rows.scope.eq("overall")]
    fig, axes = plt.subplots(2, 2, figsize=(13, 7.5), constrained_layout=True)
    for i, (window, title) in enumerate(WINDOWS.items()):
        for j, (metric, label) in enumerate([("rank_correlation", "順位相関  高いほど良い"),
                                             ("rmse_speed_mps", "RMSE m/s  低いほど良い")]):
            axis = axes[i, j]
            for strategy, name in NAMES.items():
                group = overall.loc[overall.window.eq(window) & overall.strategy.eq(strategy)].sort_values("year")
                axis.plot(group.year, group[metric], marker="o", label=name, color=COLORS[strategy], linewidth=1.7)
            axis.set_title(title + "  " + label)
            axis.set_xticks(range(2016, 2027), [str(year) + ("*" if year == 2026 else "") for year in range(2016, 2027)], rotation=35)
            axis.grid(alpha=.2)
            axis.legend(fontsize=9)
    fig.suptitle("競馬場別モデルの年次評価  2016〜2026年\n2026*は9月13日まで  同じレースと47特徴量で比較", fontsize=14)
    outputs = []
    destination = run / "yearly-accuracy-v2.png"
    if destination.exists():
        raise FileExistsError(destination)
    fig.savefig(destination, dpi=150)
    plt.close(fig)
    outputs.append(str(destination))
    venues = rows.loc[rows.scope.eq("venue")]
    for metric, label, scale, palette in [
        ("delta_rank_correlation_vs_global", "順位相関の差  プラスが改善", .10, "RdYlGn"),
        ("delta_rmse_speed_mps_vs_global", "RMSEの差 m/s  マイナスが改善", .10, "RdYlGn_r"),
    ]:
        fig, axes = plt.subplots(2, 2, figsize=(16, 8.5), constrained_layout=True)
        precision = 3 if "rank" in metric else 4
        for i, (window, title) in enumerate(WINDOWS.items()):
            for j, strategy in enumerate(["venue_only", "blend50"]):
                selected = venues.loc[venues.window.eq(window) & venues.strategy.eq(strategy)]
                table = selected.pivot(index="venue", columns="year", values=metric).reindex(index=ORDER, columns=range(2016, 2027))
                image = axes[i, j].imshow(np.ma.masked_invalid(table.to_numpy()), cmap=palette, vmin=-scale, vmax=scale, aspect="auto")
                axes[i, j].set_yticks(range(len(ORDER)), ORDER)
                axes[i, j].set_xticks(range(11), [str(year) + ("*" if year == 2026 else "") for year in range(2016, 2027)], rotation=35)
                axes[i, j].set_title(title + "  " + NAMES[strategy])
                for row in range(len(ORDER)):
                    for column in range(11):
                        value = table.iloc[row, column]
                        label_value = "—" if pd.isna(value) else f"{value:+.{precision}f}"
                        axes[i, j].text(column, row, label_value, ha="center", va="center", fontsize=7)
        fig.colorbar(image, ax=axes, shrink=.75)
        fig.suptitle("全場共通との差  " + label + "\n空欄は開催データなし  小標本の競馬場は共通へ戻す  場所別は探索的評価", fontsize=13)
        destination = run / ("venue-rank-difference-v2.png" if "rank" in metric else "venue-rmse-difference-v2.png")
        if destination.exists():
            raise FileExistsError(destination)
        fig.savefig(destination, dpi=150)
        plt.close(fig)
        outputs.append(str(destination))
    print(json.dumps({"plots": outputs}, ensure_ascii=False))


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run", type=Path, required=True)
    render(parser.parse_args().run)
