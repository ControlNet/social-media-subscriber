"""Credential-bound adapter instances and classified collection outcomes."""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum, unique
from typing import TYPE_CHECKING, NewType, Protocol, override

from social_media_subscriber.providers.brightdata.normalization_outcomes import (
    SkippedPostCounts,
)

if TYPE_CHECKING:
    from datetime import date

    from pydantic import SecretStr

    from social_media_subscriber.accounts.locator import LinkedInLocator
    from social_media_subscriber.adapters.protocol import AdapterDriver
    from social_media_subscriber.domain.account import Account
    from social_media_subscriber.domain.ids import AccountId
    from social_media_subscriber.domain.post import Post
    from social_media_subscriber.providers.brightdata.normalization_outcomes import (
        AccountIdentityOutcome,
    )
    from social_media_subscriber.providers.brightdata.source_record import (
        BrightDataLinkedInPostSourceRecord,
    )

AdapterInstanceOrdinal = NewType("AdapterInstanceOrdinal", int)


@unique
class AccountRejectionCategory(StrEnum):
    """Account-scoped provider failures that must not rotate credentials."""

    INVALID = "invalid_account"
    NOT_FOUND = "account_not_found"


@unique
class AdapterRequestErrorCategory(StrEnum):
    """Machine-readable post-request integrity failure."""

    INVERTED_WINDOW = "inverted_window"
    CONFLICTING_WINDOW = "conflicting_window"
    MIXED_LOCATOR_KIND = "mixed_locator_kind"


@dataclass(frozen=True, slots=True)
class AdapterRequestError(ValueError):
    """Reject invalid collection requests without exposing Account details."""

    category: AdapterRequestErrorCategory

    @override
    def __str__(self) -> str:
        return f"invalid Adapter post request ({self.category.value})"


@dataclass(frozen=True, slots=True)
class AdapterPostRequest:
    """Provider-neutral Account plus inclusive post collection dates."""

    account: Account
    start_date: date
    end_date: date

    def __post_init__(self) -> None:
        """Reject inverted dates before an Adapter instance can run."""
        if self.start_date > self.end_date:
            raise AdapterRequestError(AdapterRequestErrorCategory.INVERTED_WINDOW)


@dataclass(frozen=True, slots=True)
class AdapterPostLocatorRequest:
    """Provider-neutral locator plus inclusive Posts discovery dates."""

    locator: LinkedInLocator
    start_date: date
    end_date: date

    def __post_init__(self) -> None:
        """Reject inverted dates before an Adapter instance can run."""
        if self.start_date > self.end_date:
            raise AdapterRequestError(AdapterRequestErrorCategory.INVERTED_WINDOW)


@dataclass(frozen=True, slots=True)
class AdapterBatch:
    """One kind-homogeneous post-request batch bounded by the Router."""

    requests: tuple[AdapterPostRequest, ...]

    @property
    def accounts(self) -> tuple[Account, ...]:
        """Return ordered Accounts for result identity validation."""
        return tuple(request.account for request in self.requests)


@dataclass(frozen=True, slots=True)
class AdapterPostLocatorBatch:
    """One kind-homogeneous deduplicated locator batch bounded by the Router."""

    requests: tuple[AdapterPostLocatorRequest, ...]

    def __post_init__(self) -> None:
        """Canonicalize equivalent requests before provider I/O."""
        if self.requests and any(
            request.locator.kind is not self.requests[0].locator.kind
            for request in self.requests
        ):
            raise AdapterRequestError(AdapterRequestErrorCategory.MIXED_LOCATOR_KIND)
        unique_requests: dict[str, AdapterPostLocatorRequest] = {}
        for request in self.requests:
            existing = unique_requests.get(request.locator.canonical_url)
            if existing is not None and (
                existing.start_date != request.start_date
                or existing.end_date != request.end_date
            ):
                raise AdapterRequestError(
                    AdapterRequestErrorCategory.CONFLICTING_WINDOW
                )
            if existing is None:
                unique_requests[request.locator.canonical_url] = request
        object.__setattr__(self, "requests", tuple(unique_requests.values()))


@dataclass(frozen=True, slots=True)
class ResolvedLocatorPosts:
    """One discovered locator with its resolved Account collection result."""

    locator: LinkedInLocator
    account: Account
    collected: CollectedAccount


@dataclass(frozen=True, slots=True)
class UnresolvedLocatorPosts:
    """One locator whose Posts cannot establish an Account."""

    locator: LinkedInLocator


type AdapterPostLocatorOutcome = ResolvedLocatorPosts | UnresolvedLocatorPosts


@dataclass(frozen=True, slots=True)
class LocatorPostsBatchCompleted:
    """A fully classified Posts discovery response for every locator."""

    outcomes: tuple[AdapterPostLocatorOutcome, ...]


@dataclass(frozen=True, slots=True)
class AdapterIdentityBatch:
    """One kind-homogeneous locator batch with immutable known identities."""

    locators: tuple[LinkedInLocator, ...]
    known_accounts: tuple[Account, ...]


@dataclass(frozen=True, slots=True)
class CollectedAccount:
    """Complete source and canonical post result for one Account."""

    account_id: AccountId
    posts: tuple[Post, ...]
    source_records: tuple[BrightDataLinkedInPostSourceRecord, ...] = ()
    skipped: SkippedPostCounts = field(default_factory=SkippedPostCounts)


@dataclass(frozen=True, slots=True)
class RejectedAccount:
    """One invalid or missing Account returned without credential rotation."""

    account_id: AccountId
    category: AccountRejectionCategory


type AdapterAccountOutcome = CollectedAccount | RejectedAccount


@dataclass(frozen=True, slots=True)
class BatchCompleted:
    """A fully classified provider response for every batch Account."""

    outcomes: tuple[AdapterAccountOutcome, ...]


@dataclass(frozen=True, slots=True)
class IdentityBatchCompleted:
    """A fully classified identity response preserving locator order."""

    outcomes: tuple[AccountIdentityOutcome, ...]


@dataclass(frozen=True, slots=True)
class RetryableBatchFailure:
    """A transient pre-acceptance failure eligible for instance failover."""


@dataclass(frozen=True, slots=True)
class QuotaBatchFailure:
    """Run-local quota exhaustion that disables only one instance."""


@dataclass(frozen=True, slots=True)
class InvalidCredentialBatchFailure:
    """Credential rejection that disables one instance for the run."""


@dataclass(frozen=True, slots=True)
class SchemaBatchFailure:
    """Provider schema or identity corruption requiring a run abort."""


@dataclass(frozen=True, slots=True)
class AcceptedSnapshotBatchFailure:
    """Failure after remote acceptance; ownership forbids retriggering."""


type AdapterAttempt = (
    BatchCompleted
    | RetryableBatchFailure
    | QuotaBatchFailure
    | InvalidCredentialBatchFailure
    | SchemaBatchFailure
    | AcceptedSnapshotBatchFailure
)

type AdapterIdentityAttempt = (
    IdentityBatchCompleted
    | RetryableBatchFailure
    | QuotaBatchFailure
    | InvalidCredentialBatchFailure
    | SchemaBatchFailure
    | AcceptedSnapshotBatchFailure
)

type AdapterPostLocatorAttempt = (
    LocatorPostsBatchCompleted
    | RetryableBatchFailure
    | QuotaBatchFailure
    | InvalidCredentialBatchFailure
    | SchemaBatchFailure
    | AcceptedSnapshotBatchFailure
)


class AdapterInstance(Protocol):
    """One opaque credential-bound runtime instance."""

    driver_class: type[AdapterDriver]
    ordinal: AdapterInstanceOrdinal

    async def aclose(self) -> None:
        """Close resources owned by this credential-bound instance."""
        ...

    async def collect(self, batch: AdapterBatch) -> AdapterAttempt:
        """Collect one bounded homogeneous batch after client-level retries."""
        ...

    async def discover_posts(
        self,
        batch: AdapterPostLocatorBatch,
    ) -> AdapterPostLocatorAttempt:
        """Discover Accounts and Posts for one bounded homogeneous locator batch."""
        ...

    async def resolve_identity(
        self,
        batch: AdapterIdentityBatch,
    ) -> AdapterIdentityAttempt:
        """Resolve one bounded homogeneous locator batch."""
        ...


class AdapterInstanceFactory(Protocol):
    """Construct one approved Adapter instance for one authorized credential."""

    def create(
        self,
        credential: SecretStr,
        ordinal: AdapterInstanceOrdinal,
    ) -> AdapterInstance:
        """Bind a credential without exposing or fingerprinting it."""
        ...
