"""Fixed Bright Data LinkedIn transport constants."""

from typing import Final

LINKEDIN_POSTS_DATASET: Final = "gd_lyy3tktm25m4avu764"
MAX_SYNC_BATCH_SIZE: Final = 20
POST_RESULTS_PER_INPUT: Final = 1000
STATUS_RETRY_DELAYS: Final = (1.0, 2.0)
SNAPSHOT_POLL_SECONDS: Final = 5.0
SNAPSHOT_TIMEOUT_SECONDS: Final = 1800.0
