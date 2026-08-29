"""Credential-bound adapter instances and classified collection outcomes."""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum, unique
from typing import TYPE_CHECKING, NewType, Protocol, override

if TYPE_CHECKING:
    from datetime import date

    from pydantic import SecretStr

    from social_media_subscriber.adapters.protocol import AdapterDriver
    from social_media_subscriber.domain.account import Account
    from social_media_subscriber.domain.ids import AccountId
    from social_media_subscriber.domain.post import Post

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


@dataclass(frozen=True, slots=True)
class AdapterRequestError(ValueError):
    """Reject invalid collection requests without exposing Account details."""

    category: AdapterRequestErrorCategory

    @override
    def __str__(self) -> str:
        return f"invalid Adapter post request ({self.category.value})"


@dataclass(frozen=True, slots=True)
class AdapterPostRequest:
    """Provider-neutral Account, inclusive dates, and collection lifecycle."""

    account: Account
    start_date: date
    end_date: date
    is_initial_collection: bool = False

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
class CollectedAccount:
    """Complete canonical post result for one Account."""

    account_id: AccountId
    posts: tuple[Post, ...]


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
    """Provider schema or ownership corruption requiring a run abort."""


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


class AdapterInstanceFactory(Protocol):
    """Construct one approved Adapter instance for one authorized credential."""

    def create(
        self,
        credential: SecretStr,
        ordinal: AdapterInstanceOrdinal,
    ) -> AdapterInstance:
        """Bind a credential without exposing or fingerprinting it."""
        ...


@dataclass(frozen=True, slots=True)
class AdapterInstanceSpec:
    """One ordered credential-bound instance to construct."""

    driver_class: type[AdapterDriver]
    factory: AdapterInstanceFactory
    credential: SecretStr
