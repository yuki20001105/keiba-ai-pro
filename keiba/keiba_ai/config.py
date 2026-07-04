from __future__ import annotations
from dataclasses import dataclass
from pathlib import Path
import yaml

@dataclass(frozen=True)
class NetkeibaConfig:
    base: str
    race_list_url: str
    race_list_sub_url: str
    shutuba_url: str
    result_url: str
    user_agent: str
    timeout_sec: int
    min_sleep_sec: float
    max_sleep_sec: float
    max_pages_per_run: int
    cache_html: bool

@dataclass(frozen=True)
class StorageConfig:
    data_dir: Path
    sqlite_path: Path
    html_dir: Path
    logs_dir: Path
    models_dir: Path

@dataclass(frozen=True)
class TrainingConfig:
    target: str
    test_split_days: int
    random_seed: int
    fast_mode: bool = False
    audit_mode: bool = False
    prefer_gpu: bool = True
    device_type: str = "auto"  # "auto", "gpu", "cpu"
    n_trials_fast: int = 10
    n_trials_prod: int = 100
    n_trials_audit: int = 3
    n_splits_fast: int = 3
    n_splits_prod: int = 5
    n_splits_audit: int = 2
    boosting_type_fast: str = "gbdt"
    boosting_type_prod: str = "dart"
    boosting_type_audit: str = "gbdt"
    num_boost_round_fast: int = 1000
    num_boost_round_prod: int = 3000
    num_boost_round_audit: int = 200
    optuna_n_jobs: int = 1
    optuna_gc_after_trial: bool = True
    gpu_tier_high_min: int = 3060
    gpu_tier_low_max: int = 3050
    n_trials_low_gpu: int = 30
    model_type: str = "lightgbm"            # "lightgbm" or "logistic"
    lgbm_early_stopping_rounds: int = 100   # Early Stopping の胲ち強り回数
    lgbm_num_boost_round: int = 3000        # 最大ブースティング回数

@dataclass(frozen=True)
class AppConfig:
    netkeiba: NetkeibaConfig
    storage: StorageConfig
    training: TrainingConfig

def load_config(path: str | Path) -> AppConfig:
    path = Path(path)
    raw = yaml.safe_load(path.read_text(encoding="utf-8"))

    nk = raw["netkeiba"]
    st = raw["storage"]
    tr = raw["training"]

    storage = StorageConfig(
        data_dir=Path(st["data_dir"]),
        sqlite_path=Path(st["sqlite_path"]),
        html_dir=Path(st["html_dir"]),
        logs_dir=Path(st["logs_dir"]),
        models_dir=Path(st["models_dir"]),
    )
    # ensure dirs
    storage.data_dir.mkdir(parents=True, exist_ok=True)
    storage.html_dir.mkdir(parents=True, exist_ok=True)
    storage.logs_dir.mkdir(parents=True, exist_ok=True)
    storage.models_dir.mkdir(parents=True, exist_ok=True)

    netkeiba = NetkeibaConfig(
        base=nk["base"].rstrip("/"),
        race_list_url=nk["race_list_url"],
        race_list_sub_url=nk["race_list_sub_url"],
        shutuba_url=nk["shutuba_url"],
        result_url=nk["result_url"],
        user_agent=nk["user_agent"],
        timeout_sec=int(nk["timeout_sec"]),
        min_sleep_sec=float(nk["min_sleep_sec"]),
        max_sleep_sec=float(nk["max_sleep_sec"]),
        max_pages_per_run=int(nk["max_pages_per_run"]),
        cache_html=bool(nk["cache_html"]),
    )
    training = TrainingConfig(
        target=str(tr["target"]),
        test_split_days=int(tr["test_split_days"]),
        random_seed=int(tr["random_seed"]),
        fast_mode=bool(tr.get("fast_mode", False)),
        audit_mode=bool(tr.get("audit_mode", False)),
        prefer_gpu=bool(tr.get("prefer_gpu", True)),
        device_type=str(tr.get("device_type", "auto")).lower(),
        n_trials_fast=int(tr.get("n_trials_fast", 10)),
        n_trials_prod=int(tr.get("n_trials_prod", 100)),
        n_trials_audit=int(tr.get("n_trials_audit", 3)),
        n_splits_fast=int(tr.get("n_splits_fast", 3)),
        n_splits_prod=int(tr.get("n_splits_prod", 5)),
        n_splits_audit=int(tr.get("n_splits_audit", 2)),
        boosting_type_fast=str(tr.get("boosting_type_fast", "gbdt")),
        boosting_type_prod=str(tr.get("boosting_type_prod", "dart")),
        boosting_type_audit=str(tr.get("boosting_type_audit", "gbdt")),
        num_boost_round_fast=int(tr.get("num_boost_round_fast", 1000)),
        num_boost_round_prod=int(tr.get("num_boost_round_prod", 3000)),
        num_boost_round_audit=int(tr.get("num_boost_round_audit", 200)),
        optuna_n_jobs=int(tr.get("optuna_n_jobs", 1)),
        optuna_gc_after_trial=bool(tr.get("optuna_gc_after_trial", True)),
        gpu_tier_high_min=int(tr.get("gpu_tier_high_min", 3060)),
        gpu_tier_low_max=int(tr.get("gpu_tier_low_max", 3050)),
        n_trials_low_gpu=int(tr.get("n_trials_low_gpu", 30)),
        model_type=str(tr.get("model_type", "lightgbm")),
        lgbm_early_stopping_rounds=int(tr.get("lgbm_early_stopping_rounds", 100)),
        lgbm_num_boost_round=int(tr.get("lgbm_num_boost_round", 3000)),
    )
    return AppConfig(netkeiba=netkeiba, storage=storage, training=training)
