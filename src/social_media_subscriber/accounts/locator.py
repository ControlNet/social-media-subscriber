"""Strict parsing for public LinkedIn account locators."""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Final
from urllib.parse import unquote_to_bytes, urlsplit

from social_media_subscriber.accounts.errors import (
    AccountInputError,
    AccountInputErrorCategory,
    AccountInputField,
)
from social_media_subscriber.domain.platform import AccountKind, Platform

_HOST_PATTERN: Final = re.compile(
    r"(?:linkedin\.com|www\.linkedin\.com|[a-z]{2,3}\.linkedin\.com)\Z",
    re.ASCII,
)
_PATH_PATTERN: Final = re.compile(r"/(in|company)/([^/]+)/?\Z", re.ASCII)
_PERCENT_ESCAPE_PATTERN: Final = re.compile(r"%(?:[0-9A-Fa-f]{2})")
_ENCODED_DOT_PATTERN: Final = re.compile(r"%2e", re.IGNORECASE)
_ASCII_CONTROL_BOUNDARY: Final = 32
_ASCII_DELETE: Final = 127
_ACCOUNT_KIND_BY_PATH: Final = {
    "in": AccountKind.PERSON,
    "company": AccountKind.COMPANY,
}


@dataclass(frozen=True, slots=True)
class LinkedInLocator:
    """Canonical public LinkedIn identity locator."""

    platform: Platform
    kind: AccountKind
    canonical_url: str


def _invalid_locator() -> AccountInputError:
    return AccountInputError(
        category=AccountInputErrorCategory.INVALID_ACCOUNT_URL,
        field=AccountInputField.ACCOUNTS,
    )


def _has_malformed_percent_escape(value: str) -> bool:
    without_valid_escapes = _PERCENT_ESCAPE_PATTERN.sub("", value)
    return "%" in without_valid_escapes


def parse_linkedin_locator(raw: str) -> LinkedInLocator:
    """Parse one untrusted URL into a canonical LinkedIn locator."""
    value = raw.strip()
    if not value or any(
        ord(character) < _ASCII_CONTROL_BOUNDARY for character in value
    ):
        raise _invalid_locator()

    try:
        parsed = urlsplit(value)
        hostname = parsed.hostname
        port = parsed.port
    except ValueError:
        raise _invalid_locator() from None

    if (
        parsed.scheme.casefold() != "https"
        or hostname is None
        or parsed.username is not None
        or parsed.password is not None
        or port is not None
        or parsed.netloc.casefold() != hostname.casefold()
        or _HOST_PATTERN.fullmatch(hostname.casefold()) is None
        or "\\" in parsed.path
    ):
        raise _invalid_locator()

    path_match = _PATH_PATTERN.fullmatch(parsed.path)
    if path_match is None:
        raise _invalid_locator()

    path_kind, segment = path_match.groups()
    if (
        _has_malformed_percent_escape(segment)
        or _ENCODED_DOT_PATTERN.search(segment) is not None
    ):
        raise _invalid_locator()

    try:
        decoded_segment = unquote_to_bytes(segment).decode("utf-8")
    except UnicodeDecodeError:
        raise _invalid_locator() from None

    if (
        not decoded_segment
        or decoded_segment in {".", ".."}
        or "/" in decoded_segment
        or "\\" in decoded_segment
        or any(
            ord(character) < _ASCII_CONTROL_BOUNDARY or ord(character) == _ASCII_DELETE
            for character in decoded_segment
        )
    ):
        raise _invalid_locator()

    return LinkedInLocator(
        platform=Platform.LINKEDIN,
        kind=_ACCOUNT_KIND_BY_PATH[path_kind],
        canonical_url=f"https://www.linkedin.com/{path_kind}/{segment}/",
    )
