"""Deterministic incremental collection-window policy."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, timedelta
from enum import StrEnum, unique
from typing import TYPE_CHECKING, override

from social_media_subscriber.adapters.instance import (
    AdapterPostLocatorRequest,
    AdapterPostRequest,
)

if TYPE_CHECKING:
    from social_media_subscriber.accounts.locator import LinkedInLocator
    from social_media_subscriber.domain.account import Account
    from social_media_subscriber.storage.snapshot import SnapshotState

_INITIAL_DAYS = 7
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
    prior_posts = () if previous is None else previous.posts
    requests: list[AdapterPostRequest] = []
    for account in accounts:
        newest = max(
            (
                post.published_at.date()
                for post in prior_posts
                if post.account_id == account.id
            ),
            default=None,
        )
        if context.override.start_date is not None:
            start_date = context.override.start_date
            end_date = context.override.end_date
            if end_date is None:
                raise WindowInputError(WindowInputErrorCategory.INCOMPLETE)
        else:
            base = context.run_started_at.date() if newest is None else newest
            days = _INITIAL_DAYS if newest is None else _OVERLAP_DAYS
            start_date = base - timedelta(days=days)
            end_date = context.run_started_at.date()
        requests.append(AdapterPostRequest(account, start_date, end_date))
    return tuple(requests)


def build_locator_post_requests(
    locators: tuple[LinkedInLocator, ...],
    context: WindowContext,
) -> tuple[AdapterPostLocatorRequest, ...]:
    """Calculate one initial or explicit Posts discovery request per locator."""
    if context.override.start_date is not None:
        start_date = context.override.start_date
        end_date = context.override.end_date
        if end_date is None:
            raise WindowInputError(WindowInputErrorCategory.INCOMPLETE)
    else:
        end_date = context.run_started_at.date()
        start_date = end_date - timedelta(days=_INITIAL_DAYS)
    return tuple(
        AdapterPostLocatorRequest(locator, start_date, end_date) for locator in locators
    )
