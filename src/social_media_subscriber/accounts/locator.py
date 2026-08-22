"""Strict parsing for public LinkedIn account locators."""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Final
from urllib.parse import urlsplit

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
    if "%" in segment:
        raise _invalid_locator()

    if (
        not segment
        or segment in {".", ".."}
        or "\\" in segment
        or any(
            ord(character) < _ASCII_CONTROL_BOUNDARY or ord(character) == _ASCII_DELETE
            for character in segment
        )
    ):
        raise _invalid_locator()

    return LinkedInLocator(
        platform=Platform.LINKEDIN,
        kind=_ACCOUNT_KIND_BY_PATH[path_kind],
        canonical_url=f"https://www.linkedin.com/{path_kind}/{segment}/",
    )
