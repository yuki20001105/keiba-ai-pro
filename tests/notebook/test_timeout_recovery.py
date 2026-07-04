from __future__ import annotations

import json

from scripts.notebook_audit_runner import CACHE_PARQUET_FILES, MODE_PROFILE, ensure_cache_layout


def test_mode_profiles_are_defined() -> None:
    assert set(MODE_PROFILE.keys()) == {"fast", "audit", "prod"}


def test_mode_profile_values() -> None:
    assert MODE_PROFILE["fast"]["env"]["N_TRIALS"] == "10"
    assert MODE_PROFILE["fast"]["env"]["N_SPLITS"] == "3"
    assert MODE_PROFILE["fast"]["env"]["BOOSTING_TYPE"] == "gbdt"

    assert MODE_PROFILE["audit"]["env"]["N_TRIALS"] == "3"
    assert MODE_PROFILE["audit"]["env"]["N_SPLITS"] == "2"
    assert MODE_PROFILE["audit"]["env"]["NUM_BOOST_ROUND"] == "200"

    assert MODE_PROFILE["prod"]["env"]["N_TRIALS"] == "100"
    assert MODE_PROFILE["prod"]["env"]["N_SPLITS"] == "5"
    assert MODE_PROFILE["prod"]["env"]["BOOSTING_TYPE"] == "dart"


def test_cache_layout_and_keys(tmp_path) -> None:
    root = tmp_path
    (root / "cache").mkdir(parents=True, exist_ok=True)
    (root / "keiba" / "data").mkdir(parents=True, exist_ok=True)
    (root / "notebooks").mkdir(parents=True, exist_ok=True)
    (root / "keiba" / "data" / "keiba_ultimate.db").write_bytes(b"db")

    notes: list[str] = []
    ensure_cache_layout(root, "audit", notes)

    # Invalid 0-byte parquet placeholders should not be created.
    for name in CACHE_PARQUET_FILES:
        assert not (root / "cache" / name).exists()

    index = json.loads((root / "cache" / "cache_index.json").read_text(encoding="utf-8"))
    assert set(index.keys()) >= {"data_version", "feature_schema_hash", "notebook_step", "mode"}
    assert index["mode"] == "audit"


def test_cache_invalidation_on_mode_change(tmp_path) -> None:
    root = tmp_path
    (root / "cache").mkdir(parents=True, exist_ok=True)
    (root / "keiba" / "data").mkdir(parents=True, exist_ok=True)
    (root / "notebooks").mkdir(parents=True, exist_ok=True)
    (root / "keiba" / "data" / "keiba_ultimate.db").write_bytes(b"db")

    notes: list[str] = []
    ensure_cache_layout(root, "audit", notes)
    ensure_cache_layout(root, "prod", notes)
    assert any("mode変更" in n for n in notes)
