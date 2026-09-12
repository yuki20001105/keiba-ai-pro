"""
学習エンドポイント
POST /api/train
POST /api/train/start
GET  /api/train/capability
GET  /api/train/status/{job_id}
"""
from __future__ import annotations

import asyncio
import functools
import ipaddress
import os
import shutil
import sqlite3
import subprocess
import tempfile
import threading
import time
import traceback
import uuid
from collections.abc import Mapping
from datetime import datetime
from pathlib import Path
from typing import List

import joblib
import numpy as np
from fastapi import APIRouter, Depends, HTTPException, Request

from app_config import (  # type: ignore
    SUPABASE_DATA_ENABLED,
    CONFIG_PATH,
    MODELS_DIR,
    ULTIMATE_DB,
    get_active_model_id,
    get_supabase_client,
    logger,
)
from deps.auth import require_admin  # type: ignore
from models import TrainRequest, TrainResponse  # type: ignore
from keiba_ai.constants import FUTURE_FIELDS  # type: ignore
from training.approved_execution import (  # type: ignore
    ApprovedExecutionError,
    ApprovedTrainingExecution,
)
from training.job_store import (  # type: ignore
    load_train_job,
    mark_interrupted_train_jobs,
)
from training.local_feature_contract import (  # type: ignore
    derive_local_feature_schema,
    engineer_training_features,
    filter_training_period,
    prepare_lightgbm_feature_frame,
)
from training.model_evaluation import evaluate_speed_deviation_predictions  # type: ignore
from training.local_retrain import (  # type: ignore
    LOCAL_EXECUTION_POLICY,
    LocalExecutionBundle,
    LocalRetrainConflict,
    LocalRetrainError,
    LocalRetrainGateway,
    LocalRetrainNotFound,
    LocalRetrainStore,
    LocalTrainingContract,
    LocalTrainingExecution,
    compute_source_tree_sha256,
    copy_local_snapshot,
    create_sqlite_snapshot,
    new_preparation_token,
    reconcile_snapshot_catalog,
    sha256_file,
)
from training.retrain_worker import (  # type: ignore
    MAX_ARTIFACT_BYTES,
    ApprovedRetrainCoordinator,
)

router = APIRouter()


# ---------------------------------------------------------------------------
# BetaCalibration ラッパー（モジュールレベルで定義 → joblib pickle 対応）
# ---------------------------------------------------------------------------
class BCWrap:
    """BetaCalibration を 1D 入力対応にラップ（既存 predict.py との互換性維持）"""

    def __init__(self, c: object) -> None:
        self._c = c

    def predict(self, x: "np.ndarray") -> "np.ndarray":
        return self._c.predict(np.asarray(x, float).reshape(-1, 1)).ravel()



_LOCAL_ENVIRONMENTS = frozenset({"local", "development", "dev", "test", "ci"})
_LOCAL_RETRAIN_SOURCE_ROOT = Path(__file__).resolve().parents[2]
_LOCAL_RETRAIN_LEDGER = Path(__file__).resolve().parents[1] / "data" / "local_retrain_jobs.db"
_LOCAL_RETRAIN_SNAPSHOT_CATALOG = ULTIMATE_DB.parent / "model_retrain_snapshots"
_LOCAL_RETRAIN_ARTIFACT_ROOT = MODELS_DIR / ".local-retrain" / "artifacts"
_LOCAL_RETRAIN_RUNTIME_LOCK = threading.RLock()
_LOCAL_RETRAIN_RUNTIME: tuple[LocalRetrainStore, LocalRetrainGateway] | None = None
_LOCAL_RETRAIN_STARTUP_ERROR: str | None = None
_LOCAL_PREPARATION_TTL_SECONDS = 300
_LOCAL_PREPARATION_HEARTBEAT_SECONDS = 15


def _local_owner_id(current_user: Mapping[str, object]) -> str:
    """Return a verified durable Admin UUID; development fallbacks are forbidden."""

    value = str(current_user.get("user_id") or "").strip().lower()
    try:
        parsed = uuid.UUID(value)
    except (ValueError, AttributeError) as exc:
        raise HTTPException(status_code=403, detail="admin identity is unavailable") from exc
    if parsed.version != 4 or str(parsed) != value:
        raise HTTPException(status_code=403, detail="admin identity is unavailable")
    return value


async def require_local_training_admin(
    current_user: dict = Depends(require_admin),
) -> dict:
    """Reject the middleware's unauthenticated local-dev Admin fallback."""

    _local_owner_id(current_user)
    return current_user


def _active_model_binding() -> tuple[str, str]:
    """Bind the exact active model ID and bytes used to authorize a local run."""

    model_id = get_active_model_id()
    if not model_id:
        raise LocalRetrainError("local-active-model-unavailable")
    model_root = MODELS_DIR.resolve(strict=True)
    model_path = MODELS_DIR / f"{model_id}.joblib"
    if model_path.is_symlink():
        raise LocalRetrainError("local-active-model-path-invalid")
    try:
        resolved = model_path.resolve(strict=True)
    except OSError as exc:
        raise LocalRetrainError("local-active-model-unavailable") from exc
    if not resolved.is_file() or resolved.parent != model_root:
        raise LocalRetrainError("local-active-model-path-invalid")
    try:
        digest, size = sha256_file(resolved, max_bytes=MAX_ARTIFACT_BYTES)
    except OSError as exc:
        raise LocalRetrainError("local-active-model-unavailable") from exc
    if size < 1:
        raise LocalRetrainError("local-active-model-unavailable")
    return model_id, digest


def _active_model_features(expected_binding: tuple[str, str]) -> tuple[str, ...]:
    """Read the trusted active bundle and recheck it after deserialization."""

    model_id, _digest = expected_binding
    path = MODELS_DIR / f"{model_id}.joblib"
    try:
        bundle = joblib.load(path)
    except Exception as exc:
        raise LocalRetrainError("local-active-model-bundle-invalid") from exc
    if not isinstance(bundle, Mapping):
        raise LocalRetrainError("local-active-model-bundle-invalid")
    raw_features = bundle.get("feature_columns")
    if not isinstance(raw_features, (list, tuple)):
        raise LocalRetrainError("local-active-model-features-invalid")
    features = tuple(raw_features)
    if (
        not features
        or len(features) > 2_048
        or any(not isinstance(value, str) or not value for value in features)
        or len(set(features)) != len(features)
        or set(features).intersection(FUTURE_FIELDS)
    ):
        raise LocalRetrainError("local-active-model-features-invalid")
    if _active_model_binding() != expected_binding:
        raise LocalRetrainError("local-active-model-binding-changed")
    return features


def _candidate_commit_sha() -> str:
    """Read the full HEAD SHA without treating a dirty tree as clean."""

    try:
        completed = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            cwd=_LOCAL_RETRAIN_SOURCE_ROOT,
            check=True,
            capture_output=True,
            text=True,
            timeout=10,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        raise LocalRetrainError("local-source-commit-unavailable") from exc
    candidate = completed.stdout.strip().lower()
    if len(candidate) != 40 or any(char not in "0123456789abcdef" for char in candidate):
        raise LocalRetrainError("local-source-commit-invalid")
    return candidate


def _get_local_retrain_runtime() -> tuple[LocalRetrainStore, LocalRetrainGateway]:
    """Construct one process-local facade over the durable SQLite ledger."""

    global _LOCAL_RETRAIN_RUNTIME
    with _LOCAL_RETRAIN_RUNTIME_LOCK:
        if _LOCAL_RETRAIN_RUNTIME is None:
            store = LocalRetrainStore(_LOCAL_RETRAIN_LEDGER)
            gateway = LocalRetrainGateway(
                store,
                artifact_root=_LOCAL_RETRAIN_ARTIFACT_ROOT,
                published_model_root=MODELS_DIR,
                source_root=_LOCAL_RETRAIN_SOURCE_ROOT,
                candidate_commit_provider=_candidate_commit_sha,
                active_model_binding_provider=_active_model_binding,
            )
            _LOCAL_RETRAIN_RUNTIME = (store, gateway)
        return _LOCAL_RETRAIN_RUNTIME


def _local_training_parameters(request: TrainRequest) -> dict[str, object]:
    payload = request.model_dump()
    payload.pop("target", None)
    payload.pop("model_type", None)
    return payload


def reconcile_interrupted_train_jobs() -> int:
    """Reconcile legacy and fenced local jobs from the startup lifecycle."""

    global _LOCAL_RETRAIN_STARTUP_ERROR
    legacy_count = mark_interrupted_train_jobs()
    try:
        _require_legacy_model_training_allowed()
    except HTTPException:
        return legacy_count

    try:
        store, gateway = _get_local_retrain_runtime()
        removed_snapshots = reconcile_snapshot_catalog(_LOCAL_RETRAIN_SNAPSHOT_CATALOG)
        report = gateway.reconcile_orphan_artifacts()
        for job_id in report.redispatch_job_ids:
            try:
                _start_local_retrain_thread(job_id, prepare_request=None)
            except Exception:
                try:
                    queued = store.get_job(job_id)
                    if queued.state == "queued":
                        store.fail_queued(
                            job_id=job_id,
                            expected_version=queued.record_version,
                            failure_code="local-runner-start-failed",
                        )
                except LocalRetrainError:
                    pass
                logger.error(
                    "local retrain redispatch %s failed:\n%s",
                    job_id,
                    traceback.format_exc(),
                )
        _LOCAL_RETRAIN_STARTUP_ERROR = None
        logger.info(
            "local retrain startup reconciliation: redispatch=%s failed=%s "
            "publications=%s artifacts_removed=%s snapshots_removed=%s",
            len(report.redispatch_job_ids),
            len(report.failed_job_ids),
            report.completed_publications,
            report.removed_orphans,
            removed_snapshots,
        )
        return legacy_count + len(report.failed_job_ids)
    except Exception:
        _LOCAL_RETRAIN_STARTUP_ERROR = "local-training-reconciliation-failed"
        logger.error("local retrain startup reconciliation failed:\n%s", traceback.format_exc())
        return legacy_count


def _require_legacy_model_training_allowed() -> None:
    """Keep direct artifact writers behind explicit local/test compatibility."""

    environment = (os.environ.get("APP_ENV") or "").strip().lower()
    enabled = (os.environ.get("MODEL_TRAINING_LOCAL_ENABLED") or "").strip().lower() == "true"
    if environment not in _LOCAL_ENVIRONMENTS or not enabled:
        raise HTTPException(
            status_code=409,
            detail=(
                "model training requires an approval-bound durable job; "
                "legacy direct training is available only by explicit local/test opt-in"
            ),
        )


def _is_loopback_client(request: Request) -> bool:
    """Accept only a direct request from this machine."""

    host = request.client.host.strip() if request.client and request.client.host else ""
    try:
        address = ipaddress.ip_address(host)
    except ValueError:
        return host.lower() == "localhost"
    if address.is_loopback:
        return True
    mapped = getattr(address, "ipv4_mapped", None)
    return bool(mapped and mapped.is_loopback)


async def require_loopback_training_request(request: Request) -> None:
    if not _is_loopback_client(request):
        raise HTTPException(
            status_code=403,
            detail="local model training is available only from this computer",
        )


def _training_capability_state() -> dict[str, object]:
    """Return a bounded, fail-closed local training readiness result."""

    try:
        _require_legacy_model_training_allowed()
    except HTTPException:
        return {
            "enabled": False,
            "mode": "disabled",
            "reason": "local-training-disabled",
        }

    try:
        database_ready = (
            ULTIMATE_DB.is_file()
            and ULTIMATE_DB.stat().st_size > 0
            and os.access(ULTIMATE_DB, os.R_OK)
        )
    except (OSError, sqlite3.Error):
        database_ready = False
    if not database_ready:
        return {
            "enabled": False,
            "mode": "disabled",
            "reason": "database-unavailable",
        }

    try:
        model_storage_ready = MODELS_DIR.is_dir() and os.access(MODELS_DIR, os.W_OK)
    except OSError:
        model_storage_ready = False
    if not model_storage_ready:
        return {
            "enabled": False,
            "mode": "disabled",
            "reason": "model-storage-unavailable",
        }

    if _LOCAL_RETRAIN_STARTUP_ERROR is not None:
        return {
            "enabled": False,
            "mode": "disabled",
            "reason": _LOCAL_RETRAIN_STARTUP_ERROR,
        }

    try:
        database_size = ULTIMATE_DB.stat().st_size
        free_bytes = shutil.disk_usage(ULTIMATE_DB.parent).free
        reserve_bytes = max(512 * 1024 * 1024, database_size // 10)
        if free_bytes < database_size + reserve_bytes:
            return {
                "enabled": False,
                "mode": "disabled",
                "reason": "snapshot-storage-insufficient",
            }
        _LOCAL_RETRAIN_SNAPSHOT_CATALOG.mkdir(parents=True, exist_ok=True)
        if (
            not os.access(_LOCAL_RETRAIN_SNAPSHOT_CATALOG, os.W_OK)
            or os.stat(_LOCAL_RETRAIN_SNAPSHOT_CATALOG).st_dev
            != os.stat(tempfile.gettempdir()).st_dev
        ):
            return {
                "enabled": False,
                "mode": "disabled",
                "reason": "snapshot-storage-incompatible",
            }
        _get_local_retrain_runtime()
        binding = _active_model_binding()
        _active_model_features(binding)
        _candidate_commit_sha()
        compute_source_tree_sha256(_LOCAL_RETRAIN_SOURCE_ROOT)
    except LocalRetrainError as exc:
        return {
            "enabled": False,
            "mode": "disabled",
            "reason": str(exc),
        }
    except (OSError, sqlite3.Error):
        return {
            "enabled": False,
            "mode": "disabled",
            "reason": "local-training-storage-unavailable",
        }

    return {"enabled": True, "mode": "local-admin", "reason": None}


def _atomic_joblib_dump(bundle: dict, model_path: Path) -> None:
    """Publish a complete artifact without exposing a partially written file."""

    temporary_path = model_path.with_name(
        f".{model_path.name}.{uuid.uuid4().hex}.tmp"
    )
    try:
        joblib.dump(bundle, temporary_path)
        os.replace(temporary_path, model_path)
    finally:
        try:
            temporary_path.unlink(missing_ok=True)
        except OSError:
            logger.warning("temporary model artifact cleanup failed")


def _extract_ym_from_df(df: "pd.DataFrame") -> list:  # noqa: F821
    # race_date (YYYYMMDD) が存在すれば先頭6桁 (YYYYMM) を使う。
    # race_id は YYYYVVKKNNRR 形式で venueCode が混入するため使用しない。
    if df.empty:
        return []
    if "race_date" in df.columns:
        yms = df["race_date"].dropna().astype(str).str[:6]
        valid = yms[yms.str.match(r"^\d{6}$")]
        if not valid.empty:
            return valid.tolist()
    return []


def _get_actual_date_from(df: "pd.DataFrame", fallback: "str | None") -> "str | None":  # noqa: F821
    yms = _extract_ym_from_df(df)
    if yms:
        ym = min(yms)
        return f"{ym[:4]}-{ym[4:6]}"
    return fallback


def _get_actual_date_to(df: "pd.DataFrame", fallback: "str | None") -> "str | None":  # noqa: F821
    yms = _extract_ym_from_df(df)
    if yms:
        ym = max(yms)
        return f"{ym[:4]}-{ym[4:6]}"
    return fallback


def _get_date8_from(df: "pd.DataFrame") -> str:  # noqa: F821
    """学習データ最小日付をYYYYMMDD形式で返す（race_dateカラム優先）"""
    if "race_date" in df.columns:
        dates = df["race_date"].dropna().astype(str).str.strip()
        valid = dates[dates.str.match(r"^\d{8}$")]
        if len(valid) > 0:
            return valid.min()
    # fallback: YYYYMM from race_id → YYYYMM01
    yms = _extract_ym_from_df(df)
    if yms:
        return min(yms) + "01"
    return datetime.now().strftime("%Y%m%d")


def _get_date8_to(df: "pd.DataFrame") -> str:  # noqa: F821
    """学習データ最大日付をYYYYMMDD形式で返す（race_dateカラム優先）"""
    if "race_date" in df.columns:
        dates = df["race_date"].dropna().astype(str).str.strip()
        valid = dates[dates.str.match(r"^\d{8}$")]
        if len(valid) > 0:
            return valid.max()
    yms = _extract_ym_from_df(df)
    if yms:
        return max(yms) + "28"
    return datetime.now().strftime("%Y%m%d")


# レース後確定フィールド（keiba_ai.constants.FUTURE_FIELDS を参照）


async def _do_train(
    request: TrainRequest,
    current_user: dict,
    progress_cb=None,
    approved_execution: ApprovedTrainingExecution | LocalTrainingExecution | None = None,
) -> TrainResponse:
    """モデル学習内部実装（progress_cb は任意のコールバック = (msg: str, pct: int | None) -> None）"""
    if approved_execution is None:
        _require_legacy_model_training_allowed()
    else:
        try:
            approved_execution.validate_request(
                target=request.target,
                model_type=request.model_type,
                force_sync=request.force_sync,
                test_size=request.test_size,
                cv_folds=request.cv_folds,
                use_optuna=request.use_optuna,
                training_date_from=request.training_date_from,
                training_date_to=request.training_date_to,
            )
            approved_execution.verify_snapshot()
        except ApprovedExecutionError as exc:
            raise HTTPException(
                status_code=409,
                detail="approved training contract mismatch",
            ) from exc
    uses_explicit_oot_split = bool(
        approved_execution is not None
        and getattr(approved_execution, "uses_explicit_oot_split", True)
    )
    allows_calibration = bool(
        approved_execution is None
        or getattr(approved_execution, "allows_calibration", False)
    )
    if progress_cb is None:
        def progress_cb(msg: str, pct: int = None): pass  # noqa: F811
    try:
        import pandas as pd
        from keiba_ai.db_ultimate_loader import load_ultimate_training_frame  # type: ignore
        from keiba_ai.lightgbm_feature_optimizer import (  # type: ignore
            prepare_for_lightgbm_ultimate,
        )
        from keiba_ai.optuna_optimizer import OptunaLightGBMOptimizer  # type: ignore

        # Phase 0: 87特徴量モード固定（入力値に関わらず常に ultimate LightGBM）
        request = request.model_copy(update={
            "ultimate_mode": True,
            "use_optimizer": True,
            "model_type": "lightgbm",
        })

        start_time = datetime.now()
        optuna_executed = False
        optuna_error = None

        print("\n" + "=" * 70)
        print("【学習リクエスト受信】")
        print("=" * 70)
        print(f"  target: {request.target}")
        print(f"  model_type: {request.model_type}")
        print(f"  use_optimizer: {request.use_optimizer}")
        print(f"  use_optuna: {request.use_optuna}")
        print(f"  optuna_trials: {request.optuna_trials}")
        print(f"  cv_folds: {request.cv_folds}")
        print("=" * 70 + "\n")

        # 常に ultimate DB を使用（87特徴量モード固定）
        db_path = (
            ULTIMATE_DB
            if approved_execution is None
            else approved_execution.snapshot_path
        )

        progress_cb("データベース接続中...", 3)

        # Supabase → SQLite 同期（ブロッキング呼び出しを to_thread で分離）
        if approved_execution is None and SUPABASE_DATA_ENABLED and get_supabase_client():
            from app_config import sync_supabase_to_sqlite  # type: ignore
            if request.force_sync:
                logger.info("Supabase からデータを同期中...")
                db_path.parent.mkdir(parents=True, exist_ok=True)
                synced = await asyncio.to_thread(sync_supabase_to_sqlite, db_path)
                logger.info(f"同期完了: {synced} レース")
            else:
                logger.info("force_sync=False: Supabase同期スキップ")
                if not db_path.exists():
                    db_path.parent.mkdir(parents=True, exist_ok=True)
                    synced = await asyncio.to_thread(sync_supabase_to_sqlite, db_path)
                    logger.info(f"初回同期: {synced} レース")

        # データ読み辿み（常に ultimate モード）
        progress_cb("学習データ読み込み中...", 8)
        df = load_ultimate_training_frame(
            db_path,
            read_only=approved_execution is not None,
        )

        print(f"DEBUG: Loaded {len(df)} rows from database")
        progress_cb(f"データ読み込み完了 ({len(df):,} 行)", 15)

        # Preparation and execution share this exact period selector so the
        # immutable local feature contract cannot drift from runtime.
        df = filter_training_period(
            df,
            training_date_from=request.training_date_from,
            training_date_to=request.training_date_to,
        )

        if df.empty:
            raise HTTPException(
                status_code=400,
                detail=f"訓練データが見つかりません。DB: {db_path}",
            )

        evaluation_frame = pd.DataFrame(index=df.index)
        for evaluation_column in (
            "race_id",
            "race_date",
            "finish",
            "popularity",
            "odds",
            "tansho_payout",
        ):
            evaluation_frame[evaluation_column] = (
                df[evaluation_column] if evaluation_column in df.columns else pd.NA
            )

        # ターゲット変数を先に取得（FUTURE_FIELDSのdrop前にfinish列が必要）
        from keiba_ai.train import _make_target  # type: ignore
        y = _make_target(df, request.target)

        if request.target != "speed_deviation" and len(y.unique()) < 2:
            raise HTTPException(status_code=400, detail="2クラス以上が必要です")
        # 特徴量エンジニアリング（finish等を使うのでdrop前に実施）
        progress_cb("特徴量エンジニアリング中...", 20)
        df = engineer_training_features(df)

        _all_feature_columns: List[str] = []
        optimizer = None
        categorical_features = []
        feature_count = 0
        valid_num_cols = []
        valid_cat_cols = []
        model = None
        auc = logloss = cv_auc_mean = cv_auc_std = 0.0

        if request.use_optimizer and request.model_type == "lightgbm":
            try:
                print("\n=== LightGBM最適化モード ===")
                progress_cb("特徴量選択・最適化中...", 28)
                exclude_cols = [
                    request.target, "race_id", "horse_id", "jockey_id",
                    "trainer_id", "owner_id", "finish_position",
                ]
                if uses_explicit_oot_split:
                    if "race_date" not in df.columns:
                        raise ApprovedExecutionError("approved-period-column-unavailable")
                    approved_dates = pd.to_datetime(
                        df["race_date"].astype(str).str.strip(),
                        format="%Y%m%d",
                        errors="coerce",
                    )
                    train_mask = approved_dates.between(
                        pd.Timestamp(approved_execution.train_period_start),
                        pd.Timestamp(approved_execution.train_period_end),
                    )
                    validation_mask = approved_dates.between(
                        pd.Timestamp(approved_execution.validation_period_start),
                        pd.Timestamp(approved_execution.validation_period_end),
                    )
                    if int(train_mask.sum()) < 200 or int(validation_mask.sum()) < 50:
                        raise ApprovedExecutionError("approved-period-observations-insufficient")
                    df_train_source = df.loc[train_mask].copy()
                    df_validation_source = df.loc[validation_mask].copy()
                    y_train_source = y.loc[train_mask].reset_index(drop=True)
                    y_validation_source = y.loc[validation_mask].reset_index(drop=True)
                    if y_train_source.nunique() < 2 or y_validation_source.nunique() < 2:
                        raise ApprovedExecutionError("approved-period-target-classes-insufficient")

                    df_train_optimized, optimizer, categorical_features = (
                        prepare_for_lightgbm_ultimate(
                            df_train_source,
                            target_col=request.target,
                            is_training=True,
                        )
                    )
                    df_validation_optimized, _, _ = prepare_for_lightgbm_ultimate(
                        df_validation_source,
                        target_col=request.target,
                        is_training=False,
                        optimizer=optimizer,
                    )
                    X_train_source = df_train_optimized.drop(
                        [c for c in exclude_cols if c in df_train_optimized.columns],
                        axis=1,
                    )
                    X_validation_source = df_validation_optimized.drop(
                        [c for c in exclude_cols if c in df_validation_optimized.columns],
                        axis=1,
                    )
                    train_objects = X_train_source.select_dtypes(include=["object"]).columns.tolist()
                    validation_objects = X_validation_source.select_dtypes(
                        include=["object"]
                    ).columns.tolist()
                    if train_objects:
                        X_train_source = X_train_source.drop(columns=train_objects)
                    if validation_objects:
                        X_validation_source = X_validation_source.drop(columns=validation_objects)
                    try:
                        approved_columns = approved_execution.select_feature_columns(
                            X_train_source.columns.tolist(),
                            future_fields=FUTURE_FIELDS,
                        )
                        approved_execution.select_feature_columns(
                            X_validation_source.columns.tolist(),
                            future_fields=FUTURE_FIELDS,
                        )
                    except ApprovedExecutionError as exc:
                        raise HTTPException(
                            status_code=409,
                            detail="approved training contract mismatch",
                        ) from exc
                    X_train_source = X_train_source.loc[:, list(approved_columns)]
                    X_validation_source = X_validation_source.loc[:, list(approved_columns)]
                    categorical_features = [
                        feature for feature in categorical_features if feature in approved_columns
                    ]
                    approved_train_count = len(X_train_source)
                    X = pd.concat(
                        [X_train_source, X_validation_source],
                        ignore_index=True,
                    )
                    y = pd.concat(
                        [y_train_source, y_validation_source],
                        ignore_index=True,
                    )
                    df = pd.concat(
                        [df_train_source, df_validation_source],
                        ignore_index=True,
                    )
                    evaluation_frame = pd.concat(
                        [
                            evaluation_frame.loc[train_mask],
                            evaluation_frame.loc[validation_mask],
                        ],
                        ignore_index=True,
                    )
                    df_optimized = pd.concat(
                        [df_train_optimized, df_validation_optimized],
                        ignore_index=True,
                    )
                else:
                    prepared_features = prepare_lightgbm_feature_frame(
                        df,
                        target=request.target,
                    )
                    df_optimized = prepared_features.optimized
                    optimizer = prepared_features.optimizer
                    categorical_features = list(
                        prepared_features.categorical_features
                    )
                    X = prepared_features.features
                    if approved_execution is not None:
                        try:
                            bound_columns = approved_execution.select_feature_columns(
                                X.columns.tolist(),
                                future_fields=FUTURE_FIELDS,
                            )
                        except ApprovedExecutionError as exc:
                            raise HTTPException(
                                status_code=409,
                                detail="local training contract mismatch",
                            ) from exc
                        X = X.loc[:, list(bound_columns)]
                        categorical_features = [
                            feature for feature in categorical_features if feature in bound_columns
                        ]
                feature_count = len(X.columns)
                _all_feature_columns = X.columns.tolist()

                from sklearn.model_selection import train_test_split
                from sklearn.metrics import roc_auc_score, log_loss
                import lightgbm as lgb

                # 時系列分割（ランダム分割より汎化性能検証に適切）
                X = X.reset_index(drop=True)
                y = y.reset_index(drop=True)
                evaluation_frame = evaluation_frame.reset_index(drop=True)

                _is_regression = request.target == "speed_deviation"
                _is_ranking    = request.target == "rank"

                # regression 前処理: finish_time NaN 行を分割前に除去
                # race_results_ultimate に出馬表(shutuba)データが混在すると
                # 時系列分割でテストセットが全 NaN になり model.predict が失敗する
                if _is_regression:
                    _pre_valid = y.notna().values
                    if not _pre_valid.all():
                        n_removed = int((~_pre_valid).sum())
                        logger.info(
                            f"speed_deviation 前処理: finish_time NaN {n_removed} 行を除去 "
                            f"({int(_pre_valid.sum())} 行が有効)"
                        )
                        X = X.loc[_pre_valid].reset_index(drop=True)
                        y = y.loc[_pre_valid].reset_index(drop=True)
                        evaluation_frame = (
                            evaluation_frame.loc[_pre_valid].reset_index(drop=True)
                        )
                        # df のインデックスを同期（race_date が時系列分割に使用される）
                        df = df.reset_index(drop=True).loc[_pre_valid].reset_index(drop=True)

                _time_split = uses_explicit_oot_split
                if uses_explicit_oot_split:
                    X_train = X.iloc[:approved_train_count]
                    X_test = X.iloc[approved_train_count:]
                    y_train = y.iloc[:approved_train_count]
                    y_test = y.iloc[approved_train_count:]
                    logger.info(
                        "Approved out-of-time split: train=%s validation=%s",
                        len(X_train),
                        len(X_test),
                    )
                elif "race_date" in df.columns:
                    _dates = pd.to_datetime(
                        df["race_date"].reset_index(drop=True).astype(str).str[:8],
                        format="%Y%m%d", errors="coerce",
                    )
                    _cutoff = _dates.quantile(1.0 - request.test_size)
                    _tr_mask = (_dates <= _cutoff).values
                    _te_mask = ~_tr_mask
                    if int(_tr_mask.sum()) >= 200 and int(_te_mask.sum()) >= 50:
                        X_train = X.loc[_tr_mask]
                        X_test = X.loc[_te_mask]
                        y_train = y.loc[_tr_mask]
                        y_test = y.loc[_te_mask]
                        _time_split = True
                        logger.info(f"時系列分割: 学習 {_tr_mask.sum()}行, テスト {_te_mask.sum()}行")
                if not _time_split:
                    if _is_regression:
                        X_train, X_test, y_train, y_test = train_test_split(
                            X, y, test_size=request.test_size, random_state=42
                        )
                    elif _is_ranking:
                        X_train, X_test, y_train, y_test = train_test_split(
                            X, y, test_size=request.test_size, random_state=42
                        )
                    else:
                        X_train, X_test, y_train, y_test = train_test_split(
                            X, y, test_size=request.test_size, random_state=42, stratify=y
                        )
                # speed_deviation: NaN行を除外（タイム欠損馬）
                if _is_regression:
                    _valid_train = y_train.notna()
                    _valid_test  = y_test.notna()
                    X_train, y_train = X_train.loc[_valid_train], y_train.loc[_valid_train]
                    X_test,  y_test  = X_test.loc[_valid_test],   y_test.loc[_valid_test]

                categorical_indices = [X.columns.get_loc(c) for c in categorical_features if c in X.columns]

                if _is_ranking:
                    # ─── LambdaRank パス ─────────────────────────────────────
                    # race_id ごとのグループサイズを計算（train/test 分割後の df を参照）
                    _df_for_groups = df.reset_index(drop=True)
                    _y_full = y.reset_index(drop=True)
                    _tr_idx = X_train.index if hasattr(X_train, 'index') else range(len(X_train))
                    _te_idx = X_test.index  if hasattr(X_test,  'index') else range(len(X_test))
                    _race_ids_full = df_optimized["race_id"].reset_index(drop=True) if "race_id" in df_optimized.columns else pd.Series(["0"] * len(df_optimized))
                    _race_ids_tr   = _race_ids_full.loc[list(_tr_idx)].values
                    _race_ids_te   = _race_ids_full.loc[list(_te_idx)].values
                    from collections import Counter as _Counter
                    _ctr_tr = list(_Counter(_race_ids_tr).values())
                    _ctr_te = list(_Counter(_race_ids_te).values())
                    _max_label = int(y_train.max()) if len(y_train) > 0 else 18
                    train_data = lgb.Dataset(
                        X_train.values, label=y_train.values,
                        group=_ctr_tr,
                    )
                    valid_data = lgb.Dataset(
                        X_test.values, label=y_test.values,
                        group=_ctr_te,
                    )
                    params = {
                        "objective":        "lambdarank",
                        "metric":           "ndcg",
                        "ndcg_eval_at":     [1, 3, 5],
                        "label_gain":       list(range(_max_label + 2)),
                        "num_leaves":       31,
                        "min_data_in_leaf": 20,
                        "feature_fraction": 0.8,
                        "bagging_fraction": 0.8,
                        "bagging_freq":     5,
                        "reg_alpha":        0.1,
                        "reg_lambda":       0.1,
                        "verbose":          -1,
                        "seed":             42,
                    }
                    model = lgb.train(
                        params, train_data,
                        num_boost_round=500,
                        valid_sets=[valid_data],
                        callbacks=[
                            lgb.early_stopping(stopping_rounds=50, verbose=False),
                            lgb.log_evaluation(100),
                        ],
                    )
                    _scores = model.predict(X_test.values)
                    from scipy.stats import spearmanr as _spr
                    import numpy as _np_loc_r
                    _sp, _ = _spr(y_test.values, _scores)
                    auc = float(_sp) if not _np_loc_r.isnan(_sp) else 0.0
                    logloss = 0.0
                    cv_auc_mean = auc
                    cv_auc_std  = 0.0
                    best_round_cv = model.num_trees()
                    best_round_final = best_round_cv
                    y_pred_proba = _scores
                    _is_ranker_model = True
                else:
                    _is_ranker_model = False
                    train_data = lgb.Dataset(X_train, y_train, categorical_feature=categorical_indices)

                if _is_ranking:
                    pass  # 上のブロックで学習完了
                elif _is_regression:
                    params = {
                        "objective": "regression", "metric": "rmse",
                        "max_cat_to_onehot": 4, "learning_rate": 0.05,
                        "num_leaves": 31, "min_data_in_leaf": 20,
                        "feature_fraction": 0.8, "bagging_fraction": 0.8,
                        "bagging_freq": 5, "reg_alpha": 0.1, "reg_lambda": 0.1,
                        "verbose": -1, "random_state": 42,
                    }
                else:
                    params = {
                        "objective": "binary", "metric": "binary_logloss",
                        "max_cat_to_onehot": 4, "learning_rate": 0.05,
                        "num_leaves": 31, "min_data_in_leaf": 20,
                        "feature_fraction": 0.8, "bagging_fraction": 0.8,
                        "bagging_freq": 5, "reg_alpha": 0.1, "reg_lambda": 0.1,
                        "verbose": -1, "random_state": 42,
                    }

                if not _is_ranking:
                    # CV で最適ラウンド探索
                    progress_cb(f"CV学習中 ({request.cv_folds}折)...", 35)
                    _cv_total_rounds = 1000
                    def _cv_lgb_cb(env,
                                   _pcb=progress_cb,
                                   _folds=request.cv_folds,
                                   _tot=_cv_total_rounds):
                        if env.iteration % 20 != 0:
                            return
                        pct = 35 + int(22 * env.iteration / _tot)
                        _pcb(f"CV {_folds}折 — ラウンド {env.iteration}/{_tot}", min(pct, 56))
                    _cv_lgb_cb.order = 100
                    cv_result = lgb.cv(
                        params, train_data,
                        num_boost_round=1000, nfold=request.cv_folds,
                        stratified=(not _is_regression), return_cvbooster=True,
                        callbacks=[
                            lgb.early_stopping(stopping_rounds=50, verbose=False),
                            lgb.log_evaluation(period=0),
                            _cv_lgb_cb,
                        ],
                    )
                    if _is_regression:
                        _cv_rmse_mean = cv_result["valid rmse-mean"][-1]
                        _cv_rmse_std  = cv_result["valid rmse-stdv"][-1]
                        best_round_cv = len(cv_result["valid rmse-mean"])
                        cv_auc_mean = 1.0 - _cv_rmse_mean
                        cv_auc_std  = _cv_rmse_std
                    else:
                        cv_logloss_mean = cv_result["valid binary_logloss-mean"][-1]
                        cv_logloss_std  = cv_result["valid binary_logloss-stdv"][-1]
                        best_round_cv = len(cv_result["valid binary_logloss-mean"])
                        cv_auc_mean = 1.0 - cv_logloss_mean
                        cv_auc_std  = cv_logloss_std
                    best_round_final = int(best_round_cv * request.cv_folds / (request.cv_folds - 1))

                    progress_cb("最終モデル学習中...", 60)
                    full_train_data = lgb.Dataset(X_train, y_train, categorical_feature=categorical_indices)
                    _final_rounds = best_round_final
                    def _final_lgb_cb(env,
                                      _pcb=progress_cb,
                                      _tot=_final_rounds):
                        if env.iteration % 20 != 0:
                            return
                        pct = 60 + int(5 * env.iteration / max(_tot, 1))
                        _pcb(f"最終モデル — ラウンド {env.iteration}/{_tot}", min(pct, 64))
                    _final_lgb_cb.order = 100
                    model = lgb.train(params, full_train_data, num_boost_round=best_round_final,
                                      callbacks=[_final_lgb_cb])

                    y_pred_proba = model.predict(X_test)
                    if _is_regression:
                        from scipy.stats import spearmanr as _spearmanr
                        import numpy as _np_loc
                        _sp, _ = _spearmanr(y_test, y_pred_proba)
                        auc = float(_sp) if not _np_loc.isnan(_sp) else 0.0
                        logloss = float(_np_loc.sqrt(_np_loc.nanmean((y_test.values - y_pred_proba) ** 2)))
                        cv_auc_mean = auc
                    else:
                        auc = roc_auc_score(y_test, y_pred_proba)
                        logloss = log_loss(y_test, y_pred_proba)

            except Exception as e:
                traceback.print_exc()
                logger.error(f"LightGBM処理エラー:\n{traceback.format_exc()}")
                raise

            # Optuna
            if request.use_optuna:
                optuna_executed = True
                try:
                    print("\n=== Optunaハイパーパラメータ最適化 ===")
                    progress_cb(f"Optuna最適化中 ({request.optuna_trials}試行)...", 65)
                    if request.model_type == "lightgbm":
                        categorical_indices = [X.columns.get_loc(c) for c in categorical_features if c in X.columns]
                        optuna_optimizer = OptunaLightGBMOptimizer(
                            n_trials=request.optuna_trials, cv_folds=request.cv_folds,
                            random_state=42, timeout=request.optuna_timeout,
                        )
                        best_params, best_optuna_score = optuna_optimizer.optimize(
                            X, y, categorical_features=categorical_indices,
                        )
                        optimized_params = optuna_optimizer.get_best_model_params()
                        optuna_num_rounds = optimized_params.pop("n_estimators", 1000)
                        train_data_opt = lgb.Dataset(X_train, y_train, categorical_feature=categorical_indices)
                        _opt_num_rounds = optuna_num_rounds
                        def _opt_cv_lgb_cb(env,
                                           _pcb=progress_cb,
                                           _folds=request.cv_folds,
                                           _tot=_opt_num_rounds):
                            if env.iteration % 20 != 0:
                                return
                            pct = 67 + int(12 * env.iteration / max(_tot, 1))
                            _pcb(f"Optuna CV {_folds}折 — ラウンド {env.iteration}/{_tot}", min(pct, 78))
                        _opt_cv_lgb_cb.order = 100
                        cv_result_opt = lgb.cv(
                            optimized_params, train_data_opt,
                            num_boost_round=optuna_num_rounds, nfold=request.cv_folds,
                            stratified=(not _is_regression),
                            callbacks=[
                                lgb.early_stopping(stopping_rounds=50, verbose=False),
                                lgb.log_evaluation(period=0),
                                _opt_cv_lgb_cb,
                            ],
                        )
                        # どちらの評価指標が使われていても対応できるようにフォールバック
                        _logloss_key = "valid binary_logloss-mean"
                        _rmse_key    = "valid rmse-mean"
                        _auc_key     = "valid auc-mean"
                        if _is_regression and _rmse_key in cv_result_opt:
                            cv_auc_std  = cv_result_opt["valid rmse-stdv"][-1]
                            best_round_opt = len(cv_result_opt[_rmse_key])
                        elif _logloss_key in cv_result_opt:
                            cv_auc_mean = 1.0 - cv_result_opt[_logloss_key][-1]
                            cv_auc_std = cv_result_opt["valid binary_logloss-stdv"][-1]
                            best_round_opt = len(cv_result_opt[_logloss_key])
                        elif _auc_key in cv_result_opt:
                            cv_auc_mean = cv_result_opt[_auc_key][-1]
                            cv_auc_std = cv_result_opt["valid auc-stdv"][-1]
                            best_round_opt = len(cv_result_opt[_auc_key])
                        else:
                            cv_auc_mean = 0.0
                            cv_auc_std = 0.0
                            best_round_opt = optuna_num_rounds
                        best_round_opt_final = int(best_round_opt * request.cv_folds / (request.cv_folds - 1))
                        progress_cb("Optunaパラメータでモデル再学習中...", 82)
                        _opt_final_rounds = best_round_opt_final
                        def _opt_final_lgb_cb(env,
                                              _pcb=progress_cb,
                                              _tot=_opt_final_rounds):
                            if env.iteration % 20 != 0:
                                return
                            pct = 82 + int(5 * env.iteration / max(_tot, 1))
                            _pcb(f"Optuna最終モデル — ラウンド {env.iteration}/{_tot}", min(pct, 86))
                        _opt_final_lgb_cb.order = 100
                        model = lgb.train(optimized_params, train_data_opt, num_boost_round=best_round_opt_final,
                                          callbacks=[_opt_final_lgb_cb])
                        y_pred_proba = model.predict(X_test)
                        if _is_regression:
                            from scipy.stats import spearmanr as _spearmanr2
                            import numpy as _np_loc2
                            _sp2, _ = _spearmanr2(y_test, y_pred_proba)
                            auc = float(_sp2) if not _np_loc2.isnan(_sp2) else 0.0
                            logloss = float(_np_loc2.sqrt(_np_loc2.nanmean((y_test.values - y_pred_proba) ** 2)))
                            cv_auc_mean = auc
                        else:
                            auc = roc_auc_score(y_test, y_pred_proba)
                            logloss = log_loss(y_test, y_pred_proba)
                    else:
                        from keiba_ai.optuna_optimizer import optimize_model  # type: ignore
                        model_type_map = {
                            "logistic_regression": "logistic",
                            "random_forest": "random_forest",
                            "gradient_boosting": "gradient_boosting",
                        }
                        optimize_model(
                            model_type_map[request.model_type], X.values, y.values,
                            n_trials=request.optuna_trials, timeout=request.optuna_timeout,
                        )
                except Exception as e:
                    optuna_error = f"{type(e).__name__}: {str(e)}"
                    print(f"❌ Optuna最適化エラー: {optuna_error}")
                    traceback.print_exc()

        else:
            # Phase 0: 標準モード削除。use_optimizer=True/model_type='lightgbm' のみサポート。
            raise HTTPException(
                status_code=400,
                detail="use_optimizer=True / model_type='lightgbm' のみサポートされています（87特徴量モード）",
            )

        # 確率キャリブレーション（BetaCalibration優先、IsotonicRegressionにフォールバック）
        # speed_deviation（回帰）/ rank（ランキング）の場合はキャリブレーション不要
        progress_cb("確率キャリブレーション中...", 88)
        calibrator = None
        logloss_calibrated = logloss
        if (
            allows_calibration
            and request.target not in ("speed_deviation", "rank")
        ):
            try:
                from sklearn.isotonic import IsotonicRegression as _IR
                _ir = _IR(out_of_bounds="clip")
                _ir.fit(y_pred_proba, y_test.values)
                calibrator = _ir
                # betacal が利用可能なら BetaCalibration で置き換える（より良い確率推定）
                try:
                    from betacal import BetaCalibration as _BC  # type: ignore

                    _bc = _BC(parameters="abm")
                    _bc.fit(y_pred_proba.reshape(-1, 1), y_test.values)
                    calibrator = BCWrap(_bc)
                    logger.info("BetaCalibration を使用してキャリブレーション学習完了")
                except ImportError:
                    logger.info("betacal 未インストール。IsotonicRegression でキャリブレーション学習完了")
                # キャリブレーション後の logloss を計算
                from sklearn.metrics import log_loss as _ll_fn
                _y_cal = calibrator.predict(y_pred_proba)
                logloss_calibrated = float(_ll_fn(y_test, _y_cal))
            except Exception as _cal_err:
                logger.warning(f"キャリブレーション学習失敗: {_cal_err}")

        evaluation: dict = {}
        if request.target == "speed_deviation":
            evaluation_test = evaluation_frame.loc[X_test.index].reset_index(drop=True)
            evaluation = evaluate_speed_deviation_predictions(
                actual=y_test.reset_index(drop=True),
                predicted=y_pred_proba,
                race_ids=evaluation_test["race_id"],
                finish_positions=evaluation_test["finish"],
                race_dates=evaluation_test["race_date"],
                popularity=evaluation_test["popularity"],
                odds=evaluation_test["odds"],
                payouts=evaluation_test["tansho_payout"],
            )

        # モデル保存 — IDはデータ日付範囲 + 作成日時（一意性を保証）
        progress_cb("モデルを保存中...", 93)
        date_from_8 = _get_date8_from(df)
        date_to_8 = _get_date8_to(df)
        saved_at = datetime.now().strftime("%Y%m%d_%H%M")
        # Numeric suffix preserves existing UI parsing while preventing a
        # later run in the same minute from replacing the earlier artifact.
        artifact_suffix = datetime.now().strftime("%S%f")
        model_id = f"{date_from_8}_{date_to_8}_{saved_at}_{artifact_suffix}"
        model_filename = f"model_{request.target}_{request.model_type}_{model_id}.joblib"
        if approved_execution is None:
            model_directory = MODELS_DIR
            model_directory.mkdir(parents=True, exist_ok=True)
        else:
            model_directory = approved_execution.prepare_artifact_directory()
        model_path = model_directory / model_filename

        bundle = {
            "model": model,
            "calibrator": calibrator,
            "optimizer": optimizer if request.use_optimizer else None,
            "categorical_features": categorical_features,
            "feature_cols_num": valid_num_cols if not request.use_optimizer else None,
            "feature_cols_cat": valid_cat_cols if not request.use_optimizer else None,
            "feature_columns": _all_feature_columns,
            "target": request.target,
            "model_type": "lightgbm",
            "ultimate_mode": True,
            "use_optimizer": True,
            "pipeline_config": {
                "use_feature_engineering": True,
                "use_optimizer": request.use_optimizer,
                "optimizer_type": type(optimizer).__name__ if optimizer is not None else None,
                "requires_full_history": True,
                "feature_engineering_hash": __import__("hashlib").md5(
                    __import__("inspect").getsource(
                        __import__("keiba_ai.feature_engineering", fromlist=["add_derived_features"])
                    ).encode()
                ).hexdigest()[:12],
            },
            "metrics": {
                "auc": float(auc), "logloss": float(logloss),
                "logloss_calibrated": float(logloss_calibrated),
                "cv_auc_mean": float(cv_auc_mean), "cv_auc_std": float(cv_auc_std),
            },
            "evaluation": evaluation,
            "data_count": len(df),
            "race_count": df["race_id"].nunique() if "race_id" in df.columns else 0,
            "created_at": saved_at,
            "training_date_from": _get_actual_date_from(df, request.training_date_from),
            "training_date_to": _get_actual_date_to(df, request.training_date_to),
        }
        if approved_execution is not None:
            execution_policy = getattr(
                approved_execution,
                "execution_policy",
                "approved-retrain",
            )
            execution_binding_key = (
                "local_execution"
                if execution_policy == "local-admin-train"
                else "approved_execution"
            )
            execution_binding = {
                "execution_policy": execution_policy,
                "job_id": approved_execution.job_id,
                "approved_payload_hash": approved_execution.approved_payload_hash,
                "data_snapshot_sha256": approved_execution.data_snapshot_sha256,
                "feature_contract_sha256": approved_execution.feature_contract_sha256,
                "candidate_commit_sha": approved_execution.candidate_commit_sha,
                "active_model_id": approved_execution.active_model_id,
            }
            if uses_explicit_oot_split:
                execution_binding["train_period"] = {
                    "start": approved_execution.train_period_start,
                    "end": approved_execution.train_period_end,
                }
                execution_binding["validation_period"] = {
                    "start": approved_execution.validation_period_start,
                    "end": approved_execution.validation_period_end,
                }
            else:
                execution_binding["training_period"] = {
                    "from": request.training_date_from,
                    "to": request.training_date_to,
                }
            bundle[execution_binding_key] = execution_binding
            source_tree_sha256 = getattr(
                approved_execution,
                "source_tree_sha256",
                None,
            )
            active_model_sha256 = getattr(
                approved_execution,
                "active_model_sha256",
                None,
            )
            if source_tree_sha256 is not None:
                execution_binding["source_tree_sha256"] = source_tree_sha256
            if active_model_sha256 is not None:
                execution_binding["active_model_sha256"] = active_model_sha256
        # LambdaRank はランカー固有フラグを保存
        if request.target == "rank" and locals().get("_is_ranker_model"):
            bundle["_is_ranker"] = True
        _atomic_joblib_dump(bundle, model_path)

        # Training artifacts must not mutate the source-controlled feature
        # catalog. Catalog changes are reviewed and committed separately.
        _catalog_path = Path(__file__).parent.parent.parent / "keiba" / "feature_catalog.yaml"
        if approved_execution is None and _catalog_path.exists():
            logger.debug("feature_catalog auto-sync is disabled for local training")

        # Supabase へモデルアップロード（ブロッキング I/O を to_thread で分離）
        if approved_execution is None and SUPABASE_DATA_ENABLED and get_supabase_client():
            from app_config import upload_model_to_supabase  # type: ignore
            await asyncio.to_thread(
                upload_model_to_supabase,
                model_path,
                model_id,
                {
                    "user_id": current_user.get("user_id"),
                    "model_id": model_id,
                    "target": request.target,
                    "model_type": "lightgbm",
                    "ultimate_mode": True,
                    "use_optimizer": True,
                    "auc": float(auc),
                    "cv_auc_mean": float(cv_auc_mean),
                    "data_count": len(df),
                    "race_count": int(df["race_id"].nunique()) if "race_id" in df.columns else 0,
                    "created_at": saved_at,
                    "training_date_from": _get_actual_date_from(df, request.training_date_from),
                    "training_date_to": _get_actual_date_to(df, request.training_date_to),
                },
            )

        progress_cb("学習完了", 98)
        training_time = (datetime.now() - start_time).total_seconds()
        completion_message = (
            f"モデル学習完了 (順位相関: {auc:.4f}, RMSE: {logloss:.4f})"
            if request.target == "speed_deviation"
            else (
                f"モデル学習完了 (AUC: {auc:.4f}, LogLoss: {logloss:.4f}, "
                f"LogLoss(Cal): {logloss_calibrated:.4f})"
            )
        )
        return TrainResponse(
            success=True,
            model_id=model_id,
            model_path=str(model_path),
            metrics={
                "auc": float(auc), "logloss": float(logloss),
                "logloss_calibrated": float(logloss_calibrated),
                "cv_auc_mean": float(cv_auc_mean), "cv_auc_std": float(cv_auc_std),
            },
            evaluation=evaluation,
            data_count=len(df),
            race_count=df["race_id"].nunique() if "race_id" in df.columns else 0,
            feature_count=feature_count,
            training_time=training_time,
            message=completion_message,
            optuna_executed=optuna_executed,
            optuna_error=optuna_error,
            feature_columns=_all_feature_columns,
        )

    except HTTPException:
        raise
    except ApprovedExecutionError as exc:
        raise HTTPException(
            status_code=409,
            detail="approved training contract mismatch",
        ) from exc
    except Exception as e:
        traceback.print_exc()
        raise HTTPException(status_code=500, detail=f"学習中にエラーが発生: {str(e)}")


@router.get("/api/train/capability")
async def train_capability(
    current_user: dict = Depends(require_local_training_admin),
    _: None = Depends(require_loopback_training_request),
):
    """Report whether this local Admin session can create a model."""
    return _training_capability_state()


@router.post("/api/train", response_model=TrainResponse)
async def train_model(
    request: TrainRequest,
    current_user: dict = Depends(require_local_training_admin),
    _: None = Depends(require_loopback_training_request),
):
    """Reject the synchronous writer; local UI uses the serialized job route."""
    _require_legacy_model_training_allowed()
    raise HTTPException(
        status_code=409,
        detail="synchronous model training is disabled; use /api/train/start",
    )


# ── 非同期ジョブ管理 ──────────────────────────────────────


def _safe_local_failure_code(error: BaseException, fallback: str) -> str:
    value = str(error).strip().lower()
    if (
        3 <= len(value) <= 63
        and value[0].isalnum()
        and all(char.isalnum() or char == "-" for char in value)
    ):
        return value
    return fallback


async def _execute_queued_local_retrain(job_id: str) -> None:
    """Run one already-bound queued job through the shared fenced coordinator."""

    store: LocalRetrainStore | None = None
    try:
        store, gateway = _get_local_retrain_runtime()
        queued = store.get_job(job_id)
        if queued.state != "queued" or queued.contract is None or queued.snapshot_path is None:
            return
        contract = queued.contract
        parser = functools.partial(
            LocalExecutionBundle.from_gateway,
            expected_source_tree_sha256=contract.source_tree_sha256,
            expected_active_model_sha256=contract.active_model_sha256,
        )
        coordinator = ApprovedRetrainCoordinator(
            gateway,
            worker_id=f"local-admin-{job_id[:8]}",
            candidate_commit_sha=contract.candidate_commit_sha,
            active_model_id=contract.active_model_id,
            execution_policy=LOCAL_EXECUTION_POLICY,
            lease_ttl_seconds=120,
            trainer=_do_train,
            bundle_parser=parser,
            request_factory=lambda bundle: TrainRequest(**bundle.training_request_payload()),
            progress_observer=gateway.progress_observer(job_id),
            snapshot_copier=copy_local_snapshot,
            result_observer=gateway.stage_result,
        )
        artifact = await coordinator.run_job(
            job_id=job_id,
            expected_version=queued.record_version,
            snapshot_source=queued.snapshot_path,
        )
        last_error: BaseException | None = None
        for attempt in range(3):
            try:
                await asyncio.to_thread(
                    gateway.record_result,
                    job_id=job_id,
                    artifact=artifact,
                )
                return
            except Exception as exc:
                last_error = exc
                if attempt < 2:
                    await asyncio.sleep(0.25 * (attempt + 1))
        if last_error is not None:
            raise last_error
    except Exception as exc:
        # `claim` happens before the Coordinator's guarded section. If it
        # fails, close the otherwise-permanent queued state with a CAS. Once
        # claimed, the Coordinator/lease protocol owns all failure mutation.
        if store is not None:
            try:
                current = store.get_job(job_id)
                if current.state == "queued":
                    store.fail_queued(
                        job_id=job_id,
                        expected_version=current.record_version,
                        failure_code="claim-runtime-binding-failed",
                    )
            except Exception:
                logger.warning(
                    "local retrain queued-job failure persistence failed for %s",
                    job_id,
                    exc_info=True,
                )
        logger.error("local retrain job %s failed:\n%s", job_id, traceback.format_exc())


async def _prepare_and_execute_local_retrain(
    job_id: str,
    request: TrainRequest,
    preparation_token: str,
) -> None:
    """Create immutable inputs asynchronously, then enter the lease protocol."""

    store: LocalRetrainStore | None = None
    try:
        store, _gateway = _get_local_retrain_runtime()
        preparing = store.get_job(job_id)
        if preparing.state != "preparing":
            return
        heartbeat_lock = threading.Lock()
        last_heartbeat = 0.0

        def renew_preparation(*, force: bool = False) -> None:
            """Renew only when this worker demonstrates forward activity."""

            nonlocal last_heartbeat
            observed = time.monotonic()
            with heartbeat_lock:
                if (
                    not force
                    and observed - last_heartbeat
                    < _LOCAL_PREPARATION_HEARTBEAT_SECONDS
                ):
                    return
                store.heartbeat_preparation(
                    job_id=job_id,
                    expected_version=preparing.record_version,
                    preparation_token=preparation_token,
                    ttl_seconds=_LOCAL_PREPARATION_TTL_SECONDS,
                )
                last_heartbeat = observed

        renew_preparation(force=True)
        store.update_progress(job_id, "実行条件を確認中", 1)
        candidate_commit = await asyncio.to_thread(_candidate_commit_sha)
        renew_preparation(force=True)
        source_tree_sha256 = await asyncio.to_thread(
            compute_source_tree_sha256,
            _LOCAL_RETRAIN_SOURCE_ROOT,
            heartbeat=renew_preparation,
        )
        renew_preparation(force=True)
        active_binding = await asyncio.to_thread(_active_model_binding)
        renew_preparation(force=True)
        # Validate the trusted active bundle while retaining its artifact hash
        # as the runtime authorization binding.  Its historical feature list
        # is not a valid schema for today's transformer.
        await asyncio.to_thread(
            _active_model_features,
            active_binding,
        )
        renew_preparation(force=True)

        def snapshot_progress(done: int, total: int) -> None:
            pct = 2 if total <= 0 else 2 + min(2, int(2 * done / total))
            store.update_progress(job_id, "データを準備中", pct)

        snapshot = await asyncio.to_thread(
            create_sqlite_snapshot,
            ULTIMATE_DB,
            _LOCAL_RETRAIN_SNAPSHOT_CATALOG,
            progress=snapshot_progress,
            heartbeat=renew_preparation,
        )
        renew_preparation(force=True)

        def feature_contract_progress(message: str, pct: int) -> None:
            # Renewal is tied to completed/started pipeline phases.  There is
            # deliberately no independent watchdog that can renew a hung job.
            renew_preparation(force=True)
            store.update_progress(job_id, message, pct)

        selected_features = await asyncio.to_thread(
            derive_local_feature_schema,
            snapshot.path,
            target=request.target,
            training_date_from=request.training_date_from,
            training_date_to=request.training_date_to,
            observe_phase=feature_contract_progress,
        )
        renew_preparation(force=True)
        # Close the read/hash race before the immutable contract is queued.
        if (
            await asyncio.to_thread(_candidate_commit_sha) != candidate_commit
            or await asyncio.to_thread(
                compute_source_tree_sha256,
                _LOCAL_RETRAIN_SOURCE_ROOT,
                heartbeat=renew_preparation,
            )
            != source_tree_sha256
            or await asyncio.to_thread(_active_model_binding) != active_binding
        ):
            raise LocalRetrainError("local-runtime-binding-changed")
        contract = await asyncio.to_thread(
            LocalTrainingContract.create,
            job_id=job_id,
            owner_id=preparing.owner_id,
            snapshot=snapshot,
            target=request.target,
            model_type=request.model_type,
            selected_features=selected_features,
            removed_features=(),
            candidate_commit_sha=candidate_commit,
            source_tree_sha256=source_tree_sha256,
            active_model_id=active_binding[0],
            active_model_sha256=active_binding[1],
            training_parameters=_local_training_parameters(request),
            future_fields=FUTURE_FIELDS,
        )
        renew_preparation(force=True)
        store.queue_job(
            job_id=job_id,
            expected_version=preparing.record_version,
            preparation_token=preparation_token,
            snapshot=snapshot,
            contract=contract,
        )
    except Exception as exc:
        if store is not None:
            try:
                current = store.get_job(job_id)
                if current.state == "preparing":
                    store.fail_preparation(
                        job_id=job_id,
                        expected_version=current.record_version,
                        preparation_token=preparation_token,
                        failure_code=_safe_local_failure_code(
                            exc,
                            "local-training-preparation-failed",
                        ),
                    )
            except Exception:
                logger.warning(
                    "local retrain preparation failure persistence failed for %s",
                    job_id,
                    exc_info=True,
                )
        logger.error(
            "local retrain job %s preparation failed:\n%s",
            job_id,
            traceback.format_exc(),
        )
        return
    await _execute_queued_local_retrain(job_id)


def _start_local_retrain_thread(
    job_id: str,
    *,
    prepare_request: TrainRequest | None,
    preparation_token: str | None = None,
) -> None:
    """Start the bounded local runner without blocking the API event loop."""

    async def run() -> None:
        if prepare_request is None:
            await _execute_queued_local_retrain(job_id)
        else:
            if preparation_token is None:
                raise LocalRetrainError("local-preparation-token-missing")
            await _prepare_and_execute_local_retrain(
                job_id,
                prepare_request,
                preparation_token,
            )

    def background() -> None:
        asyncio.run(run())

    threading.Thread(
        target=background,
        daemon=True,
        name=f"local-train-{job_id[:8]}",
    ).start()


@router.post("/api/train/start")
async def train_start(
    request: TrainRequest,
    current_user: dict = Depends(require_local_training_admin),
    _: None = Depends(require_loopback_training_request),
):
    """Create a durable local job and return before snapshot/training I/O."""

    _require_legacy_model_training_allowed()
    if request.force_sync is not False:
        raise HTTPException(
            status_code=409,
            detail={
                "code": "local-training-sync-forbidden",
                "message": "非同期ジョブとして実行してください",
            },
        )
    if not (request.use_sqlite and request.ultimate_mode and request.use_optimizer):
        raise HTTPException(
            status_code=409,
            detail={
                "code": "local-training-mode-invalid",
                "message": "現在のモデル作成方式を使用してください",
            },
        )
    capability = _training_capability_state()
    if capability.get("enabled") is not True:
        raise HTTPException(
            status_code=409,
            detail={
                "code": "local-training-unavailable",
                "message": "モデル作成を利用できません",
                "reason": capability.get("reason"),
            },
        )

    owner_id = _local_owner_id(current_user)
    normalized_request = request.model_copy(update={"force_sync": False})
    job_id = str(uuid.uuid4())
    preparation_token = new_preparation_token()
    try:
        store, _gateway = _get_local_retrain_runtime()
        job = store.create_job(
            job_id=job_id,
            owner_id=owner_id,
            request_payload=normalized_request.model_dump(),
            preparation_token=preparation_token,
            preparation_ttl_seconds=_LOCAL_PREPARATION_TTL_SECONDS,
        )
    except LocalRetrainConflict as exc:
        detail: dict[str, object] = {
            "code": "train-job-active",
            "message": "別のモデル作成が実行中です",
        }
        if exc.job_id is not None:
            detail["job_id"] = exc.job_id
        raise HTTPException(status_code=409, detail=detail) from exc
    except (LocalRetrainError, sqlite3.Error, OSError) as exc:
        raise HTTPException(
            status_code=503,
            detail="training job state is unavailable",
        ) from exc

    try:
        _start_local_retrain_thread(
            job_id,
            prepare_request=normalized_request,
            preparation_token=preparation_token,
        )
    except Exception as exc:
        try:
            store.fail_preparation(
                job_id=job_id,
                expected_version=job.record_version,
                preparation_token=preparation_token,
                failure_code="local-runner-start-failed",
            )
        except LocalRetrainError:
            pass
        raise HTTPException(
            status_code=503,
            detail="training job runner is unavailable",
        ) from exc
    return {"job_id": job_id, "status": "queued"}


@router.get("/api/train/status/{job_id}")
async def train_job_status(
    job_id: str,
    current_user: dict = Depends(require_local_training_admin),
    _: None = Depends(require_loopback_training_request),
):
    """Return only this Admin's durable job projection without mutating it."""

    owner_id = _local_owner_id(current_user)
    try:
        store, _gateway = _get_local_retrain_runtime()
        record = store.get_job_for_owner(job_id, owner_id)
    except LocalRetrainNotFound:
        record = None
    except (LocalRetrainError, sqlite3.Error, OSError) as exc:
        raise HTTPException(
            status_code=503,
            detail="training job state is unavailable",
        ) from exc

    if record is not None:
        return record.to_status()

    # Preserve a terminal projection for pre-migration local jobs only.
    legacy_job = load_train_job(job_id)
    if legacy_job and str(legacy_job.get("owner_id") or "") == str(
        current_user.get("user_id") or ""
    ):
        return {
            "job_id": job_id,
            "status": legacy_job["status"],
            "progress": legacy_job["progress"],
            "pct": legacy_job.get("pct", 0),
            "result": legacy_job.get("result"),
            "error": legacy_job.get("error"),
        }

    if record is None:
        return {
            "job_id": job_id,
            "status": "not_found",
            "progress": "",
            "pct": 0,
            "result": None,
            "error": "学習ジョブが見つかりません",
        }
