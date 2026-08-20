"""Branded canonical identifiers and safe record filenames."""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass
from typing import Final, NewType, assert_never, override

from social_media_subscriber.domain.platform import AccountKind

AccountId = NewType("AccountId", str)
PostId = NewType("PostId", str)
PlatformAccountId = NewType("PlatformAccountId", str)
PlatformPostId = NewType("PlatformPostId", str)
ContentHash = NewType("ContentHash", str)
ACCOUNT_ID_PATTERN: Final = r"^linkedin:(?:person|company):[0-9]+$"
_NUMERIC_PLATFORM_ID_PATTERN: Final = re.compile(r"[0-9]+", re.ASCII)
_CANONICAL_ACCOUNT_ID_PATTERN: Final = re.compile(
    r"linkedin:(?:person|company):[0-9]+",
    re.ASCII,
)


@dataclass(frozen=True, slots=True)
class InvalidPlatformAccountIdError(ValueError):
    """A LinkedIn Account identity is not a non-empty ASCII numeric ID."""

    value_length: int

    @override
    def __str__(self) -> str:
        """Describe the required grammar without echoing boundary input."""
        return "platform account ID must contain only ASCII digits"


def is_canonical_account_id(value: str) -> bool:
    """Return whether a value follows the canonical LinkedIn Account ID grammar."""
    return _CANONICAL_ACCOUNT_ID_PATTERN.fullmatch(value) is not None


def redact_invalid_account_id(value: str) -> str:
    """Replace malformed Account IDs before boundary error rendering."""
    return value if is_canonical_account_id(value) else "<redacted>"


def account_id_for(
    kind: AccountKind, platform_account_id: PlatformAccountId
) -> AccountId:
    """Build the canonical LinkedIn Account ID for one stable provider identity."""
    if _NUMERIC_PLATFORM_ID_PATTERN.fullmatch(platform_account_id) is None:
        raise InvalidPlatformAccountIdError(value_length=len(platform_account_id))
    match kind:
        case AccountKind.PERSON:
            return AccountId(f"linkedin:person:{platform_account_id}")
        case AccountKind.COMPANY:
            return AccountId(f"linkedin:company:{platform_account_id}")
    assert_never(kind)


def post_id_for(platform_post_id: PlatformPostId) -> PostId:
    """Build the canonical LinkedIn Post ID for one provider post identity."""
    return PostId(f"linkedin:post:{platform_post_id}")


def record_filename(record_id: AccountId | PostId) -> str:
    """Derive a traversal-safe filename without exposing the external identifier."""
    digest = hashlib.sha256(str(record_id).encode()).hexdigest()
    return f"{digest}.json"
