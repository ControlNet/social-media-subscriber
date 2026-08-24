"""Validated Apify LinkedIn actor inputs."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from datetime import date


@dataclass(frozen=True, slots=True)
class ApifyPostInput:
    """One public profile and inclusive local collection window."""

    profile_url: str
    start_date: date
    end_date: date

    def as_json(self) -> dict[str, bool | int | str | list[str]]:
        """Return the exact actor input with costly nested scraping disabled."""
        return {
            "targetUrls": [self.profile_url],
            "maxPosts": 0,
            "postedLimitDate": self.start_date.isoformat(),
            "includeQuotePosts": True,
            "includeReposts": True,
            "scrapeReactions": False,
            "postNestedReactions": False,
            "scrapeComments": False,
            "postNestedComments": False,
        }
