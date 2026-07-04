from __future__ import annotations

# Data quality error codes (scraping stage)
E001_RACE_IDS_EMPTY = "E001"      # required violation / empty list
E002_RACE_ID_DUPLICATE = "E002"   # duplicate race_id detected
E003_RACE_ID_TYPE = "E003"        # non-string race_id detected
E004_RACE_ID_LENGTH = "E004"      # invalid race_id length
E005_RACE_ID_FORMAT = "E005"      # invalid race_id format
E201_TASK_EMPTY_OR_SAVE_FAILED = "E201"   # task-level save failed or empty result
E202_TASK_EXEC_EXCEPTION = "E202"         # task executor runtime exception
E099_UNKNOWN = "E099"             # fallback
