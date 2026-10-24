"""Explicit local feature profiles; legacy artifacts keep their exact contract."""
from types import MappingProxyType

BASELINE_PIPELINE = "local-db-shared-v1"
OPTIMIZED_PIPELINE = "local-db-shared-optimized-v1"
SHARED_PIPELINES = frozenset({BASELINE_PIPELINE, OPTIMIZED_PIPELINE})
PROFILE_FEATURE_COUNTS = MappingProxyType({BASELINE_PIPELINE: 47, OPTIMIZED_PIPELINE: 46})
