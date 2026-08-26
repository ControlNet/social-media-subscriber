"""Shared X identity and URL canonicalization."""

from __future__ import annotations

import re
from dataclasses import dataclass
from enum import StrEnum
from typing import TYPE_CHECKING, Final, override
from urllib.parse import urlsplit

from social_media_subscriber.domain.time import canonical_utc

if TYPE_CHECKING:
    from datetime import datetime

_HOST_PATTERN: Final = re.compile(
    r"(?:x\.com|www\.x\.com|twitter\.com|www\.twitter\.com|mobile\.twitter\.com)\Z",
    re.ASCII,
)
X_RESERVED_HANDLES: Final = frozenset(
    {
        "compose",
        "explore",
        "home",
        "i",
        "intent",
        "login",
        "messages",
        "notifications",
        "search",
        "settings",
        "signup",
    }
)
_X_RESERVED_HANDLE_ALTERNATION: Final = "|".join(sorted(X_RESERVED_HANDLES))
X_CANONICAL_HANDLE_BODY: Final = (
    rf"(?!(?:{_X_RESERVED_HANDLE_ALTERNATION})/)[a-z0-9_]{{1,15}}"
)
_X_PLATFORM_POST_ID_BODY: Final = r"[1-9][0-9]*"
X_PLATFORM_POST_ID_PATTERN: Final = rf"^{_X_PLATFORM_POST_ID_BODY}$"
_STATUS_ID_PATTERN: Final = re.compile(X_PLATFORM_POST_ID_PATTERN, re.ASCII)
_STATUS_PATH_PATTERN: Final = re.compile(
    rf"/([A-Za-z0-9_]{{1,15}})/status/({_X_PLATFORM_POST_ID_BODY})/?\Z",
    re.ASCII,
)
_UNSAFE_URL_PATTERN: Final = re.compile(
    r"(?:[\x00-\x1f\x7f\\]|%(?:[01][0-9a-f]|7f|2f|5c|2e))",
    re.IGNORECASE,
)
X_POST_URL_PATTERN: Final = (
    rf"^https://x\.com/{X_CANONICAL_HANDLE_BODY}/status/"
    rf"{_X_PLATFORM_POST_ID_BODY}$"
)


class XPostUrlErrorCategory(StrEnum):
    """Stable categories for rejected X post URLs."""

    INVALID = "invalid"


@dataclass(frozen=True, slots=True)
class XPostUrlError(ValueError):
    """Reject an X post URL without reflecting the raw value."""

    category: XPostUrlErrorCategory = XPostUrlErrorCategory.INVALID

    @override
    def __str__(self) -> str:
        return f"invalid X post URL ({self.category.value})"


def canonical_platform_post_id(value: str) -> str:
    """Return one canonical numeric X post identity."""
    if _STATUS_ID_PATTERN.fullmatch(value) is None:
        raise XPostUrlError
    return value


def canonical_post_url(value: str, *, platform_post_id: str | None = None) -> str:
    """Return one provider-independent canonical X status URL."""
    try:
        parsed = urlsplit(value)
        hostname = parsed.hostname
        port = parsed.port
    except ValueError:
        hostname = None
        port = -1
        parsed = urlsplit("")
    path_match = _STATUS_PATH_PATTERN.fullmatch(parsed.path)
    if (
        _UNSAFE_URL_PATTERN.search(value) is not None
        or parsed.scheme.casefold() != "https"
        or hostname is None
        or _HOST_PATTERN.fullmatch(hostname.casefold()) is None
        or parsed.username is not None
        or parsed.password is not None
        or port is not None
        or parsed.query
        or parsed.fragment
        or path_match is None
    ):
        raise XPostUrlError
    handle, post_id = path_match.groups()
    if handle.casefold() in X_RESERVED_HANDLES:
        raise XPostUrlError
    canonical_id = canonical_platform_post_id(post_id)
    if platform_post_id is not None:
        expected_id = canonical_platform_post_id(platform_post_id)
        if canonical_id != expected_id:
            raise XPostUrlError
    return f"https://x.com/{handle.casefold()}/status/{canonical_id}"


def canonical_post_timestamp(value: datetime) -> datetime:
    """Return the UTC publication second shared by X providers."""
    return canonical_utc(value).replace(microsecond=0)
