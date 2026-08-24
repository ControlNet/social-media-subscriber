"""Task-specific typed contracts for Bright Data Adapter orchestration."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Protocol

if TYPE_CHECKING:
    from collections.abc import Sequence
    from datetime import datetime

    from social_media_subscriber.domain.ids import AccountId
    from social_media_subscriber.domain.post import Post
    from social_media_subscriber.providers.brightdata.models import (
        BrightDataPost,
    )
    from social_media_subscriber.providers.brightdata.requests import PostDiscoveryInput


class BrightDataClientContract(Protocol):
    """Provider methods used by the LinkedIn Adapter."""

    async def aclose(self) -> None:
        """Close the credential-bound transport."""
        ...

    async def collect_person_posts(
        self, inputs: Sequence[PostDiscoveryInput]
    ) -> tuple[BrightDataPost, ...]:
        """Collect complete personal-profile post records."""
        ...

    async def collect_company_posts(
        self, inputs: Sequence[PostDiscoveryInput]
    ) -> tuple[BrightDataPost, ...]:
        """Collect complete company-page post records."""
        ...


@dataclass(frozen=True, slots=True)
class BrightDataAdapterConfig:
    """Run-scoped deterministic values shared by credential instances."""

    first_seen_at: datetime


@dataclass(frozen=True, slots=True)
class CollectedAccountPosts:
    """Complete canonical normalization result for one Account."""

    account_id: AccountId
    posts: tuple[Post, ...]


@dataclass(frozen=True, slots=True)
class BrightDataPostBatchResult:
    """Ordered complete results for every requested Account."""

    accounts: tuple[CollectedAccountPosts, ...]
