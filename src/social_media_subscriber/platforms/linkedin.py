"""Shared LinkedIn identity and URL canonicalization."""

from __future__ import annotations

import re
from dataclasses import dataclass
from enum import StrEnum
from typing import TYPE_CHECKING, Final, override
from urllib.parse import urlsplit, urlunsplit

from social_media_subscriber.domain.time import canonical_utc

if TYPE_CHECKING:
    from datetime import datetime

    from social_media_subscriber.serialization.json import JsonValue

_LINKEDIN_HOST: Final = re.compile(
    r"(?:linkedin\.com|www\.linkedin\.com|[a-z]{2,3}\.linkedin\.com)\Z",
    re.ASCII,
)
_UNSAFE_URL: Final = re.compile(
    r"(?:[\x00-\x1f\x7f\\]|%(?:[01][0-9a-f]|7f|2f|5c|2e))", re.IGNORECASE
)
_ACTIVITY_URN_PREFIX: Final = "urn:li:activity:"
_NUMERIC_ACTIVITY_ID: Final = re.compile(r"[0-9]+\Z", re.ASCII)


class LinkedInPostUrlErrorCategory(StrEnum):
    """Stable categories for rejected LinkedIn post URLs."""

    INVALID = "invalid"


@dataclass(frozen=True, slots=True)
class LinkedInPostUrlError(ValueError):
    """Reject a provider post URL without reflecting the raw value."""

    category: LinkedInPostUrlErrorCategory = LinkedInPostUrlErrorCategory.INVALID

    @override
    def __str__(self) -> str:
        return f"invalid LinkedIn post URL ({self.category.value})"


def canonical_post_url(value: str, *, platform_post_id: str | None = None) -> str:
    """Return a query-free provider-independent LinkedIn post URL."""
    try:
        parsed = urlsplit(value)
        hostname = parsed.hostname
        port = parsed.port
    except ValueError:
        hostname = None
        port = -1
        parsed = urlsplit("")
    if (
        _UNSAFE_URL.search(value) is not None
        or parsed.scheme.casefold() != "https"
        or hostname is None
        or _LINKEDIN_HOST.fullmatch(hostname.casefold()) is None
        or parsed.username is not None
        or parsed.password is not None
        or port not in {None, 443}
        or not parsed.path.startswith(("/posts/", "/feed/update/"))
    ):
        raise LinkedInPostUrlError
    validated = urlunsplit(("https", "www.linkedin.com", parsed.path, "", ""))
    if platform_post_id is None:
        return validated
    canonical_id = canonical_platform_post_id(platform_post_id)
    if _NUMERIC_ACTIVITY_ID.fullmatch(canonical_id) is None:
        return validated
    return f"https://www.linkedin.com/feed/update/urn:li:activity:{canonical_id}/"


def canonical_platform_post_id(value: str) -> str:
    """Align Bright Data activity URNs with Apify numeric activity IDs."""
    normalized = value.strip()
    folded = normalized.casefold()
    if folded.startswith(_ACTIVITY_URN_PREFIX):
        return normalized[len(_ACTIVITY_URN_PREFIX) :]
    return normalized


def canonical_post_timestamp(value: datetime) -> datetime:
    """Return the UTC publication second shared by all LinkedIn providers."""
    return canonical_utc(value).replace(microsecond=0)


def canonical_post_type(value: str, *, is_repost: bool) -> str:
    """Return a normalized type, correcting generic posts with repost evidence."""
    normalized = value.strip().casefold()
    if normalized == "post" and is_repost:
        return "repost"
    return normalized


def canonical_media_items(value: JsonValue) -> list[JsonValue]:
    """Represent provider media as a list of objects with a stable URL key."""
    items = value if isinstance(value, list) else [value]
    normalized: list[JsonValue] = []
    for item in items:
        if isinstance(item, str):
            normalized.append({"url": item})
        elif item is not None:
            normalized.append(item)
    return normalized


def has_meaningful_value(value: JsonValue) -> bool:
    """Return whether an optional provider marker contains positive evidence."""
    match value:
        case None | False | "":
            return False
        case list() | dict():
            return bool(value)
        case _:
            return True
