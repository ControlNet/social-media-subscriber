"""Strict parsing for supported public account locators."""

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
from social_media_subscriber.platforms.x import X_RESERVED_HANDLES

_LINKEDIN_HOST_PATTERN: Final = re.compile(
    r"(?:linkedin\.com|www\.linkedin\.com|[a-z]{2,3}\.linkedin\.com)\Z",
    re.ASCII,
)
_LINKEDIN_PATH_PATTERN: Final = re.compile(r"/(in|company)/([^/]+)/?\Z", re.ASCII)
_X_HOST_PATTERN: Final = re.compile(
    r"(?:x\.com|www\.x\.com|twitter\.com|www\.twitter\.com|mobile\.twitter\.com)\Z",
    re.ASCII,
)
_X_HANDLE_PATTERN: Final = re.compile(r"/([A-Za-z0-9_]{1,15})/?\Z", re.ASCII)
_ASCII_CONTROL_BOUNDARY: Final = 32
_ASCII_DELETE: Final = 127
_ACCOUNT_KIND_BY_PATH: Final = {
    "in": AccountKind.PERSON,
    "company": AccountKind.COMPANY,
}


@dataclass(frozen=True, slots=True)
class AccountLocator:
    """Canonical public account identity locator."""

    platform: Platform
    kind: AccountKind
    canonical_url: str


LinkedInLocator = AccountLocator


def _invalid_locator() -> AccountInputError:
    return AccountInputError(
        category=AccountInputErrorCategory.INVALID_ACCOUNT_URL,
        field=AccountInputField.ACCOUNTS,
    )


def parse_linkedin_locator(raw: str) -> AccountLocator:
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
        or _LINKEDIN_HOST_PATTERN.fullmatch(hostname.casefold()) is None
        or "\\" in parsed.path
    ):
        raise _invalid_locator()

    path_match = _LINKEDIN_PATH_PATTERN.fullmatch(parsed.path)
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

    return AccountLocator(
        platform=Platform.LINKEDIN,
        kind=_ACCOUNT_KIND_BY_PATH[path_kind],
        canonical_url=f"https://www.linkedin.com/{path_kind}/{segment}/",
    )


def parse_x_locator(raw: str) -> AccountLocator:
    """Parse one untrusted URL into a canonical X profile locator."""
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
        or _X_HOST_PATTERN.fullmatch(hostname.casefold()) is None
        or parsed.query
        or parsed.fragment
        or "\\" in parsed.path
    ):
        raise _invalid_locator()
    path_match = _X_HANDLE_PATTERN.fullmatch(parsed.path)
    if path_match is None:
        raise _invalid_locator()
    handle = path_match.group(1).casefold()
    if handle in X_RESERVED_HANDLES:
        raise _invalid_locator()
    return AccountLocator(
        platform=Platform.X,
        kind=AccountKind.PROFILE,
        canonical_url=f"https://x.com/{handle}/",
    )


def parse_account_locator(raw: str) -> AccountLocator:
    """Dispatch one untrusted public account URL to its platform parser."""
    value = raw.strip()
    try:
        hostname = urlsplit(value).hostname
    except ValueError:
        raise _invalid_locator() from None
    if hostname is None:
        raise _invalid_locator()
    folded = hostname.casefold()
    if _LINKEDIN_HOST_PATTERN.fullmatch(folded) is not None:
        return parse_linkedin_locator(value)
    if _X_HOST_PATTERN.fullmatch(folded) is not None:
        return parse_x_locator(value)
    raise _invalid_locator()
