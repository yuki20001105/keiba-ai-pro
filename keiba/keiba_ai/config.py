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
    )
    return AppConfig(netkeiba=netkeiba, storage=storage, training=training)
