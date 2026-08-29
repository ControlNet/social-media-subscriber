"""Deterministic incremental collection-window policy."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, timedelta
from enum import StrEnum, unique
from typing import TYPE_CHECKING, override

from social_media_subscriber.adapters.instance import AdapterPostRequest
from social_media_subscriber.domain.platform import earliest_collection_date

if TYPE_CHECKING:
    from social_media_subscriber.domain.account import Account
    from social_media_subscriber.domain.ids import AccountId
    from social_media_subscriber.storage.snapshot import SnapshotState

_OVERLAP_DAYS = 3


@unique
class WindowInputErrorCategory(StrEnum):
    """Closed invalid explicit-window categories."""

    INCOMPLETE = "incomplete_window"
    INVERTED = "inverted_window"


@dataclass(frozen=True, slots=True)
class WindowInputError(Exception):
    """Reject an invalid override without rendering its values."""

    category: WindowInputErrorCategory

    @override
    def __str__(self) -> str:
        return f"invalid collection window ({self.category.value})"


@dataclass(frozen=True, slots=True)
class ExplicitWindow:
    """A complete bounded override, or the incremental default marker."""

    start_date: date | None
    end_date: date | None

    @classmethod
    def parse(cls, start_date: date | None, end_date: date | None) -> ExplicitWindow:
        """Parse two optional boundary values atomically."""
        if (start_date is None) != (end_date is None):
            raise WindowInputError(WindowInputErrorCategory.INCOMPLETE)
        if start_date is not None and end_date is not None and start_date > end_date:
            raise WindowInputError(WindowInputErrorCategory.INVERTED)
        return cls(start_date, end_date)


@dataclass(frozen=True, slots=True)
class WindowContext:
    """One run date and optional override shared by every Account."""

    run_started_at: datetime
    override: ExplicitWindow


def build_post_requests(
    accounts: tuple[Account, ...],
    previous: SnapshotState | None,
    context: WindowContext,
) -> tuple[AdapterPostRequest, ...]:
    """Calculate one inclusive provider-neutral request per Account."""
    prior_account_ids: frozenset[AccountId] = (
        frozenset()
        if previous is None
        else frozenset(account.id for account in previous.accounts)
    )
    run_date = context.run_started_at.date()
    requests: list[AdapterPostRequest] = []
    for account in accounts:
        is_initial_collection = account.id not in prior_account_ids
        if context.override.start_date is not None:
            start_date = context.override.start_date
            end_date = context.override.end_date
            if end_date is None:
                raise WindowInputError(WindowInputErrorCategory.INCOMPLETE)
        else:
            start_date = (
                run_date - timedelta(days=_OVERLAP_DAYS)
                if account.id in prior_account_ids
                else earliest_collection_date(account.platform)
            )
            end_date = run_date
        requests.append(
            AdapterPostRequest(
                account,
                start_date,
                end_date,
                is_initial_collection=is_initial_collection,
            )
        )
    return tuple(requests)
