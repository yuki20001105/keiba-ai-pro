"""Read-only tabular views and CSV export for the locally acquired race data."""
from __future__ import annotations

import asyncio
import csv
import io
import json
import math
import re
import sqlite3
import threading
import time
from pathlib import Path
from typing import Any, Iterator, Literal
from urllib.parse import quote

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import StreamingResponse

from app_config import ULTIMATE_DB  # type: ignore
from deps.auth import require_premium  # type: ignore

router = APIRouter()
Dataset = Literal["races", "horses"]
TABLES = {"races": "races_ultimate", "horses": "race_results_ultimate"}
DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
_CSV_CHUNK_ROWS = 500
_CSV_MAX_ROWS = 200_000
_FEATURE_CACHE_ROOT = Path(__file__).resolve().parents[1] / "data" / "shared_feature_cache"
_FEATURE_PREVIEW_LOCK = threading.Lock()
_SPEED_BASELINE_CACHE: dict[tuple[str, str], tuple[dict[str, Any] | None, dict[str, Any]]] = {}
_SPEED_BASELINE_LOOKBACK_DAYS = 365
FeaturePipeline = Literal[
    "local-db-shared-v1",
    "local-db-shared-optimized-v1",
    "local-db-context-v1",
]
FeatureTarget = Literal["speed_deviation", "win", "place3", "rank"]


def _date_prefix(value: str | None, name: str) -> str | None:
    if not value:
        return None
    if not DATE_RE.fullmatch(value):
        raise HTTPException(status_code=400, detail=f"{name} は YYYY-MM-DD 形式で指定してください")
    return value.replace("-", "")


def _connect_readonly() -> sqlite3.Connection:
    """Open the same SQLite database written by scraping.storage without creating it."""
    path = Path(ULTIMATE_DB).resolve()
    if not path.is_file():
        raise HTTPException(status_code=503, detail="取得データの保存先データベースが見つかりません")
    try:
        connection = sqlite3.connect(f"{path.as_uri()}?mode=ro", uri=True, timeout=10)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA query_only=ON")
        return connection
    except sqlite3.Error as exc:
        raise HTTPException(status_code=503, detail="取得データベースを読み込めません") from exc


def _ensure_tables(connection: sqlite3.Connection) -> None:
    tables = {str(row[0]) for row in connection.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    if not set(TABLES.values()).issubset(tables):
        raise HTTPException(status_code=503, detail="取得データのテーブルがまだ準備されていません")


def _filters(from_date: str | None, to_date: str | None) -> tuple[str, list[str]]:
    start = _date_prefix(from_date, "開始日")
    end = _date_prefix(to_date, "終了日")
    if start and end and start > end:
        raise HTTPException(status_code=400, detail="開始日は終了日以前の日付にしてください")
    clauses: list[str] = []
    params: list[str] = []
    if start:
        clauses.append("substr(race_id, 1, 8) >= ?")
        params.append(start)
    if end:
        clauses.append("substr(race_id, 1, 8) <= ?")
        params.append(end)
    return (" WHERE " + " AND ".join(clauses) if clauses else "", params)


def _columns(connection: sqlite3.Connection, table: str, where: str, params: list[str]) -> list[str]:
    """Discover the union of keys for an explicit export (not the interactive page)."""
    try:
        found = [str(row[0]) for row in connection.execute(
            f"SELECT DISTINCT j.key FROM {table} AS source, json_each(source.data) AS j{where} ORDER BY j.key",
            params,
        ) if isinstance(row[0], str)]
    except sqlite3.Error as exc:
        raise HTTPException(status_code=500, detail="データ項目の一覧を読み取れません") from exc
    return ["race_id", *[column for column in found if column != "race_id"]]


def _as_record(race_id: str, data: str) -> dict[str, Any]:
    try:
        payload = json.loads(data)
    except (TypeError, json.JSONDecodeError):
        payload = {}
    record = dict(payload) if isinstance(payload, dict) else {"data": payload}
    record["race_id"] = race_id
    return record


def _counts(connection: sqlite3.Connection, where: str, params: list[str]) -> tuple[int, int]:
    race_count = int(connection.execute(f"SELECT COUNT(*) FROM races_ultimate{where}", params).fetchone()[0])
    horse_count = int(connection.execute(f"SELECT COUNT(*) FROM race_results_ultimate{where}", params).fetchone()[0])
    return race_count, horse_count


def _dataset_count(dataset: Dataset, from_date: str | None, to_date: str | None) -> int:
    where, params = _filters(from_date, to_date)
    connection = _connect_readonly()
    try:
        _ensure_tables(connection)
        return int(connection.execute(f"SELECT COUNT(*) FROM {TABLES[dataset]}{where}", params).fetchone()[0])
    finally:
        connection.close()


def _page(dataset: Dataset, from_date: str | None, to_date: str | None, limit: int, offset: int) -> dict[str, Any]:
    where, params = _filters(from_date, to_date)
    table = TABLES[dataset]
    connection = _connect_readonly()
    try:
        _ensure_tables(connection)
        race_count, horse_count = _counts(connection, where, params)
        if dataset == "races":
            query = f"SELECT race_id, data FROM {table}{where} ORDER BY race_id DESC LIMIT ? OFFSET ?"
        else:
            query = (
                f"SELECT race_id, data FROM {table}{where} "
                "ORDER BY race_id DESC, CAST(COALESCE(json_extract(data,'$.horse_number'), "
                "json_extract(data,'$.horse_num')) AS INTEGER), id LIMIT ? OFFSET ?"
            )
        rows = connection.execute(query, [*params, limit, offset]).fetchall()
        records = [_as_record(str(row["race_id"]), row["data"]) for row in rows]
        # Derive columns from the page currently shown. Scanning every JSON blob
        # in the multi-gigabyte DB on each page turn would make the viewer stall.
        columns = sorted({"race_id", *(key for record in records for key in record)})
        return {
            "dataset": dataset,
            "columns": columns,
            "records": records,
            "total_rows": race_count if dataset == "races" else horse_count,
            "race_count": race_count,
            "horse_count": horse_count,
            "limit": limit,
            "offset": offset,
        }
    except HTTPException:
        raise
    except sqlite3.Error as exc:
        raise HTTPException(status_code=500, detail="取得データを読み込めません") from exc
    finally:
        connection.close()


def _csv_cell(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, float) and not math.isfinite(value):
        return ""
    if isinstance(value, (dict, list)):
        result = json.dumps(value, ensure_ascii=False, separators=(",", ":"))
    else:
        result = str(value)
    # Prevent untrusted scraped text from being interpreted as a spreadsheet formula.
    if result.lstrip().startswith(("=", "+", "-", "@")):
        return "'" + result
    return result


def _csv_stream(dataset: Dataset, from_date: str | None, to_date: str | None) -> Iterator[str]:
    where, params = _filters(from_date, to_date)
    table = TABLES[dataset]
    connection = _connect_readonly()
    try:
        _ensure_tables(connection)
        columns = _columns(connection, table, where, params)
        if dataset == "races":
            query = f"SELECT race_id, data FROM {table}{where} ORDER BY race_id"
        else:
            query = (
                f"SELECT race_id, data FROM {table}{where} "
                "ORDER BY race_id, CAST(COALESCE(json_extract(data,'$.horse_number'), "
                "json_extract(data,'$.horse_num')) AS INTEGER), id"
            )
        buffer = io.StringIO(newline="")
        writer = csv.writer(buffer)
        writer.writerow(columns)
        yield "\ufeff" + buffer.getvalue()
        buffer.seek(0)
        buffer.truncate(0)
        for row_index, row in enumerate(connection.execute(query, params), start=1):
            record = _as_record(str(row["race_id"]), row["data"])
            writer.writerow([_csv_cell(record.get(column)) for column in columns])
            if row_index % _CSV_CHUNK_ROWS == 0:
                yield buffer.getvalue()
                buffer.seek(0)
                buffer.truncate(0)
        if buffer.tell():
            yield buffer.getvalue()
    except HTTPException:
        raise
    except sqlite3.Error as exc:
        raise HTTPException(status_code=500, detail="CSVを書き出せません") from exc
    finally:
        connection.close()


def _feature_inputs(date_from: str, date_to: str, pipeline: FeaturePipeline):
    """Build/reuse the selected trainer's raw feature matrix from a read-only DB snapshot."""
    if not re.fullmatch(r"\d{4}-\d{2}", date_from or "") or not re.fullmatch(r"\d{4}-\d{2}", date_to or ""):
        raise HTTPException(status_code=400, detail="学習前特徴量は開始年月と終了年月を指定してください")
    if date_from > date_to:
        raise HTTPException(status_code=400, detail="開始年月は終了年月以前にしてください")

    from training.db_context_registry import CONTEXT_PIPELINE, fit_module
    from training.db_shared_cache import prepare_shared_inputs
    from training.db_shared_fit import source_hashes as shared_source_hashes

    profile = fit_module(pipeline)
    source = Path(ULTIMATE_DB).resolve()
    if not source.is_file():
        raise HTTPException(status_code=503, detail="学習データの保存先が見つかりません")
    started = time.perf_counter()
    try:
        # Use the same lineage-checked feature cache as the training workflow.
        # Source SQLite is opened read-only by the cache's preparation functions.
        with _FEATURE_PREVIEW_LOCK:
            if pipeline == CONTEXT_PIPELINE:
                target, features, coverage, receipt = profile.prepare_inputs(
                    source, date_from, date_to, _FEATURE_CACHE_ROOT,
                    expected_source_hashes=profile.source_hashes(), reuse_features=True,
                )
            else:
                target, features, coverage, receipt = prepare_shared_inputs(
                    source, date_from, date_to, _FEATURE_CACHE_ROOT,
                    expected_source_hashes=shared_source_hashes(), reuse_features=True,
                )
                if pipeline == "local-db-shared-optimized-v1":
                    features = profile.select_features(features)
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"学習前特徴量を準備できません: {exc}") from exc
    elapsed = time.perf_counter() - started
    return target, features, coverage, receipt, profile, elapsed


def _speed_deviation_target_values(target, date_from: str, receipt: dict[str, Any]):
    """Build preview labels from eligible, strictly prior historical results."""
    from training.db_shared_fit import apply_speed_deviation_baseline, fit_speed_deviation_baseline
    from training.db_shared_features import load_speed_baseline_frame

    snapshot_sha = str(receipt.get("snapshot_sha256") or "unverified-snapshot")
    cache_key = (snapshot_sha, date_from)
    with _FEATURE_PREVIEW_LOCK:
        cached = _SPEED_BASELINE_CACHE.get(cache_key)
        if cached is None:
            training_rows, provenance = load_speed_baseline_frame(
                ULTIMATE_DB, date_from, lookback_days=_SPEED_BASELINE_LOOKBACK_DAYS,
            )
            provenance = {name: value for name, value in provenance.items() if name != "coverage"}
            try:
                baseline = fit_speed_deviation_baseline(training_rows)
                status = {**provenance, "available": True,
                          "training_sample_count": int(baseline["training_sample_count"])}
            except ValueError:
                baseline = None
                status = {**provenance, "available": False,
                          "reason": "prior-history-does-not-contain-enough-valid-speed-results"}
            cached = (baseline, status)
            _SPEED_BASELINE_CACHE[cache_key] = cached
            while len(_SPEED_BASELINE_CACHE) > 4:
                _SPEED_BASELINE_CACHE.pop(next(iter(_SPEED_BASELINE_CACHE)))

    baseline, status = cached
    if baseline is None:
        import pandas as pd
        return pd.Series(float("nan"), index=target.index, dtype=float), status
    return apply_speed_deviation_baseline(target, baseline), status


def _feature_missingness_summary(target, features) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Summarize missing feature cells by field and year without guessing causes.

    Only a feature's own saved-history counter can establish a history gap.
    Even that means "no eligible saved history", not a verified career debut.
    Other missing values remain explicitly unclassified pending source review.
    """
    import pandas as pd

    years = target["race_date"].astype("string").str.slice(0, 4).fillna("不明")
    overall: list[dict[str, Any]] = []
    by_year: list[dict[str, Any]] = []
    year_groups = [(str(year), index) for year, index in years.groupby(years).groups.items()]

    def history_requirement(name: str) -> tuple[str, int] | None:
        if name in {"safe_days_since_last", "safe_distance_change"}:
            return "safe_prior_starts", 1
        previous = re.fullmatch(r"safe_prev(2?)_(?:finish|time_seconds|distance|speed_mps)", name)
        if previous:
            return "safe_prior_starts", 2 if previous.group(1) else 1
        window = re.fullmatch(r"safe_(past\d+)_.*", name)
        if window:
            return f"safe_{window.group(1)}_count", 1
        group = re.fullmatch(r"safe_(horse_(?:surface|distance_band|course)|jockey|trainer|sire|damsire)_.*", name)
        if group:
            return f"safe_{group.group(1)}_starts", 1
        return None

    for name in features.columns:
        missing = features[name].isna()
        missing_count = int(missing.sum())
        structural = 0
        requirement = history_requirement(str(name))
        if requirement and requirement[0] in features:
            counter, minimum = requirement
            values = pd.to_numeric(features[counter], errors="coerce")
            structural = int((missing & values.notna() & values.ge(0) & values.lt(minimum)).sum())
        base = {
            "feature": str(name), "row_count": int(len(features)),
            "missing_count": missing_count,
            "missing_fraction": float(missing.mean()) if len(features) else 0.0,
            "all_missing": bool(len(features) > 0 and missing_count == len(features)),
            "saved_history_gap_count": structural,
            "unclassified_count": missing_count - structural,
        }
        overall.append(base)
        for year, indices in year_groups:
            local = missing.loc[indices]
            local_count = int(local.sum())
            if not local_count:
                continue
            local_structural = 0
            if requirement and requirement[0] in features:
                counter_values = pd.to_numeric(features.loc[indices, requirement[0]], errors="coerce")
                local_structural = int((local & counter_values.notna() & counter_values.ge(0)
                                        & counter_values.lt(requirement[1])).sum())
            by_year.append({
                "year": year, "feature": str(name), "row_count": int(len(indices)),
                "missing_count": local_count, "missing_fraction": float(local.mean()),
                "saved_history_gap_count": local_structural,
                "unclassified_count": local_count - local_structural,
            })
    return overall, by_year


def _feature_records(
    date_from: str,
    date_to: str,
    pipeline: FeaturePipeline,
    target_name: FeatureTarget,
    test_size: float,
    limit: int,
    offset: int,
) -> dict[str, Any]:
    if not 0.10 <= test_size <= 0.50:
        raise HTTPException(status_code=400, detail="評価データの割合は10%から50%で指定してください")
    target, features, coverage, receipt, _profile, elapsed = _feature_inputs(date_from, date_to, pipeline)
    if len(target) != len(features) or not target.index.equals(features.index):
        raise HTTPException(status_code=500, detail="特徴量と学習対象の行が一致しません")

    target_baseline = None
    if target_name == "speed_deviation":
        target_values, target_baseline = _speed_deviation_target_values(target, date_from, receipt)
    elif target_name == "win":
        target_values = (target["finish"].astype(float) == 1).astype("int8")
    elif target_name == "place3":
        target_values = (target["finish"].astype(float) <= 3).astype("int8")
    else:
        finish = target["finish"].astype(float)
        field_size = target["num_horses"].astype(float)
        target_values = (field_size - finish + 1).clip(lower=0).fillna(0).astype("int16")

    metadata_columns = [
        "race_date", "race_id", "venue", "distance", "surface", "num_horses",
        "horse_id", "horse_number", "finish", "odds", "popularity",
    ]
    metadata_columns = [name for name in metadata_columns if name in target.columns]
    selected_target = target.loc[:, metadata_columns].copy()
    selected_target.insert(len(selected_target.columns), "target_value", target_values.to_numpy())
    page_features = features.iloc[offset:offset + limit].reset_index(drop=True)
    page_target = selected_target.iloc[offset:offset + limit].reset_index(drop=True)
    records_frame = page_target.join(page_features)
    records = records_frame.astype(object).where(records_frame.notna(), None).to_dict(orient="records")
    missing_summary, missing_summary_by_year = _feature_missingness_summary(target, features)
    race_count = int(target["race_id"].nunique()) if "race_id" in target else 0
    return {
        "dataset": "features",
        "pipeline": pipeline,
        "target": target_name,
        "columns": list(records_frame.columns),
        "records": records,
        "total_rows": len(target),
        "race_count": race_count,
        "horse_count": len(target),
        "feature_count": len(features.columns),
        "limit": limit,
        "offset": offset,
        "requested_period": [date_from, date_to],
        "actual_period": [coverage.get("target", {}).get("actual_date_from"), coverage.get("target", {}).get("actual_date_to")],
        "cache_hit": bool(receipt.get("cache_hit")),
        "preparation_seconds": round(elapsed, 2),
        "point_in_time_validated": bool(receipt.get("point_in_time_validated")),
        "target_baseline": target_baseline,
        "missing_summary": missing_summary,
        "missing_summary_by_year": missing_summary_by_year,
    }


def _feature_csv_stream(date_from: str, date_to: str, pipeline: FeaturePipeline, target_name: FeatureTarget,
                        test_size: float, prepared=None) -> Iterator[str]:
    target, features, _coverage, receipt, _profile, _elapsed = prepared or _feature_inputs(
        date_from, date_to, pipeline,
    )
    if target_name == "speed_deviation":
        values, _target_baseline = _speed_deviation_target_values(target, date_from, receipt)
    elif target_name == "win":
        values = (target["finish"].astype(float) == 1).astype("int8")
    elif target_name == "place3":
        values = (target["finish"].astype(float) <= 3).astype("int8")
    else:
        values = (target["num_horses"].astype(float) - target["finish"].astype(float) + 1).clip(lower=0).fillna(0).astype("int16")

    metadata_columns = [name for name in (
        "race_date", "race_id", "venue", "distance", "surface", "num_horses",
        "horse_id", "horse_number", "finish", "odds", "popularity",
    ) if name in target.columns]
    columns = [*metadata_columns, "target_value", *list(features.columns)]
    buffer = io.StringIO(newline="")
    writer = csv.writer(buffer)
    writer.writerow(columns)
    yield "\ufeff" + buffer.getvalue()
    buffer.seek(0)
    buffer.truncate(0)
    for start in range(0, len(target), _CSV_CHUNK_ROWS):
        stop = min(start + _CSV_CHUNK_ROWS, len(target))
        chunk = target.iloc[start:stop].loc[:, metadata_columns].reset_index(drop=True).copy()
        chunk.insert(len(chunk.columns), "target_value", values.iloc[start:stop].to_numpy())
        chunk = chunk.join(features.iloc[start:stop].reset_index(drop=True))
        for row in chunk.itertuples(index=False, name=None):
            writer.writerow([_csv_cell(value) for value in row])
        yield buffer.getvalue()
        buffer.seek(0)
        buffer.truncate(0)


@router.get("/api/dataframe")
async def read_dataframe(
    dataset: Dataset = "horses",
    from_date: str | None = None,
    to_date: str | None = None,
    limit: int = Query(default=100, ge=1, le=500),
    offset: int = Query(default=0, ge=0),
    _: dict = Depends(require_premium),
):
    """Return a paginated DataFrame-like view from the scraper's SQLite database."""
    _filters(from_date, to_date)
    return await asyncio.to_thread(_page, dataset, from_date, to_date, limit, offset)


@router.get("/api/dataframe/csv")
async def export_dataframe_csv(
    dataset: Dataset = "horses",
    from_date: str | None = None,
    to_date: str | None = None,
    _: dict = Depends(require_premium),
):
    """Stream all rows in the selected date range from the same read-only SQLite source."""
    _filters(from_date, to_date)
    row_count = await asyncio.to_thread(_dataset_count, dataset, from_date, to_date)
    if row_count > _CSV_MAX_ROWS:
        raise HTTPException(
            status_code=413,
            detail=f"一度に出力できるCSVは{_CSV_MAX_ROWS:,}行までです。日付の範囲を絞ってください。",
        )
    label = "races" if dataset == "races" else "horse-data"
    suffix = f"{from_date or 'all'}_{to_date or 'all'}"
    filename = f"keiba_{label}_{suffix}.csv"
    return StreamingResponse(
        _csv_stream(dataset, from_date, to_date),
        media_type="text/csv; charset=utf-8",
        headers={"Content-Disposition": f"attachment; filename*=UTF-8''{quote(filename)}"},
    )


@router.get("/api/dataframe/features")
async def read_engineered_features(
    date_from: str,
    date_to: str,
    pipeline: FeaturePipeline = "local-db-shared-optimized-v1",
    target: FeatureTarget = "win",
    test_size: float = Query(default=0.2, ge=0.1, le=0.5),
    limit: int = Query(default=100, ge=1, le=500),
    offset: int = Query(default=0, ge=0),
    _: dict = Depends(require_premium),
):
    """Preview the exact pre-encoding feature matrix used by the shared trainer."""
    return await asyncio.to_thread(
        _feature_records, date_from, date_to, pipeline, target, test_size, limit, offset,
    )


@router.get("/api/dataframe/features/csv")
async def export_engineered_features_csv(
    date_from: str,
    date_to: str,
    pipeline: FeaturePipeline = "local-db-shared-optimized-v1",
    target: FeatureTarget = "win",
    test_size: float = Query(default=0.2, ge=0.1, le=0.5),
    _: dict = Depends(require_premium),
):
    if not re.fullmatch(r"\d{4}-\d{2}", date_from or "") or not re.fullmatch(r"\d{4}-\d{2}", date_to or ""):
        raise HTTPException(status_code=400, detail="学習前特徴量は開始年月と終了年月を指定してください")
    prepared = await asyncio.to_thread(_feature_inputs, date_from, date_to, pipeline)
    row_count = len(prepared[0])
    if row_count > _CSV_MAX_ROWS:
        raise HTTPException(status_code=413, detail=f"一度に出力できるCSVは{_CSV_MAX_ROWS:,}行までです。学習期間を絞ってください。")
    filename = f"keiba_features_{pipeline}_{target}_{date_from}_{date_to}.csv"
    return StreamingResponse(
        _feature_csv_stream(date_from, date_to, pipeline, target, test_size, prepared=prepared),
        media_type="text/csv; charset=utf-8",
        headers={"Content-Disposition": f"attachment; filename*=UTF-8''{quote(filename)}"},
    )
