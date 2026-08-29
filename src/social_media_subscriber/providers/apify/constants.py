"""Fixed Apify actors and bounded execution policy."""

from typing import Final

APIFY_ACTOR: Final = "harvestapi~linkedin-profile-posts"
APIFY_X_ACTOR: Final = "xquik~x-tweet-scraper"
APIFY_X_REPORT_KEY: Final = "run-report"
ACTOR_WAIT_SECONDS: Final = 0
RUN_POLL_SECONDS: Final = 5.0
RUN_TIMEOUT_SECONDS: Final = 1_800.0
