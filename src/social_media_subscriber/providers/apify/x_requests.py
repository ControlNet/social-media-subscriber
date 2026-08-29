"""Validated Xquik Actor inputs."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import timedelta
from typing import TYPE_CHECKING

from social_media_subscriber.accounts.locator import parse_x_locator

if TYPE_CHECKING:
    from datetime import date

    from social_media_subscriber.providers.apify.runner import ActorInput


@dataclass(frozen=True, slots=True)
class ApifyXPostInput:
    """One public X profile and inclusive UTC-date collection window."""

    profile_url: str
    start_date: date
    end_date: date
    is_initial_collection: bool = False

    def as_json(self) -> ActorInput:
        """Return an initial profile scrape or bounded incremental search."""
        if self.start_date > self.end_date:
            message = "X collection window must not be inverted"
            raise ValueError(message)
        locator = parse_x_locator(self.profile_url)
        handle = locator.canonical_url.removeprefix("https://x.com/").removesuffix("/")
        output: ActorInput = {
            "outputVariant": "rich",
            "fieldStyle": "camelCase",
            "outputPreset": "nested",
        }
        if self.is_initial_collection:
            return output | {
                "twitterHandles": [handle],
                "mode": "profileReplies",
            }
        exclusive_end = self.end_date + timedelta(days=1)
        search_term = (
            f"from:{handle} since:{self.start_date.isoformat()} "
            f"until:{exclusive_end.isoformat()}"
        )
        return output | {
            "searchTerms": [search_term],
            "mode": "search",
            "queryType": "Latest",
        }
