"""Fixed Bright Data LinkedIn transport constants."""

from typing import Final

PERSON_IDENTITY_DATASET: Final = "gd_l1viktl72bvl7bjuj0"
COMPANY_IDENTITY_DATASET: Final = "gd_l1vikfnt1wgvvqz95w"
LINKEDIN_POSTS_DATASET: Final = "gd_lyy3tktm25m4avu764"
MAX_SYNC_BATCH_SIZE: Final = 20
STATUS_RETRY_DELAYS: Final = (1.0, 2.0)
SNAPSHOT_POLL_SECONDS: Final = 5.0
SNAPSHOT_TIMEOUT_SECONDS: Final = 300.0
