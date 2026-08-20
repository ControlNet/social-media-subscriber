"""Task-specific typed contracts for Bright Data Adapter orchestration."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Protocol

from social_media_subscriber.providers.brightdata.errors import (
    BrightDataError,
    BrightDataErrorCategory,
)

if TYPE_CHECKING:
    from collections.abc import Sequence
    from datetime import date, datetime

    from social_media_subscriber.domain.account import Account
    from social_media_subscriber.domain.ids import AccountId
    from social_media_subscriber.domain.post import Post
    from social_media_subscriber.providers.brightdata.models import (
        BrightDataCompanyIdentity,
        BrightDataPersonIdentity,
        BrightDataPost,
    )
    from social_media_subscriber.providers.brightdata.normalization_outcomes import (
        SkippedPostCounts,
    )
    from social_media_subscriber.providers.brightdata.requests import PostDiscoveryInput
    from social_media_subscriber.providers.brightdata.source_record import (
        BrightDataLinkedInPostSourceRecord,
    )


class BrightDataClientContract(Protocol):
    """Provider methods used by the LinkedIn Adapter."""

    async def resolve_person_identities(
        self, urls: Sequence[str]
    ) -> tuple[BrightDataPersonIdentity, ...]:
        """Resolve person records for canonical LinkedIn URLs."""
        ...

    async def resolve_company_identities(
        self, urls: Sequence[str]
    ) -> tuple[BrightDataCompanyIdentity, ...]:
        """Resolve company records for canonical LinkedIn URLs."""
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
class FixedCollectionWindow:
    """Inclusive collection dates used by Router-driven batches."""

    start_date: date
    end_date: date

    def __post_init__(self) -> None:
        """Reject an inverted provider date window."""
        if self.start_date > self.end_date:
            raise BrightDataError(BrightDataErrorCategory.INPUT)


@dataclass(frozen=True, slots=True)
class BrightDataAdapterConfig:
    """Run-scoped deterministic values shared by credential instances."""

    collection_window: FixedCollectionWindow
    first_seen_at: datetime


@dataclass(frozen=True, slots=True)
class AccountPostRequest:
    """One Account and its normalized inclusive provider date window."""

    account: Account
    start_date: date
    end_date: date

    def __post_init__(self) -> None:
        """Reject an inverted per-Account date window."""
        if self.start_date > self.end_date:
            raise BrightDataError(BrightDataErrorCategory.INPUT)


@dataclass(frozen=True, slots=True)
class CollectedAccountPosts:
    """Complete source and canonical normalization result for one Account."""

    account_id: AccountId
    source_records: tuple[BrightDataLinkedInPostSourceRecord, ...]
    posts: tuple[Post, ...]
    skipped: SkippedPostCounts


@dataclass(frozen=True, slots=True)
class BrightDataPostBatchResult:
    """Ordered complete results for every requested Account."""

    accounts: tuple[CollectedAccountPosts, ...]
