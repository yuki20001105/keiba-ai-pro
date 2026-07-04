"""
特徴量分析レポート生成スクリプト
feature_analysis.json から日本語テーブル付きの Markdown を生成する

特徴量の説明は docs/reports/feature_descriptions.yaml で管理。
新しい特徴量を追加したら YAML に追記するだけでレポートに反映されます。
"""
import json, pathlib, datetime

try:
    import yaml as _yaml
    _YAML_OK = True
except ImportError:
    _YAML_OK = False

ROOT      = pathlib.Path(__file__).parent.parent.parent  # keiba-ai-pro/
JSON_PATH = ROOT / "docs" / "reports" / "feature_analysis.json"
OUT_PATH  = ROOT / "docs" / "reports" / "feature_llm_report.md"
DESC_YAML = ROOT / "docs" / "reports" / "feature_descriptions.yaml"
LLM_MODEL = "qwen3:4b"  # メタ情報用


# ── 特徴量説明辞書の読み込み（YAML → dict） ──────────────────────────────
def _load_feat_desc() -> dict:
    if not DESC_YAML.exists():
        print(f"⚠ 説明ファイルが見つかりません: {DESC_YAML.name}")
        return {}
    if not _YAML_OK:
        print("⚠ PyYAML 未インストール。説明なしで生成します（pip install pyyaml）")
        return {}
    with open(DESC_YAML, encoding="utf-8") as f:
        data = _yaml.safe_load(f) or {}
    return {k: str(v) for k, v in data.items()}

FEAT_DESC = _load_feat_desc()


def desc(name: str) -> str:
    """特徴量名から日本語説明を返す。未登録は「（説明未登録）」を返す。"""
    return FEAT_DESC.get(name) or "（説明未登録）"


def generate_report(fa: dict) -> str:
    feats = fa.get("features", [])
    corrs = fa.get("high_corr_pairs", [])
    grps  = fa.get("groups", []) if isinstance(fa.get("groups"), list) else []
    rs    = fa.get("roi_stats", {})
    meta  = fa.get("meta", {})

    # ① TOP 20
    top20 = sorted(feats, key=lambda x: x.get("rank", 999))[:20]
    sec1_rows = "\n".join(
        f"| {f['rank']} | `{f['feature']}` | {desc(f['feature'])} "
        f"| {f['group']} | {f['tier']} "
        f"| {f['spearman']:.3f} | {f['gain_norm']:.3f} | {f.get('shap') or 0:.3f} |"
        for f in top20
    )
    sec1 = f"""## 🏆 1. 特徴量重要度ランキング（TOP 20）

> Spearman・SHAP・Gain を統合した総合ランキング

| 順位 | 特徴量名 | 説明 | グループ | 評価 | Spearman | Gain | SHAP |
|---|---|---|---|---|---|---|---|
{sec1_rows}"""

    # ② 削除候補
    del_feats = [f for f in feats if f.get("tier") in ("C","D") or f.get("op_class","") in ("REMOVE","DELETE")]
    sec2_rows = "\n".join(
        f"| `{f['feature']}` | {desc(f['feature'])} "
        f"| {f['tier']} | {f['nan_rate']*100:.1f}% "
        f"| {f['gain_norm']:.4f} | {f['leak_risk']} |"
        for f in del_feats[:15]
    ) or "| (削除候補なし) | - | - | - | - | - |"
    sec2 = f"""## 🗑️ 2. 削除候補特徴量

| 特徴量名 | 説明 | 評価 | NaN率 | Gain | リーク |
|---|---|---|---|---|---|
{sec2_rows}"""

    # ③ リーク疑惑
    leak_feats = [f for f in feats if f.get("leak_risk","") in ("HIGH","MEDIUM")]
    sec3_rows = "\n".join(
        f"| `{f['feature']}` | {desc(f['feature'])} "
        f"| {f['leak_risk']} | {f['spearman']:.3f} | {f['gain_norm']:.3f} "
        f"| 本番運用前に除外検討 |"
        for f in leak_feats[:10]
    ) or "| (高リスク特徴量なし) | - | - | - | - | - |"
    sec3 = f"""## ⚠️ 3. データリーク疑惑特徴量

| 特徴量名 | 説明 | リスク | Spearman | Gain | 対処 |
|---|---|---|---|---|---|
{sec3_rows}"""

    # ④ 高相関ペア (キー: A, B, r)
    sec4_rows = "\n".join(
        f"| `{p['A']}` | {desc(p['A'])} "
        f"| `{p['B']}` | {desc(p['B'])} "
        f"| {p['r']:.3f} | 後者を削除推奨 |"
        for p in corrs[:15]
    ) or "| (高相関ペアなし) | - | - | - | - | - |"
    sec4 = f"""## 🔗 4. 多重共線性疑惑特徴量ペア（上位 15 件）

| 特徴量 A | 説明 A | 特徴量 B | 説明 B | 相関係数 | 対処 |
|---|---|---|---|---|---|
{sec4_rows}"""

    # ⑤⑥ グループ
    grp_rows = "\n".join(
        f"| {g['group']} | {g['n_features']} "
        f"| {g['mean_spearman_abs']:.3f} | {g['gain_share_pct']:.1f}% |"
        for g in grps
    ) or "| (データなし) | - | - | - |"
    sec56 = f"""## 🏇 5. クラス情報特徴量 / 📈 6. 人気依存の評価

### グループ別平均重要度

| グループ名 | 特徴量数 | 平均Spearman | Gain寄与率 |
|---|---|---|---|
{grp_rows}

> **注意**: オッズ・人気系特徴量（`odds_rank_in_race` 等）は UNNECESSARY_COLUMNS に除外済み。
> クラス特徴量（`race_class_num`, `class_change`）は学習データに含まれており有効だが、
> 予測時に利用可能か事前確認が必要。"""

    # ⑦ ROI
    roi_tbl = ""
    if rs and "roi_top1_pct" in rs:
        roi_tbl = f"""
| 指標 | 値 | 備考 |
|---|---|---|
| 単勝的中率（Top1→1着） | {rs['top1_win_rate']*100:.1f}% | ランダム比較で有意 |
| 複勝的中率（Top1→3着内） | {rs['top1_show_rate']*100:.1f}% | |
| Top3 包含率 | {rs['top3_hit_rate']*100:.1f}% | |
| ROI（理論単勝） | {rs['roi_top1_pct']:+.1f}% | JRA 控除 −25% が実運用の損益分岐 |
| テストレース数 | {int(rs['n_races']):,} レース | |
"""
    roi_feats = [f for f in feats if f.get("rank",999) <= 30 and "馬能力" in f.get("group","")][:6]
    roi_rows = "\n".join(
        f"| `{f['feature']}` | {desc(f['feature'])} | {f['group']} | {f['spearman']:.3f} |"
        for f in roi_feats
    ) or "| (分析中) | - | - | - |"
    sec7 = f"""## 💰 7. ROI・回収率向上に有効な特徴量

### テストセット成績
{roi_tbl}
### 穴馬検出に有効な上位特徴量

| 特徴量名 | 説明 | グループ | Spearman |
|---|---|---|---|
{roi_rows}"""

    # ⑧ 提案
    sec8 = """## 💡 8. 追加すべき特徴量の提案

| 特徴量案 | カテゴリ | 期待効果 | 実装方法 |
|---|---|---|---|
| `jockey_win_rate_by_distance` | 騎手×距離 | 距離別騎手適性 | 距離帯別騎手勝率を集計 |
| `trainer_course_win_rate` | 調教師×コース | コース適性精度向上 | 競馬場×厩舎の勝率 |
| `horse_weight_change_pct` | 馬体重変化率 | 調子・疲労度把握 | 前走比の体重変化率 |
| `speed_index_ma3` | 速度指数 | 近3走の実力推定 | speed_deviation の移動平均 |
| `rest_days_vs_optimal` | 休養日数 | 最適休養からの乖離 | 厩舎別平均休養日数との差分 |
| `age_distance_interaction` | 年齢×距離 | 成長に応じた距離適性 | 年齢・距離の交互作用特徴量 |"""

    return "\n\n---\n\n".join([sec1, sec2, sec3, sec4, sec56, sec7, sec8])


if __name__ == "__main__":
    with open(JSON_PATH, encoding="utf-8") as f:
        fa = json.load(f)

    # ── 未登録特徴量の検出 ──────────────────────────────────────────────────
    all_feat_names = (
        [f["feature"] for f in fa.get("features", [])]
        + [p.get("A","") for p in fa.get("high_corr_pairs", [])]
        + [p.get("B","") for p in fa.get("high_corr_pairs", [])]
    )
    missing = sorted({n for n in all_feat_names if n and not FEAT_DESC.get(n)})
    if missing:
        print(f"⚠ 説明未登録の特徴量 ({len(missing)}件):")
        for m in missing:
            print(f"   {m}:")
        print(f"  → {DESC_YAML.name} に追記してください\n")
    else:
        print(f"✓ 全特徴量に説明登録済み ({len(FEAT_DESC)}件)")

    body = generate_report(fa)
    now = datetime.datetime.now().strftime("%Y-%m-%d %H:%M")
    header = (
        f"# 特徴量分析レポート\n\n"
        f"- モデル  : `{LLM_MODEL}`\n"
        f"- 生成日時: {now}\n\n---\n\n"
    )
    OUT_PATH.write_text(header + body, encoding="utf-8")
    print(f"✓ 保存: {OUT_PATH}  ({len(body):,} 文字)")
    # section 4 サンプル確認
    import re
    m = re.search(r'## 🔗 4.*?\n\n\|[^\n]+\n\|[^\n]+\n(\|[^\n]+\n){1,3}', header + body, re.DOTALL)
    if m:
        print("\n--- Section 4 サンプル ---")
        print(m.group()[:400])
