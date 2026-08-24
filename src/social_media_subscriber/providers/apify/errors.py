"""Sanitized typed Apify transport and normalization failures."""

from dataclasses import dataclass
from enum import StrEnum
from typing import Final, override

import httpx2


class ApifyErrorCategory(StrEnum):
    """Stable provider categories consumed by adapter routing."""

    AUTH = "auth"
    QUOTA = "quota"
    INPUT = "input"
    RETRYABLE = "retryable"
    TIMEOUT = "timeout"
    RUN_TERMINAL = "run_terminal"
    SCHEMA = "schema"
    OWNERSHIP = "ownership"
    DUPLICATE = "duplicate"
    POST_URL = "post_url"


_STATUS_CATEGORIES: Final = {
    400: ApifyErrorCategory.INPUT,
    401: ApifyErrorCategory.AUTH,
    402: ApifyErrorCategory.QUOTA,
    403: ApifyErrorCategory.AUTH,
    422: ApifyErrorCategory.INPUT,
    429: ApifyErrorCategory.RETRYABLE,
}


def categorize_http_status(status: int) -> ApifyErrorCategory | None:
    """Map one HTTP status without reading provider response content."""
    if httpx2.codes.is_success(status):
        return None
    category = _STATUS_CATEGORIES.get(status)
    if category is not None:
        return category
    if httpx2.codes.is_server_error(status):
        return ApifyErrorCategory.RETRYABLE
    return ApifyErrorCategory.SCHEMA


@dataclass(frozen=True, slots=True)
class ApifyError(Exception):
    """A provider failure containing no response or credential material."""

    category: ApifyErrorCategory
    status: int | None = None
    run_accepted: bool = False

    @override
    def __str__(self) -> str:
        return f"Apify request failed ({self.category.value})"

    @override
    def __repr__(self) -> str:
        return (
            "ApifyError(category="
            f"{self.category!r}, status={self.status!r}, "
            f"run_accepted={self.run_accepted!r})"
        )
