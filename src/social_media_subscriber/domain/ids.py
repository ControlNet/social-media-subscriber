"""Branded canonical identifiers and safe record filenames."""

from __future__ import annotations

import hashlib
from typing import NewType

from social_media_subscriber.domain.platform import AccountKind

AccountId = NewType("AccountId", str)
PostId = NewType("PostId", str)
PlatformAccountId = NewType("PlatformAccountId", str)
PlatformPostId = NewType("PlatformPostId", str)
ContentHash = NewType("ContentHash", str)


def account_id_for(
    kind: AccountKind, platform_account_id: PlatformAccountId
) -> AccountId:
    """Build the canonical LinkedIn Account ID for one stable provider identity."""
    match kind:
        case AccountKind.PERSON:
            prefix = "linkedin:person:"
        case AccountKind.COMPANY:
            prefix = "linkedin:company:"
    return AccountId(f"{prefix}{platform_account_id}")


def post_id_for(platform_post_id: PlatformPostId) -> PostId:
    """Build the canonical LinkedIn Post ID for one provider post identity."""
    return PostId(f"linkedin:post:{platform_post_id}")


def record_filename(record_id: AccountId | PostId) -> str:
    """Derive a traversal-safe filename without exposing the external identifier."""
    digest = hashlib.sha256(str(record_id).encode()).hexdigest()
    return f"{digest}.json"
