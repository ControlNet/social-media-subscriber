"""Fixed Apify actor and bounded execution policy."""

from typing import Final

APIFY_ACTOR: Final = "harvestapi~linkedin-profile-posts"
ACTOR_WAIT_SECONDS: Final = 0
RUN_POLL_SECONDS: Final = 5.0
RUN_TIMEOUT_SECONDS: Final = 1_800.0
