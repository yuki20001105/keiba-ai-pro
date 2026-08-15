from datetime import datetime, timezone
from pathlib import Path
import sys

import pytest


PYTHON_API = Path(__file__).resolve().parents[1]
if str(PYTHON_API) not in sys.path:
    sys.path.insert(0, str(PYTHON_API))

from observation.capture import _training_cutoff  # noqa: E402
from observation.contracts import ObservationContractError  # noqa: E402


def test_training_cutoff_accepts_exact_bundle_date() -> None:
    actual = _training_cutoff({"training_date_to": "2026-03-22"})

    assert actual == datetime(2026, 3, 22, 14, 59, 59, 999999, tzinfo=timezone.utc)


def test_training_cutoff_uses_matching_exact_artifact_range_for_legacy_month() -> None:
    actual = _training_cutoff(
        {"training_date_from": "2016-01", "training_date_to": "2026-03"},
        Path("model_speed_deviation_lightgbm_20160101_20260322_20260418_1928.joblib"),
    )

    assert actual == datetime(2026, 3, 22, 14, 59, 59, 999999, tzinfo=timezone.utc)


@pytest.mark.parametrize(
    "bundle,path",
    [
        ({"training_date_to": "2026-03"}, None),
        (
            {"training_date_from": "2016-01", "training_date_to": "2026-03"},
            Path("model_speed_deviation_lightgbm_20160101_20260228_candidate.joblib"),
        ),
        (
            {"training_date_from": "2017-01", "training_date_to": "2026-03"},
            Path("model_speed_deviation_lightgbm_20160101_20260322_candidate.joblib"),
        ),
    ],
)
def test_training_cutoff_rejects_missing_or_mismatched_exact_date(
    bundle: dict[str, str], path: Path | None
) -> None:
    with pytest.raises(ObservationContractError):
        _training_cutoff(bundle, path)
