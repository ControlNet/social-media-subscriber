"""Immutable outcomes for provider-neutral locator Posts discovery."""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum, unique
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from social_media_subscriber.accounts.locator import LinkedInLocator
    from social_media_subscriber.adapters.router_outcomes import (
        InstanceHealth,
        RouterDiagnostic,
        RouterRunStatus,
    )
    from social_media_subscriber.domain.account import Account
    from social_media_subscriber.domain.ids import AccountId
    from social_media_subscriber.domain.post import Post
    from social_media_subscriber.providers.brightdata.normalization_outcomes import (
        SkippedPostCounts,
    )
    from social_media_subscriber.providers.brightdata.source_record import (
        BrightDataLinkedInPostSourceRecord,
    )


@unique
class DiscoveryFailureCategory(StrEnum):
    """Locator-scoped terminal failure retained inside collection orchestration."""

    POOL_EXHAUSTED = "pool_exhausted"
    ACCEPTED_SNAPSHOT_FAILED = "accepted_snapshot_failed"
    UNSUPPORTED_CAPABILITY = "unsupported_capability"


@dataclass(frozen=True, slots=True)
class DiscoveryLocatorResolved:
    """One locator resolved to a canonical Account."""

    locator: LinkedInLocator
    account_id: AccountId


@dataclass(frozen=True, slots=True)
class DiscoveryLocatorUnresolved:
    """One locator completed without sufficient Account evidence."""

    locator: LinkedInLocator


@dataclass(frozen=True, slots=True)
class DiscoveryLocatorFailed:
    """One locator ended in a classified Router failure."""

    locator: LinkedInLocator
    category: DiscoveryFailureCategory


type DiscoveryLocatorOutcome = (
    DiscoveryLocatorResolved | DiscoveryLocatorUnresolved | DiscoveryLocatorFailed
)


@dataclass(frozen=True, slots=True)
class DiscoveryRouterAggregate:
    """Counts and disposition for one locator discovery run."""

    status: RouterRunStatus
    resolved_locators: int
    unresolved_locators: int
    failed_locators: int
    disabled_instances: int


@dataclass(frozen=True, slots=True)
class DiscoveryRouterResult:
    """Internal collection result for unknown locator discovery."""

    aggregate: DiscoveryRouterAggregate
    accounts: tuple[Account, ...]
    outcomes: tuple[DiscoveryLocatorOutcome, ...]
    posts: tuple[Post, ...]
    source_records: tuple[BrightDataLinkedInPostSourceRecord, ...]
    skipped: SkippedPostCounts
    health: tuple[InstanceHealth, ...]
    diagnostics: tuple[RouterDiagnostic, ...]
