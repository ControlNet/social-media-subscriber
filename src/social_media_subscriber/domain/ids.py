"""Branded canonical identifiers and safe record filenames."""

from __future__ import annotations

import hashlib
from typing import Annotated, Final, NewType

from pydantic import BeforeValidator, Field, WithJsonSchema

from social_media_subscriber.accounts.errors import AccountInputError
from social_media_subscriber.accounts.locator import parse_linkedin_locator

AccountId = NewType("AccountId", str)
PostId = NewType("PostId", str)
PlatformPostId = NewType("PlatformPostId", str)
ContentHash = NewType("ContentHash", str)
ACCOUNT_ID_PATTERN: Final = (
    r"^https://www\.linkedin\.com/(?:in|company)/"
    r"(?!\.{1,2}/)(?![^/]*(?:\\|%(?:[01][0-9A-Fa-f]|7[fF]|2[fF]|5[cC]|2[eE])))"
    r"[^/\x00-\x1f\x7f]+/$"
)
_RUNTIME_ACCOUNT_ID_PATTERN: Final = (
    r"^https://www\.linkedin\.com/(?:in|company)/[^/]+/$"
)


def is_canonical_account_id(value: str) -> bool:
    """Return whether a value is an exact canonical LinkedIn Account URL."""
    try:
        locator = parse_linkedin_locator(value)
    except AccountInputError:
        return False
    return locator.canonical_url == value


def redact_invalid_account_id(value: object) -> object:
    """Replace malformed Account IDs before boundary error rendering."""
    return (
        value
        if isinstance(value, str) and is_canonical_account_id(value)
        else "<redacted>"
    )


CanonicalAccountId = Annotated[
    AccountId,
    BeforeValidator(redact_invalid_account_id),
    Field(pattern=_RUNTIME_ACCOUNT_ID_PATTERN),
    WithJsonSchema({"type": "string", "pattern": ACCOUNT_ID_PATTERN}),
]


def post_id_for(platform_post_id: PlatformPostId) -> PostId:
    """Build the canonical LinkedIn Post ID for one provider post identity."""
    return PostId(f"linkedin:post:{platform_post_id}")


def record_filename(record_id: AccountId | PostId) -> str:
    """Derive a traversal-safe filename without exposing the external identifier."""
    digest = hashlib.sha256(str(record_id).encode()).hexdigest()
    return f"{digest}.json"
