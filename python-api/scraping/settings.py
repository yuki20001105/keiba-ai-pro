from __future__ import annotations

# INV-07 guardrails: never below 1.0s
MIN_REQUEST_INTERVAL_SEC = 1.0

# Retry defaults for downloader
MAX_RETRY = 3
BACKOFF_BASE_SEC = 2.0

# Queue statuses
QUEUE_PENDING = "PENDING"
QUEUE_RUNNING = "RUNNING"
QUEUE_SUCCESS = "SUCCESS"
QUEUE_FAILED = "FAILED"
QUEUE_SKIP = "SKIP"
