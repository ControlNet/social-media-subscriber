"""Branded canonical identifiers and safe record filenames."""

from __future__ import annotations

import hashlib
from typing import TYPE_CHECKING, Annotated, Final, NewType

from pydantic import BeforeValidator, Field, WithJsonSchema

from social_media_subscriber.accounts.errors import AccountInputError
from social_media_subscriber.accounts.locator import parse_account_locator
from social_media_subscriber.platforms.x import X_CANONICAL_HANDLE_BODY

if TYPE_CHECKING:
    from social_media_subscriber.domain.platform import Platform
    from social_media_subscriber.serialization.json import JsonValue

AccountId = NewType("AccountId", str)
PostId = NewType("PostId", str)
PlatformPostId = NewType("PlatformPostId", str)
ContentHash = NewType("ContentHash", str)
_LINKEDIN_ACCOUNT_SEGMENT_PATTERN: Final = (
    r"(?!\.{1,2}/)(?![^/]*(?:\\|%))[^/?#\x00-\x1f\x7f]+"
)
_LINKEDIN_PERSON_ACCOUNT_ID_BODY: Final = (
    rf"https://www\.linkedin\.com/in/{_LINKEDIN_ACCOUNT_SEGMENT_PATTERN}/"
)
_LINKEDIN_COMPANY_ACCOUNT_ID_BODY: Final = (
    rf"https://www\.linkedin\.com/company/{_LINKEDIN_ACCOUNT_SEGMENT_PATTERN}/"
)
_X_ACCOUNT_ID_BODY: Final = rf"https://x\.com/{X_CANONICAL_HANDLE_BODY}/"
LINKEDIN_PERSON_ACCOUNT_ID_PATTERN: Final = rf"^{_LINKEDIN_PERSON_ACCOUNT_ID_BODY}$"
LINKEDIN_COMPANY_ACCOUNT_ID_PATTERN: Final = rf"^{_LINKEDIN_COMPANY_ACCOUNT_ID_BODY}$"
LINKEDIN_ACCOUNT_ID_PATTERN: Final = (
    rf"^(?:{_LINKEDIN_PERSON_ACCOUNT_ID_BODY}|{_LINKEDIN_COMPANY_ACCOUNT_ID_BODY})$"
)
X_ACCOUNT_ID_PATTERN: Final = rf"^{_X_ACCOUNT_ID_BODY}$"
ACCOUNT_ID_PATTERN: Final = (
    rf"^(?:{_LINKEDIN_PERSON_ACCOUNT_ID_BODY}|{_LINKEDIN_COMPANY_ACCOUNT_ID_BODY}|"
    rf"{_X_ACCOUNT_ID_BODY})$"
)
_RUNTIME_ACCOUNT_ID_PATTERN: Final = (
    r"^(?:https://www\.linkedin\.com/(?:in|company)/[^/?#]+/|"
    r"https://x\.com/[a-z0-9_]{1,15}/)$"
)


def is_canonical_account_id(value: str) -> bool:
    """Return whether a value is an exact canonical supported Account URL."""
    try:
        locator = parse_account_locator(value)
    except AccountInputError:
        return False
    return locator.canonical_url == value


def redact_invalid_account_id(value: JsonValue) -> str:
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


def post_id_for(platform: Platform, platform_post_id: PlatformPostId) -> PostId:
    """Build one platform-qualified canonical Post identity."""
    return PostId(f"{platform.value}:post:{platform_post_id}")


def record_filename(record_id: AccountId | PostId) -> str:
    """Derive a traversal-safe filename without exposing the external identifier."""
    digest = hashlib.sha256(str(record_id).encode()).hexdigest()
    return f"{digest}.json"
