"""Sanitized typed Bright Data transport failures."""

from dataclasses import dataclass
from enum import StrEnum
from typing import Final, override

import httpx2


class BrightDataErrorCategory(StrEnum):
    """Stable categories consumed by adapter routing."""

    AUTH = "auth"
    QUOTA = "quota"
    NOT_FOUND = "not_found"
    INPUT = "input"
    RETRYABLE = "retryable"
    TIMEOUT = "timeout"
    SNAPSHOT_TIMEOUT = "snapshot_timeout"
    SNAPSHOT_TERMINAL = "snapshot_terminal"
    SCHEMA = "schema"


_STATUS_CATEGORIES: Final = {
    400: BrightDataErrorCategory.INPUT,
    401: BrightDataErrorCategory.AUTH,
    402: BrightDataErrorCategory.QUOTA,
    403: BrightDataErrorCategory.AUTH,
    404: BrightDataErrorCategory.NOT_FOUND,
    422: BrightDataErrorCategory.INPUT,
    429: BrightDataErrorCategory.RETRYABLE,
}


def categorize_http_status(status: int) -> BrightDataErrorCategory | None:
    """Map one HTTP status without reading provider response content."""
    if httpx2.codes.is_success(status):
        return None
    category = _STATUS_CATEGORIES.get(status)
    if category is not None:
        return category
    if httpx2.codes.is_server_error(status):
        return BrightDataErrorCategory.RETRYABLE
    return BrightDataErrorCategory.SCHEMA


@dataclass(frozen=True, slots=True)
class BrightDataError(Exception):
    """A provider failure containing no response or credential material."""

    category: BrightDataErrorCategory
    status: int | None = None
    snapshot_accepted: bool = False

    @override
    def __str__(self) -> str:
        """Render only the stable category."""
        return f"Bright Data request failed ({self.category})"

    @override
    def __repr__(self) -> str:
        """Render only sanitized typed fields."""
        return (
            "BrightDataError(category="
            f"{self.category!r}, status={self.status!r}, "
            f"snapshot_accepted={self.snapshot_accepted!r})"
        )
