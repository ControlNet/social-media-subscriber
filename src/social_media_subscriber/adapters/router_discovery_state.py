"""Run-local state for provider-neutral locator Posts discovery."""

from __future__ import annotations

from typing import TYPE_CHECKING, final

from social_media_subscriber.adapters.instance import (
    ResolvedLocatorPosts,
    UnresolvedLocatorPosts,
)
from social_media_subscriber.adapters.router_discovery_outcomes import (
    DiscoveryFailureCategory,
    DiscoveryLocatorFailed,
    DiscoveryLocatorOutcome,
    DiscoveryLocatorResolved,
    DiscoveryLocatorUnresolved,
    DiscoveryRouterAggregate,
    DiscoveryRouterResult,
)
from social_media_subscriber.adapters.router_discovery_validation import (
    covers_batch,
    merge_posts,
    resolution_mismatch,
)
from social_media_subscriber.adapters.router_outcomes import (
    InstanceHealth,
    InstanceHealthStatus,
    RouterDiagnostic,
    RouterDiagnosticCategory,
    RouterRunStatus,
)
from social_media_subscriber.providers.brightdata.normalization_outcomes import (
    SkippedPostCounts,
)

__all__ = (
    "DiscoveryFailureCategory",
    "DiscoveryLocatorFailed",
    "DiscoveryLocatorOutcome",
    "DiscoveryLocatorResolved",
    "DiscoveryLocatorUnresolved",
    "DiscoveryRouterAggregate",
    "DiscoveryRouterResult",
)

if TYPE_CHECKING:
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
        if not covers_batch(batch, outcomes):
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
                    if resolution_mismatch(
                        outcome, owner, locator_url
                    ) or not merge_posts(posts, collected.posts, account.id):
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
