"""Run-local state for provider-neutral locator Posts discovery."""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum, unique
from typing import TYPE_CHECKING, final

from social_media_subscriber.adapters.instance import (
    ResolvedLocatorPosts,
    UnresolvedLocatorPosts,
)
from social_media_subscriber.adapters.router_outcomes import (
    InstanceHealth,
    InstanceHealthStatus,
    RouterDiagnostic,
    RouterDiagnosticCategory,
    RouterRunStatus,
)
from social_media_subscriber.domain.post_merge import PostMergeConflictError, merge_post
from social_media_subscriber.providers.brightdata.normalization_outcomes import (
    SkippedPostCounts,
)

if TYPE_CHECKING:
    from social_media_subscriber.accounts.locator import LinkedInLocator
    from social_media_subscriber.adapters.instance import (
        AdapterInstance,
        AdapterInstanceOrdinal,
        AdapterPostLocatorBatch,
        AdapterPostLocatorOutcome,
        AdapterPostLocatorRequest,
    )
    from social_media_subscriber.domain.account import Account
    from social_media_subscriber.domain.ids import (
        AccountId,
        PlatformPostId,
        PostId,
    )
    from social_media_subscriber.domain.post import Post
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


@final
class DiscoveryRouterState:
    """Accumulate discovery output atomically until an immutable result is frozen."""

    def __init__(self, instances: tuple[AdapterInstance, ...]) -> None:
        """Seed fresh health and empty provider-neutral discovery output."""
        self.health: dict[AdapterInstanceOrdinal, InstanceHealthStatus] = {
            instance.ordinal: InstanceHealthStatus.HEALTHY for instance in instances
        }
        self.accounts: dict[AccountId, Account] = {}
        self.account_owners: dict[AccountId, str] = {}
        self.outcomes: dict[str, DiscoveryLocatorOutcome] = {}
        self.posts: dict[PostId, Post] = {}
        self.source_records: dict[
            PlatformPostId, BrightDataLinkedInPostSourceRecord
        ] = {}
        self.skipped = SkippedPostCounts()
        self.diagnostics: list[RouterDiagnostic] = []
        self.aborted = False

    def is_healthy(self, instance: AdapterInstance) -> bool:
        """Return whether one credential instance remains eligible in this run."""
        return self.health[instance.ordinal] is InstanceHealthStatus.HEALTHY

    def accept(
        self,
        batch: AdapterPostLocatorBatch,
        outcomes: tuple[AdapterPostLocatorOutcome, ...],
    ) -> bool:
        """Accept exactly one ownership-consistent outcome per requested locator."""
        if not _covers_batch(batch, outcomes):
            self.abort_schema()
            return False

        accounts = self.accounts.copy()
        owners = self.account_owners.copy()
        routed = self.outcomes.copy()
        posts = self.posts.copy()
        sources = self.source_records.copy()
        skipped = self.skipped
        for outcome in outcomes:
            locator_url = outcome.locator.canonical_url
            match outcome:
                case ResolvedLocatorPosts(
                    locator=locator,
                    account=account,
                    collected=collected,
                ):
                    owner = owners.get(account.id)
                    if _resolution_mismatch(
                        outcome, owner, locator_url
                    ) or not _merge_posts(posts, collected.posts, account.id):
                        self.abort_schema()
                        return False
                    for source in collected.source_records:
                        existing_source = sources.get(source.platform_post_id)
                        if source.account_id != account.id or (
                            existing_source is not None
                            and (
                                existing_source.account_id != source.account_id
                                or existing_source.payload_sha256
                                != source.payload_sha256
                            )
                        ):
                            self.abort_schema()
                            return False
                        sources[source.platform_post_id] = source
                    accounts[account.id] = account
                    owners[account.id] = locator_url
                    routed[locator_url] = DiscoveryLocatorResolved(locator, account.id)
                    skipped = _sum_skipped(skipped, collected.skipped)
                case UnresolvedLocatorPosts(locator=locator):
                    routed[locator_url] = DiscoveryLocatorUnresolved(locator)

        self.accounts = accounts
        self.account_owners = owners
        self.outcomes = routed
        self.posts = posts
        self.source_records = sources
        self.skipped = skipped
        return True

    def fail_batch(
        self,
        batch: AdapterPostLocatorBatch,
        category: DiscoveryFailureCategory,
    ) -> None:
        """Apply one terminal failure category to every locator in a batch."""
        for request in batch.requests:
            self.outcomes[request.locator.canonical_url] = DiscoveryLocatorFailed(
                request.locator,
                category,
            )

    def disable_quota(self, instance: AdapterInstance) -> None:
        """Disable one quota-exhausted credential only for this call."""
        self.health[instance.ordinal] = InstanceHealthStatus.QUOTA_EXHAUSTED
        self.diagnostics.append(
            RouterDiagnostic(RouterDiagnosticCategory.QUOTA_DISABLED, instance.ordinal)
        )

    def disable_credential(self, instance: AdapterInstance) -> None:
        """Disable one rejected credential only for this call."""
        self.health[instance.ordinal] = InstanceHealthStatus.INVALID_CREDENTIAL
        self.diagnostics.append(
            RouterDiagnostic(
                RouterDiagnosticCategory.CREDENTIAL_DISABLED,
                instance.ordinal,
            )
        )

    def abort_schema(self) -> None:
        """Suppress every discovery candidate and retain one redacted diagnostic."""
        self.aborted = True
        self.diagnostics = [RouterDiagnostic(RouterDiagnosticCategory.SCHEMA_ABORT)]

    def result(
        self,
        requests: tuple[AdapterPostLocatorRequest, ...],
    ) -> DiscoveryRouterResult:
        """Freeze deterministic output in canonical locator and identity order."""
        health = tuple(
            InstanceHealth(ordinal, self.health[ordinal])
            for ordinal in sorted(self.health)
        )
        outcomes = (
            ()
            if self.aborted
            else tuple(
                self.outcomes[request.locator.canonical_url] for request in requests
            )
        )
        resolved = sum(isinstance(item, DiscoveryLocatorResolved) for item in outcomes)
        unresolved = sum(
            isinstance(item, DiscoveryLocatorUnresolved) for item in outcomes
        )
        failed = sum(isinstance(item, DiscoveryLocatorFailed) for item in outcomes)
        status = (
            RouterRunStatus.ABORTED
            if self.aborted
            else RouterRunStatus.PARTIAL
            if unresolved or failed
            else RouterRunStatus.SUCCESS
        )
        include_output = not self.aborted
        return DiscoveryRouterResult(
            DiscoveryRouterAggregate(
                status,
                resolved,
                unresolved,
                failed,
                sum(item.status is not InstanceHealthStatus.HEALTHY for item in health),
            ),
            tuple(self.accounts[key] for key in sorted(self.accounts, key=str))
            if include_output
            else (),
            outcomes,
            tuple(self.posts[key] for key in sorted(self.posts, key=str))
            if include_output
            else (),
            tuple(
                self.source_records[key] for key in sorted(self.source_records, key=str)
            )
            if include_output
            else (),
            self.skipped if include_output else SkippedPostCounts(),
            health,
            tuple(self.diagnostics),
        )


def _sum_skipped(
    first: SkippedPostCounts,
    second: SkippedPostCounts,
) -> SkippedPostCounts:
    return SkippedPostCounts(
        replies=first.replies + second.replies,
        reposts=first.reposts + second.reposts,
        quotes=first.quotes + second.quotes,
        unknown=first.unknown + second.unknown,
    )


def _covers_batch(
    batch: AdapterPostLocatorBatch,
    outcomes: tuple[AdapterPostLocatorOutcome, ...],
) -> bool:
    expected = {request.locator.canonical_url for request in batch.requests}
    received = {outcome.locator.canonical_url for outcome in outcomes}
    return received == expected and len(received) == len(outcomes)


def _resolution_mismatch(
    outcome: ResolvedLocatorPosts,
    owner: str | None,
    locator_url: str,
) -> bool:
    return (
        outcome.collected.account_id != outcome.account.id
        or outcome.account.kind is not outcome.locator.kind
        or (owner is not None and owner != locator_url)
    )


def _merge_posts(
    destination: dict[PostId, Post],
    incoming: tuple[Post, ...],
    account_id: AccountId,
) -> bool:
    try:
        for post in incoming:
            if post.account_id != account_id:
                return False
            existing = destination.get(post.id)
            destination[post.id] = (
                post if existing is None else merge_post(existing, post)
            )
    except PostMergeConflictError:
        return False
    return True
