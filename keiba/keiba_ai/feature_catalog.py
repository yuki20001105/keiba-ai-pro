"""Feature Catalog — keiba_ai 特徴量カタログ読み込みクラス

feature_catalog.yaml の単一真実源からデータを提供する。
constants.py の FUTURE_FIELDS / UNNECESSARY_COLUMNS と互換のインターフェースを持つ。
"""
from __future__ import annotations

import hashlib
from pathlib import Path
from typing import Any

import yaml

_DEFAULT_PATH = Path(__file__).parent.parent / "feature_catalog.yaml"


class FeatureCatalog:
    """feature_catalog.yaml をロードして特徴量情報を提供するクラス。"""

    def __init__(self, data: dict[str, Any]) -> None:
        self._data = data

    # ------------------------------------------------------------------
    # Factory
    # ------------------------------------------------------------------

    @classmethod
    def load(cls, path: str | Path = _DEFAULT_PATH) -> "FeatureCatalog":
        """YAML ファイルからカタログを読み込む。"""
        with open(path, encoding="utf-8") as f:
            data = yaml.safe_load(f)
        return cls(data)

    # ------------------------------------------------------------------
    # Metadata
    # ------------------------------------------------------------------

    @property
    def version(self) -> str:
        return str(self._data.get("version", "unknown"))

    def hash(self) -> str:
        """カタログ内容の SHA-256 ハッシュ（モデルバージョン検証用）。"""
        raw = yaml.dump(self._data, allow_unicode=True, sort_keys=True).encode()
        return hashlib.sha256(raw).hexdigest()

    # ------------------------------------------------------------------
    # 未来情報フィールド
    # ------------------------------------------------------------------

    def future_fields(self) -> frozenset[str]:
        """当該レース結果として予測前に存在しないフィールドの集合。"""
        return frozenset(self._data.get("future_fields", []))

    # ------------------------------------------------------------------
    # 不要列
    # ------------------------------------------------------------------

    def unnecessary_columns(self) -> tuple[str, ...]:
        """学習・推論の両フェーズで除外する列のタプル。"""
        rows = self._data.get("unnecessary_columns", [])
        return tuple(row["name"] if isinstance(row, dict) else row for row in rows)

    def unnecessary_columns_with_reasons(self) -> list[dict[str, str]]:
        """name + reason を含むリスト（UI 表示用）。"""
        rows = self._data.get("unnecessary_columns", [])
        return [
            {"name": r["name"], "reason": r.get("reason", "")}
            if isinstance(r, dict)
            else {"name": r, "reason": ""}
            for r in rows
        ]

    # ------------------------------------------------------------------
    # エンジニアリング特徴量
    # ------------------------------------------------------------------

    def engineered_features(self) -> list[dict[str, Any]]:
        """全エンジニアリング特徴量のリスト（enabled 問わず）。"""
        return list(self._data.get("engineered_features", []))

    def enabled_features(self) -> list[str]:
        """enabled=true の特徴量名リスト。"""
        return [
            f["name"]
            for f in self.engineered_features()
            if f.get("enabled", True)
        ]

    def disabled_features(self) -> list[str]:
        """enabled=false の特徴量名リスト。"""
        return [
            f["name"]
            for f in self.engineered_features()
            if not f.get("enabled", True)
        ]

    def is_enabled(self, name: str) -> bool:
        """指定した特徴量が enabled かどうかを返す。"""
        for f in self.engineered_features():
            if f["name"] == name:
                return bool(f.get("enabled", True))
        return True  # カタログ未登録の列はデフォルト有効

    def get_stage_features(self, stage: str) -> list[dict[str, Any]]:
        """指定したステージの特徴量リストを返す。"""
        return [f for f in self.engineered_features() if f.get("stage") == stage]

    def stages(self) -> list[str]:
        """使用されているステージ名一覧（順序保持・重複除去）。"""
        seen: list[str] = []
        for f in self.engineered_features():
            s = f.get("stage", "")
            if s and s not in seen:
                seen.append(s)
        return seen

    # ------------------------------------------------------------------
    # スクレイプフィールド
    # ------------------------------------------------------------------

    def scraped_fields(self) -> dict[str, list[str]]:
        """スクレイプフィールド辞書 {"race": [...], "horse": [...]}。名前のみ返す。"""
        raw = self._data.get("scraped_fields", {})
        return {
            category: [
                f["name"] if isinstance(f, dict) else f
                for f in fields
                if not (isinstance(f, str) and f.startswith("#"))
            ]
            for category, fields in raw.items()
        }

    def scraped_fields_with_descriptions(self) -> dict[str, list[dict[str, str]]]:
        """スクレイプフィールド辞書。各フィールドは {name, description} 形式で返す。"""
        raw = self._data.get("scraped_fields", {})
        result: dict[str, list[dict[str, str]]] = {}
        for category, fields in raw.items():
            entries = []
            for f in fields:
                if isinstance(f, dict):
                    entries.append({"name": f.get("name", ""), "description": f.get("description", "")})
                elif isinstance(f, str) and not f.startswith("#"):
                    entries.append({"name": f, "description": ""})
            result[category] = entries
        return result

    # ------------------------------------------------------------------
    # サマリー統計
    # ------------------------------------------------------------------

    def summary(self) -> dict[str, Any]:
        """カタログ全体のサマリー統計辞書。"""
        eng = self.engineered_features()
        by_stage: dict[str, dict[str, int]] = {}
        for f in eng:
            stage = f.get("stage", "unknown")
            if stage not in by_stage:
                by_stage[stage] = {"total": 0, "enabled": 0, "disabled": 0}
            by_stage[stage]["total"] += 1
            if f.get("enabled", True):
                by_stage[stage]["enabled"] += 1
            else:
                by_stage[stage]["disabled"] += 1

        scraped = self.scraped_fields()
        return {
            "version": self.version,
            "hash": self.hash(),
            "future_fields_count": len(self.future_fields()),
            "scraped_fields_count": sum(len(v) for v in scraped.values()),
            "engineered_total": len(eng),
            "engineered_enabled": sum(1 for f in eng if f.get("enabled", True)),
            "engineered_disabled": sum(1 for f in eng if not f.get("enabled", True)),
            "unnecessary_columns_count": len(self.unnecessary_columns()),
            "by_stage": by_stage,
        }

    # ------------------------------------------------------------------
    # Raw data access
    # ------------------------------------------------------------------

    def raw(self) -> dict[str, Any]:
        """YAML から読み込んだ生データを返す。"""
        return self._data
