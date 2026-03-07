"""
Quality Gate: レース入力データの整合チェック
============================================

使用例:
    from keiba_ai.quality_gate import validate_race_entries, filter_valid_races

    result = validate_race_entries(df)
    print(result.summary())

    # 壊れたレースを除外して学習/推論に使う
    df_clean = filter_valid_races(df)

チェック項目（Sランク必須）:
  [Q1] distance == 0 / NaN         → レース条件が取得できていない = 距離帯特徴が全壊
  [Q2] odds 欠損率 >= 80%          → 市場情報が使えない = 最重要特徴が欠落
  [Q3] レース内 distance/venue 揺れ → 同一レース内で値が異なる = スクレイプ結果が壊れている
  [Q4] horse_name 空文字馬の割合   → 表示上の問題（現状は警告のみ: 致命傷ではない）
  [Q5] popularity 欠損率 >= 80%    → オッズと並ぶ重要特徴が欠落
"""

from __future__ import annotations

import warnings
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Set

import pandas as pd
import numpy as np


# === 判定しきい値 ===
DIST_ZERO_THRESHOLD = 0           # distance <= 0 で bad
ODDS_NULL_RATE_THRESHOLD = 0.80   # 80% 以上 null/NaN → bad race
POP_NULL_RATE_THRESHOLD = 0.80    # 80% 以上 null/NaN → bad race
INTRA_RACE_N_UNIQUE_MAX = 1       # 同一レース内で同一であるべき列の許容 unique 数


@dataclass
class RaceIssue:
    """1レース分の問題記録"""
    race_id: str
    issue_code: str        # Q1 / Q2 / Q3 / Q4 / Q5
    severity: str          # ERROR / WARNING
    message: str


@dataclass
class ValidationResult:
    """validate_race_entries の結果"""
    total_races: int = 0
    total_entries: int = 0
    bad_race_ids: Set[str] = field(default_factory=set)   # ERROR レース
    warn_race_ids: Set[str] = field(default_factory=set)  # WARNING レース
    issues: List[RaceIssue] = field(default_factory=list)

    @property
    def n_bad(self) -> int:
        return len(self.bad_race_ids)

    @property
    def n_warn(self) -> int:
        return len(self.warn_race_ids)

    def summary(self) -> str:
        lines = [
            f"[Quality Gate] {self.total_races} races / {self.total_entries} entries",
            f"  ERROR: {self.n_bad} races  WARNING: {self.n_warn} races",
        ]
        for issue in self.issues:
            tag = "❌" if issue.severity == "ERROR" else "⚠ "
            lines.append(f"  {tag} [{issue.issue_code}] {issue.race_id}: {issue.message}")
        return "\n".join(lines)

    def as_dict_list(self) -> List[Dict]:
        return [
            {
                "race_id": i.race_id,
                "issue_code": i.issue_code,
                "severity": i.severity,
                "message": i.message,
            }
            for i in self.issues
        ]


# ============================================================
# メイン関数
# ============================================================

def validate_race_entries(
    df: pd.DataFrame,
    race_id_col: str = "race_id",
    distance_col: str = "distance",
    odds_col: str = "odds",
    popularity_col: str = "popularity",
    venue_col: str = "venue",
    horse_name_col: str = "horse_name",
) -> ValidationResult:
    """DataFrame（馬単位）のレース入力データ品質をチェックする。

    Parameters
    ----------
    df : pd.DataFrame
        馬単位の DataFrame（row = 1 馬 1 レース）
    その他 : str
        各カラム名（存在しない場合はそのチェックをスキップ）

    Returns
    -------
    ValidationResult
    """
    result = ValidationResult()

    if race_id_col not in df.columns:
        # race_id 列がない場合は全体を 1 レースとして扱う
        _df = df.copy()
        _df[race_id_col] = "__single__"
    else:
        _df = df.copy()

    race_ids = _df[race_id_col].unique()
    result.total_races = len(race_ids)
    result.total_entries = len(_df)

    for race_id in race_ids:
        sub = _df[_df[race_id_col] == race_id]

        # ── [Q1] distance == 0 / NaN ─────────────────────────────────────
        if distance_col in sub.columns:
            _dist = pd.to_numeric(sub[distance_col], errors="coerce")
            if (_dist.isna() | (_dist <= DIST_ZERO_THRESHOLD)).all():
                result.bad_race_ids.add(race_id)
                result.issues.append(RaceIssue(
                    race_id=str(race_id),
                    issue_code="Q1",
                    severity="ERROR",
                    message=f"distance が 0 または NaN（{_dist.tolist()[:3]}…）",
                ))

        # ── [Q2] odds 欠損率 ──────────────────────────────────────────────
        if odds_col in sub.columns:
            _odds = pd.to_numeric(sub[odds_col], errors="coerce")
            null_rate = _odds.isna().mean()
            if null_rate >= ODDS_NULL_RATE_THRESHOLD:
                result.bad_race_ids.add(race_id)
                result.issues.append(RaceIssue(
                    race_id=str(race_id),
                    issue_code="Q2",
                    severity="ERROR",
                    message=f"odds 欠損率 {null_rate:.0%}（最重要特徴が使えない）",
                ))
            elif null_rate > 0:
                result.warn_race_ids.add(race_id)
                result.issues.append(RaceIssue(
                    race_id=str(race_id),
                    issue_code="Q2",
                    severity="WARNING",
                    message=f"odds 欠損 {_odds.isna().sum()}/{len(_odds)} 頭",
                ))

        # ── [Q3] レース内 distance/venue 揺れ ───────────────────────────
        intra_check_cols = {
            distance_col: "distance",
            venue_col: "venue",
        }
        for col, label in intra_check_cols.items():
            if col not in sub.columns:
                continue
            vals = sub[col].dropna().astype(str).unique()
            if len(vals) > INTRA_RACE_N_UNIQUE_MAX:
                result.bad_race_ids.add(race_id)
                result.issues.append(RaceIssue(
                    race_id=str(race_id),
                    issue_code="Q3",
                    severity="ERROR",
                    message=f"レース内 {label} が揺れている: {vals.tolist()}",
                ))

        # ── [Q4] horse_name 空文字率（警告のみ）─────────────────────────
        if horse_name_col in sub.columns:
            empty_mask = (
                sub[horse_name_col].isna() |
                (sub[horse_name_col].astype(str).str.strip() == "")
            )
            empty_rate = empty_mask.mean()
            if empty_rate > 0:
                result.warn_race_ids.add(race_id)
                result.issues.append(RaceIssue(
                    race_id=str(race_id),
                    issue_code="Q4",
                    severity="WARNING",
                    message=f"horse_name 空/NaN: {empty_mask.sum()}/{len(sub)} 頭",
                ))

        # ── [Q5] popularity 欠損率 ───────────────────────────────────────
        if popularity_col in sub.columns:
            _pop = pd.to_numeric(sub[popularity_col], errors="coerce")
            null_rate = _pop.isna().mean()
            if null_rate >= POP_NULL_RATE_THRESHOLD:
                result.bad_race_ids.add(race_id)
                result.issues.append(RaceIssue(
                    race_id=str(race_id),
                    issue_code="Q5",
                    severity="ERROR",
                    message=f"popularity 欠損率 {null_rate:.0%}",
                ))

    # warn_race_ids から bad_race_ids を除外（重複抑制）
    result.warn_race_ids -= result.bad_race_ids
    return result


def filter_valid_races(
    df: pd.DataFrame,
    race_id_col: str = "race_id",
    verbose: bool = True,
    **kwargs,
) -> pd.DataFrame:
    """不正なレースを除外した DataFrame を返す。

    Parameters
    ----------
    df : pd.DataFrame
    verbose : bool
        True の場合、除外件数をコンソールに出力する

    Returns
    -------
    pd.DataFrame  （bad_race を除いた DataFrame）
    """
    result = validate_race_entries(df, race_id_col=race_id_col, **kwargs)
    if verbose and result.n_bad > 0:
        print(result.summary())
    if not result.bad_race_ids:
        return df
    mask = ~df[race_id_col].isin(result.bad_race_ids)
    n_removed = (~mask).sum()
    if verbose:
        print(
            f"[Quality Gate] {result.n_bad} 不正レースを除外: "
            f"{n_removed} エントリ → {mask.sum()} エントリ残存"
        )
    return df[mask].reset_index(drop=True)


def check_feature_drift(
    train_df: pd.DataFrame,
    infer_df: pd.DataFrame,
    numeric_cols: Optional[List[str]] = None,
    cat_cols: Optional[List[str]] = None,
    psi_threshold: float = 0.2,
) -> pd.DataFrame:
    """学習データ（train）と推論データ（infer）の特徴量ドリフトを計算する。

    Returns
    -------
    pd.DataFrame  columns: [feature, train_missing_pct, infer_missing_pct,
                             missing_drift, train_median, infer_median,
                             psi, drift_flag]
    """
    if numeric_cols is None:
        numeric_cols = train_df.select_dtypes(include=[np.number]).columns.tolist()

    rows = []
    for col in numeric_cols:
        t = train_df[col].dropna() if col in train_df.columns else pd.Series(dtype=float)
        i = infer_df[col].dropna() if col in infer_df.columns else pd.Series(dtype=float)

        train_miss = (
            train_df[col].isna().mean() if col in train_df.columns else 1.0
        )
        infer_miss = (
            infer_df[col].isna().mean() if col in infer_df.columns else 1.0
        )
        missing_drift = abs(infer_miss - train_miss)

        t_med = float(t.median()) if len(t) else float("nan")
        i_med = float(i.median()) if len(i) else float("nan")

        # PSI（Population Stability Index）を10分位で計算
        psi_val = _calc_psi(t, i) if (len(t) > 10 and len(i) > 0) else float("nan")

        drift_flag = (
            missing_drift > 0.2 or
            (not np.isnan(psi_val) and psi_val >= psi_threshold)
        )
        rows.append({
            "feature": col,
            "train_missing_pct": round(train_miss * 100, 1),
            "infer_missing_pct": round(infer_miss * 100, 1),
            "missing_drift": round(missing_drift * 100, 1),
            "train_median": round(t_med, 4) if not np.isnan(t_med) else None,
            "infer_median": round(i_med, 4) if not np.isnan(i_med) else None,
            "psi": round(psi_val, 4) if not np.isnan(psi_val) else None,
            "drift_flag": drift_flag,
        })

    result_df = pd.DataFrame(rows)
    if len(result_df):
        result_df = result_df.sort_values("psi", ascending=False, na_position="last")
    return result_df


def _calc_psi(expected: pd.Series, actual: pd.Series, n_bins: int = 10) -> float:
    """PSI（Population Stability Index）を計算する。

    PSI < 0.1  : no significant change
    0.1 <= PSI < 0.2 : moderate change (monitor)
    PSI >= 0.2 : significant shift (alert)
    """
    eps = 1e-8
    bins = np.percentile(expected, np.linspace(0, 100, n_bins + 1))
    bins[0] -= eps
    bins[-1] += eps
    # 重複 bin を除去
    bins = np.unique(bins)
    if len(bins) < 2:
        return 0.0

    exp_counts, _ = np.histogram(expected, bins=bins)
    act_counts, _ = np.histogram(actual, bins=bins)

    exp_pct = exp_counts / (exp_counts.sum() + eps)
    act_pct = act_counts / (act_counts.sum() + eps)

    # ゼロ対策
    exp_pct = np.where(exp_pct == 0, eps, exp_pct)
    act_pct = np.where(act_pct == 0, eps, act_pct)

    psi = np.sum((act_pct - exp_pct) * np.log(act_pct / exp_pct))
    return float(psi)
