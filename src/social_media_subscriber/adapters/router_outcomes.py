"""Immutable, secret-safe outcomes from one Router run."""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum, unique
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from social_media_subscriber.adapters.instance import AdapterInstanceOrdinal
    from social_media_subscriber.domain.ids import AccountId, PostId
    from social_media_subscriber.domain.post import Post
    from social_media_subscriber.providers.brightdata.normalization_outcomes import (
        SkippedPostCounts,
    )
    from social_media_subscriber.providers.brightdata.source_record import (
        BrightDataLinkedInPostSourceRecord,
    )


@unique
class InstanceHealthStatus(StrEnum):
    """Run-local instance availability without credential identity."""

    HEALTHY = "healthy"
    QUOTA_EXHAUSTED = "quota_exhausted"
    INVALID_CREDENTIAL = "invalid_credential"


@unique
class AccountRouteFailureCategory(StrEnum):
    """Machine-readable Account failures used by partial-run policy."""

    INVALID_ACCOUNT = "invalid_account"
    ACCOUNT_NOT_FOUND = "account_not_found"
    POOL_EXHAUSTED = "pool_exhausted"
    ACCEPTED_SNAPSHOT_FAILED = "accepted_snapshot_failed"
    UNSUPPORTED_CAPABILITY = "unsupported_capability"


@unique
class RouterDiagnosticCategory(StrEnum):
    """Secret-safe run decisions exposed to operators."""

    QUOTA_DISABLED = "quota_disabled"
    CREDENTIAL_DISABLED = "credential_disabled"
    SCHEMA_ABORT = "schema_abort"


@unique
class RouterRunStatus(StrEnum):
    """Aggregate collection disposition."""

    SUCCESS = "success"
    PARTIAL = "partial"
    ABORTED = "aborted"


@dataclass(frozen=True, slots=True)
class InstanceHealth:
    """Final immutable health for one opaque run-local ordinal."""

    ordinal: AdapterInstanceOrdinal
    status: InstanceHealthStatus


@dataclass(frozen=True, slots=True)
class RouterDiagnostic:
    """A classified decision containing no provider credential material."""

    category: RouterDiagnosticCategory
    instance_ordinal: AdapterInstanceOrdinal | None = None


@dataclass(frozen=True, slots=True)
class AccountRouteSucceeded:
    """Stable Post identities collected for one Account."""

    account_id: AccountId
    post_ids: tuple[PostId, ...]


@dataclass(frozen=True, slots=True)
class AccountRouteFailed:
    """One Account-scoped terminal collection failure."""

    account_id: AccountId
    category: AccountRouteFailureCategory


type AccountRouteOutcome = AccountRouteSucceeded | AccountRouteFailed


@dataclass(frozen=True, slots=True)
class RouterAggregate:
    """Counts and disposition required by downstream partial-run policy."""

    status: RouterRunStatus
    succeeded_accounts: int
    failed_accounts: int
    disabled_instances: int


@dataclass(frozen=True, slots=True)
class RouterResult:
    """Immutable output of one collection run."""

    aggregate: RouterAggregate
    accounts: tuple[AccountRouteOutcome, ...]
    posts: tuple[Post, ...]
    source_records: tuple[BrightDataLinkedInPostSourceRecord, ...]
    skipped: SkippedPostCounts
    health: tuple[InstanceHealth, ...]
    diagnostics: tuple[RouterDiagnostic, ...]
