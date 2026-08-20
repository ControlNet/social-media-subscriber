"""Short-lived mutable accumulator for a single Router call."""

from __future__ import annotations

from typing import TYPE_CHECKING, final

from social_media_subscriber.adapters.instance import (
    AccountRejectionCategory,
    CollectedAccount,
    RejectedAccount,
)
from social_media_subscriber.adapters.router_outcomes import (
    AccountRouteFailed,
    AccountRouteFailureCategory,
    AccountRouteOutcome,
    AccountRouteSucceeded,
    InstanceHealth,
    InstanceHealthStatus,
    RouterAggregate,
    RouterDiagnostic,
    RouterDiagnosticCategory,
    RouterResult,
    RouterRunStatus,
)
from social_media_subscriber.domain.post_merge import PostMergeConflictError, merge_post
from social_media_subscriber.providers.brightdata.normalization_outcomes import (
    SkippedPostCounts,
)

if TYPE_CHECKING:
    from social_media_subscriber.adapters.instance import (
        AdapterAccountOutcome,
        AdapterBatch,
        AdapterInstance,
        AdapterInstanceOrdinal,
    )
    from social_media_subscriber.domain.ids import AccountId, PlatformPostId, PostId
    from social_media_subscriber.domain.post import Post
    from social_media_subscriber.providers.brightdata.source_record import (
        BrightDataLinkedInPostSourceRecord,
    )


@final
class RouterRunState:
    """Accumulate decisions only until one immutable RouterResult is returned."""

    __slots__ = (
        "diagnostics",
        "health",
        "posts",
        "routed",
        "skipped",
        "source_records",
    )

    health: dict[AdapterInstanceOrdinal, InstanceHealthStatus]
    routed: dict[AccountId, AccountRouteOutcome]
    posts: dict[PostId, Post]
    source_records: dict[PlatformPostId, BrightDataLinkedInPostSourceRecord]
    skipped: SkippedPostCounts
    diagnostics: list[RouterDiagnostic]

    def __init__(self) -> None:
        """Create a mutable state object owned by exactly one Router call."""
        self.health = {}
        self.routed = {}
        self.posts = {}
        self.source_records = {}
        self.skipped = SkippedPostCounts()
        self.diagnostics = []

    @classmethod
    def for_instances(cls, instances: tuple[AdapterInstance, ...]) -> RouterRunState:
        """Create fresh health with no state retained from an earlier run."""
        state = cls()
        state.health.update(
            (instance.ordinal, InstanceHealthStatus.HEALTHY) for instance in instances
        )
        return state

    def is_healthy(self, instance: AdapterInstance) -> bool:
        """Return whether this run may call the opaque instance."""
        return self.health[instance.ordinal] is InstanceHealthStatus.HEALTHY

    def accept(
        self,
        batch: AdapterBatch,
        outcomes: tuple[AdapterAccountOutcome, ...],
    ) -> bool:
        """Accept a complete identity-consistent response or classify corruption."""
        expected = {account.id for account in batch.accounts}
        received = {outcome.account_id for outcome in outcomes}
        if (
            received != expected
            or len(received) != len(outcomes)
            or not self._sources_are_consistent(outcomes)
        ):
            self._abort_schema()
            return False
        try:
            for outcome in outcomes:
                match outcome:
                    case CollectedAccount() as collected:
                        if not self._accept_collected(collected):
                            return False
                    case RejectedAccount() as rejected:
                        self._accept_rejected(rejected)
        except PostMergeConflictError:
            self._abort_schema()
            return False
        return True

    def _sources_are_consistent(
        self,
        outcomes: tuple[AdapterAccountOutcome, ...],
    ) -> bool:
        pending = self.source_records.copy()
        for outcome in outcomes:
            match outcome:
                case CollectedAccount(
                    account_id=account_id,
                    source_records=source_records,
                ):
                    for source in source_records:
                        existing = pending.get(source.platform_post_id)
                        if source.account_id != account_id or (
                            existing is not None
                            and (
                                existing.account_id != source.account_id
                                or existing.payload_sha256 != source.payload_sha256
                            )
                        ):
                            return False
                        pending[source.platform_post_id] = source
                case RejectedAccount():
                    pass
        return True

    def _accept_collected(self, outcome: CollectedAccount) -> bool:
        for source in outcome.source_records:
            self.source_records[source.platform_post_id] = source
        self.skipped = SkippedPostCounts(
            replies=self.skipped.replies + outcome.skipped.replies,
            reposts=self.skipped.reposts + outcome.skipped.reposts,
            quotes=self.skipped.quotes + outcome.skipped.quotes,
            unknown=self.skipped.unknown + outcome.skipped.unknown,
        )
        post_ids: list[PostId] = []
        for post in outcome.posts:
            if post.account_id != outcome.account_id:
                self._abort_schema()
                return False
            existing = self.posts.get(post.id)
            self.posts[post.id] = (
                post if existing is None else merge_post(existing, post)
            )
            post_ids.append(post.id)
        self.routed[outcome.account_id] = AccountRouteSucceeded(
            outcome.account_id,
            tuple(sorted(set(post_ids))),
        )
        return True

    def _accept_rejected(self, outcome: RejectedAccount) -> None:
        match outcome.category:
            case AccountRejectionCategory.INVALID:
                failure = AccountRouteFailureCategory.INVALID_ACCOUNT
            case AccountRejectionCategory.NOT_FOUND:
                failure = AccountRouteFailureCategory.ACCOUNT_NOT_FOUND
        self.routed[outcome.account_id] = AccountRouteFailed(
            outcome.account_id,
            failure,
        )

    def fail_batch(
        self,
        batch: AdapterBatch,
        category: AccountRouteFailureCategory,
    ) -> None:
        """Apply one terminal Account-scoped failure to a whole batch."""
        for account in batch.accounts:
            self.routed[account.id] = AccountRouteFailed(account.id, category)

    def disable_quota(self, instance: AdapterInstance) -> None:
        """Disable one quota-exhausted instance only in this state object."""
        self.health[instance.ordinal] = InstanceHealthStatus.QUOTA_EXHAUSTED
        self.diagnostics.append(
            RouterDiagnostic(
                RouterDiagnosticCategory.QUOTA_DISABLED,
                instance.ordinal,
            )
        )

    def disable_credential(self, instance: AdapterInstance) -> None:
        """Disable one invalid credential and emit a configuration diagnostic."""
        self.health[instance.ordinal] = InstanceHealthStatus.INVALID_CREDENTIAL
        self.diagnostics.append(
            RouterDiagnostic(
                RouterDiagnosticCategory.CREDENTIAL_DISABLED,
                instance.ordinal,
            )
        )

    def result(
        self,
        status: RouterRunStatus,
        *,
        include_posts: bool,
    ) -> RouterResult:
        """Freeze the deterministic public result for partial-run policy."""
        outcomes = tuple(self.routed[key] for key in sorted(self.routed, key=str))
        health = tuple(
            InstanceHealth(ordinal, self.health[ordinal])
            for ordinal in sorted(self.health)
        )
        return RouterResult(
            aggregate=RouterAggregate(
                status=status,
                succeeded_accounts=sum(
                    isinstance(outcome, AccountRouteSucceeded) for outcome in outcomes
                ),
                failed_accounts=sum(
                    isinstance(outcome, AccountRouteFailed) for outcome in outcomes
                ),
                disabled_instances=sum(
                    item.status is not InstanceHealthStatus.HEALTHY for item in health
                ),
            ),
            accounts=outcomes,
            posts=(
                tuple(self.posts[key] for key in sorted(self.posts, key=str))
                if include_posts
                else ()
            ),
            source_records=(
                tuple(
                    self.source_records[key]
                    for key in sorted(self.source_records, key=str)
                )
                if include_posts
                else ()
            ),
            skipped=self.skipped if include_posts else SkippedPostCounts(),
            health=health,
            diagnostics=tuple(self.diagnostics),
        )

    def _abort_schema(self) -> None:
        self.diagnostics.append(RouterDiagnostic(RouterDiagnosticCategory.SCHEMA_ABORT))
