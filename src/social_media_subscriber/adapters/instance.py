"""Credential-bound adapter instances and classified collection outcomes."""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum, unique
from typing import TYPE_CHECKING, NewType, Protocol

from social_media_subscriber.providers.brightdata.normalization_outcomes import (
    SkippedPostCounts,
)

if TYPE_CHECKING:
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


@dataclass(frozen=True, slots=True)
class AdapterBatch:
    """One kind-homogeneous batch bounded by the Router."""

    accounts: tuple[Account, ...]


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


class AdapterInstance(Protocol):
    """One opaque credential-bound runtime instance."""

    driver_class: type[AdapterDriver]
    ordinal: AdapterInstanceOrdinal

    async def collect(self, batch: AdapterBatch) -> AdapterAttempt:
        """Collect one bounded homogeneous batch after client-level retries."""
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
