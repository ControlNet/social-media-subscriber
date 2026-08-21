"""Closed operation vocabulary for automatic adapter drivers."""

from __future__ import annotations

from enum import StrEnum, unique


@unique
class AdapterOperation(StrEnum):
    """Operations currently supported by automatic adapter drivers."""

    RESOLVE_ACCOUNT_IDENTITY = "resolve_account_identity"
    COLLECT_ACCOUNT_POSTS = "collect_account_posts"
    DISCOVER_LOCATOR_POSTS = "discover_locator_posts"
