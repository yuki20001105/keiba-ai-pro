from __future__ import annotations

import json
import sqlite3
import sys
import time
from pathlib import Path

import pandas as pd
import psutil

sys.path.insert(0, "keiba")
from keiba_ai.db_ultimate_loader import (  # noqa: E402
    _optimize_dataframe_memory,
    _repo_root_from_db_path,
    load_ultimate_training_frame,
    load_ultimate_training_frame_cached,
)
from keiba_ai.feature_engineering import add_derived_features  # noqa: E402


def _rss_gb() -> float:
    return psutil.Process().memory_info().rss / 1024**3


def _md_table(headers: list[str], rows: list[list[str]]) -> str:
    line1 = "| " + " | ".join(headers) + " |"
    line2 = "|" + "|".join(["---" for _ in headers]) + "|"
    body = ["| " + " | ".join(r) + " |" for r in rows]
    return "\n".join([line1, line2] + body)


def _check_and_optimize_indexes(db_path: Path) -> dict:
    conn = sqlite3.connect(str(db_path))
    cur = conn.cursor()

    cur.execute("SELECT name, sql FROM sqlite_master WHERE type='index'")
    before_idx = {r[0]: (r[1] or "") for r in cur.fetchall()}

    index_sqls = {
        "idx_rr_race_id": "CREATE INDEX IF NOT EXISTS idx_rr_race_id ON race_results_ultimate(race_id)",
        "idx_ru_race_id": "CREATE INDEX IF NOT EXISTS idx_ru_race_id ON races_ultimate(race_id)",
        "idx_rr_horse_id": "CREATE INDEX IF NOT EXISTS idx_rr_horse_id ON race_results_ultimate(json_extract(data, '$.horse_id'))",
        "idx_rr_jockey_id": "CREATE INDEX IF NOT EXISTS idx_rr_jockey_id ON race_results_ultimate(json_extract(data, '$.jockey_id'))",
        "idx_rr_trainer_id": "CREATE INDEX IF NOT EXISTS idx_rr_trainer_id ON race_results_ultimate(json_extract(data, '$.trainer_id'))",
        "idx_rr_date": "CREATE INDEX IF NOT EXISTS idx_rr_date ON race_results_ultimate(substr(race_id, 1, 8))",
        "idx_ru_date": "CREATE INDEX IF NOT EXISTS idx_ru_date ON races_ultimate(json_extract(data, '$.date'))",
    }

    t0_create = time.perf_counter()
    for sql in index_sqls.values():
        cur.execute(sql)
    conn.commit()
    create_sec = time.perf_counter() - t0_create

    t0_analyze = time.perf_counter()
    cur.execute("ANALYZE")
    conn.commit()
    analyze_sec = time.perf_counter() - t0_analyze

    t0_vacuum = time.perf_counter()
    cur.execute("VACUUM")
    conn.commit()
    vacuum_sec = time.perf_counter() - t0_vacuum

    cur.execute("SELECT name, sql FROM sqlite_master WHERE type='index'")
    after_idx = {r[0]: (r[1] or "") for r in cur.fetchall()}
    conn.close()

    required_presence = {
        "race_id": any("race_id" in s.lower() for s in after_idx.values()),
        "horse_id": any("horse_id" in s.lower() for s in after_idx.values()),
        "jockey_id": any("jockey_id" in s.lower() for s in after_idx.values()),
        "trainer_id": any("trainer_id" in s.lower() for s in after_idx.values()),
        "date": any("date" in s.lower() or "substr(race_id" in s.lower() for s in after_idx.values()),
    }

    return {
        "created_index_count": len(set(after_idx.keys()) - set(before_idx.keys())),
        "create_index_sec": round(create_sec, 3),
        "analyze_sec": round(analyze_sec, 3),
        "vacuum_sec": round(vacuum_sec, 3),
        "required_presence": required_presence,
        "index_names": sorted(after_idx.keys()),
    }


def _bench_polars_vs_pandas(df: pd.DataFrame) -> dict:
    result = {"polars_available": False}

    # pandas benchmark
    p0 = time.perf_counter()
    p_mem0 = _rss_gb()
    tmp = df[["race_id", "odds", "distance"]].copy()
    tmp["odds"] = pd.to_numeric(tmp["odds"], errors="coerce")
    agg = tmp.groupby("race_id", sort=False, as_index=False)["odds"].mean().rename(columns={"odds": "odds_mean"})
    merged = tmp.merge(agg, on="race_id", how="left")
    merged = merged.sort_values(["distance", "odds_mean"], kind="mergesort")
    p_sec = time.perf_counter() - p0
    p_mem1 = _rss_gb()

    result["pandas_sec"] = round(p_sec, 3)
    result["pandas_rss_delta_gb"] = round(max(0.0, p_mem1 - p_mem0), 3)

    try:
        import polars as pl

        result["polars_available"] = True
        q0 = time.perf_counter()
        q_mem0 = _rss_gb()
        pl_df = pl.from_pandas(tmp)
        pl_agg = pl_df.group_by("race_id").agg(pl.col("odds").mean().alias("odds_mean"))
        pl_merged = pl_df.join(pl_agg, on="race_id", how="left").sort(["distance", "odds_mean"])
        _ = pl_merged.shape
        q_sec = time.perf_counter() - q0
        q_mem1 = _rss_gb()
        result["polars_sec"] = round(q_sec, 3)
        result["polars_rss_delta_gb"] = round(max(0.0, q_mem1 - q_mem0), 3)
    except Exception as e:
        result["polars_error"] = f"{type(e).__name__}: {e}"

    return result


def main() -> int:
    root = Path(__file__).resolve().parents[1]
    db_path = root / "keiba" / "data" / "keiba_ultimate.db"

    perf: dict = {}

    # 1) Baseline load (no memory optimization)
    rss0 = _rss_gb()
    t0 = time.perf_counter()
    df_base = load_ultimate_training_frame(db_path, profile=True, optimize_memory=False)
    base_sec = time.perf_counter() - t0
    rss1 = _rss_gb()
    perf["load_baseline"] = {
        "elapsed_sec": round(base_sec, 3),
        "rss_before_gb": round(rss0, 3),
        "rss_after_gb": round(rss1, 3),
        "shape": [int(df_base.shape[0]), int(df_base.shape[1])],
        "memory_mb": round(float(df_base.memory_usage(deep=True).sum() / 1024**2), 2),
        "stage_profile": df_base.attrs.get("stage_profile", {}),
    }

    # 2) Memory optimization comparison
    t1 = time.perf_counter()
    df_opt, mem_stats = _optimize_dataframe_memory(df_base.copy())
    mem_opt_sec = time.perf_counter() - t1
    perf["memory_optimization"] = {
        "elapsed_sec": round(mem_opt_sec, 3),
        **mem_stats,
    }

    # 3) Cache strategy benchmark (miss/hit)
    cache_dir = _repo_root_from_db_path(db_path) / "cache"
    parquet_path = cache_dir / "training_frame.parquet"
    pkl_path = cache_dir / "training_frame.pkl"

    if parquet_path.exists():
        parquet_path.unlink()
    if pkl_path.exists():
        pkl_path.unlink()

    t2 = time.perf_counter()
    _ = load_ultimate_training_frame_cached(
        db_path,
        cache_dir=cache_dir,
        profile=False,
        optimize_memory=True,
        prefer_parquet=False,
    )
    cache_miss_sec = time.perf_counter() - t2

    t3 = time.perf_counter()
    _ = load_ultimate_training_frame_cached(
        db_path,
        cache_dir=cache_dir,
        profile=False,
        optimize_memory=True,
        prefer_parquet=False,
    )
    cache_hit_sec = time.perf_counter() - t3

    perf["cache"] = {
        "cache_miss_sec": round(cache_miss_sec, 3),
        "cache_hit_sec": round(cache_hit_sec, 3),
        "parquet_exists": parquet_path.exists(),
        "pickle_exists": pkl_path.exists(),
    }

    # 4) add_derived_features timing (sampled for reliable completion)
    sample_n = min(20_000, len(df_base))
    df_feat_src = df_base.head(sample_n).copy()
    t4 = time.perf_counter()
    _ = add_derived_features(df_feat_src, full_history_df=df_base)
    feature_sec = time.perf_counter() - t4
    perf["feature_creation"] = {
        "sample_rows": int(sample_n),
        "add_derived_features_sec": round(feature_sec, 3),
    }

    # 5) SQLite index / analyze / vacuum
    perf["sqlite_optimization"] = _check_and_optimize_indexes(db_path)

    # 6) Polars possibility
    perf["polars_benchmark"] = _bench_polars_vs_pandas(df_opt)

    # Persist raw metrics
    metrics_path = root / "docs" / "reports" / "performance_metrics.json"
    metrics_path.parent.mkdir(parents=True, exist_ok=True)
    metrics_path.write_text(json.dumps(perf, ensure_ascii=False, indent=2), encoding="utf-8")

    # performance_report.md
    perf_rows = [
        ["load_ultimate_training_frame (baseline)", str(perf["load_baseline"]["elapsed_sec"])],
        ["load_ultimate_training_frame_cached (miss)", str(perf["cache"]["cache_miss_sec"])],
        ["load_ultimate_training_frame_cached (hit)", str(perf["cache"]["cache_hit_sec"])],
        [
            f"add_derived_features (sample={perf['feature_creation']['sample_rows']:,} rows)",
            str(perf["feature_creation"]["add_derived_features_sec"]),
        ],
    ]
    stage = perf["load_baseline"]["stage_profile"]
    stage_rows = [[k, str(v)] for k, v in stage.items()]

    pol = perf["polars_benchmark"]
    pol_rows = [["pandas", str(pol.get("pandas_sec", "n/a")), str(pol.get("pandas_rss_delta_gb", "n/a"))]]
    if pol.get("polars_available"):
        pol_rows.append(["polars", str(pol.get("polars_sec", "n/a")), str(pol.get("polars_rss_delta_gb", "n/a"))])

    pr = [
        "# performance_report",
        "",
        "## 処理時間比較（秒）",
        "",
        _md_table(["処理", "時間(sec)"], perf_rows),
        "",
        "## load_ultimate_training_frame ステージ内訳（秒）",
        "",
        _md_table(["ステージ", "時間(sec)"], stage_rows),
        "",
        "## Polars化可能性（簡易ベンチ）",
        "",
        _md_table(["実装", "時間(sec)", "RSS増分(GB)"], pol_rows),
        "",
    ]
    if not pol.get("polars_available"):
        pr.extend([
            "Polarsは未インストールまたは実行不可でした。",
            f"- reason: {pol.get('polars_error', 'not available')}",
            "",
        ])
    pr.extend([
        "## 目標との差分",
        "",
        f"- load_ultimate_training_frame 目標 <= 10 sec: 実測 {perf['load_baseline']['elapsed_sec']} sec",
        f"- RSS 目標 <= 2 GB: 実測 after {perf['load_baseline']['rss_after_gb']} GB",
        "",
        f"raw metrics: {metrics_path.as_posix()}",
    ])
    (root / "performance_report.md").write_text("\n".join(pr) + "\n", encoding="utf-8")

    # memory_optimization_report.md
    ms = perf["memory_optimization"]
    mr = [
        "# memory_optimization_report",
        "",
        "## DataFrameメモリ最適化結果",
        "",
        _md_table(
            ["項目", "値"],
            [
                ["before_mb", str(ms["before_mb"])],
                ["after_mb", str(ms["after_mb"])],
                ["reduction_mb", str(ms["reduction_mb"])],
                ["reduction_pct", str(ms["reduction_pct"])],
                ["int_downcast_cols", str(ms["int_downcast_cols"])],
                ["float_downcast_cols", str(ms["float_downcast_cols"])],
                ["object_to_category_cols", str(ms["object_to_category_cols"])],
            ],
        ),
        "",
        "適用内容:",
        "- int64 -> pd.to_numeric(..., downcast='integer')",
        "- float64 -> pd.to_numeric(..., downcast='float')",
        "- object -> astype('category') (変換不可列はスキップ)",
    ]
    (root / "memory_optimization_report.md").write_text("\n".join(mr) + "\n", encoding="utf-8")

    # cache_strategy.md
    so = perf["sqlite_optimization"]
    cs = [
        "# cache_strategy",
        "",
        "## 中間キャッシュ方針",
        "",
        "1. cache/ 以下に Notebook監査専用のparquetキャッシュを保持",
        "2. キー不一致時はキャッシュを無効化して再生成",
        "3. それ以外はキャッシュを優先利用",
        "",
        "対象ファイル:",
        "- cache/ultimate_frame.parquet",
        "- cache/features.parquet",
        "- cache/predictions.parquet",
        "- cache/race_results.parquet",
        "- cache/horse_history.parquet",
        "- cache/training_data.parquet",
        "",
        "キャッシュキー:",
        "- data_version",
        "- feature_schema_hash",
        "- notebook_step",
        "- mode",
        "",
        "invalidation:",
        "- schema変更",
        "- DB更新",
        "- mode変更",
        "",
        "## 実測",
        "",
        _md_table(
            ["項目", "値"],
            [
                ["cache_miss_sec", str(perf["cache"]["cache_miss_sec"])],
                ["cache_hit_sec", str(perf["cache"]["cache_hit_sec"])],
                ["parquet_exists", str(perf["cache"]["parquet_exists"])],
                ["pickle_exists", str(perf["cache"]["pickle_exists"])],
            ],
        ),
        "",
        "## SQLite高速化（実施結果）",
        "",
        _md_table(
            ["項目", "値"],
            [
                ["create_index_sec", str(so["create_index_sec"])],
                ["analyze_sec", str(so["analyze_sec"])],
                ["vacuum_sec", str(so["vacuum_sec"])],
                ["created_index_count", str(so["created_index_count"])],
            ],
        ),
        "",
        "必須インデックス存在確認:",
        f"- race_id: {so['required_presence']['race_id']}",
        f"- horse_id: {so['required_presence']['horse_id']}",
        f"- jockey_id: {so['required_presence']['jockey_id']}",
        f"- trainer_id: {so['required_presence']['trainer_id']}",
        f"- date: {so['required_presence']['date']}",
    ]
    (root / "cache_strategy.md").write_text("\n".join(cs) + "\n", encoding="utf-8")

    print("generated: performance_report.md, memory_optimization_report.md, cache_strategy.md")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
