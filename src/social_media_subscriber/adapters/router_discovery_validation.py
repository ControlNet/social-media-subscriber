"""Atomic validation helpers for locator Posts discovery outcomes."""

from __future__ import annotations

from typing import TYPE_CHECKING

from social_media_subscriber.domain.post_merge import PostMergeConflictError, merge_post

if TYPE_CHECKING:
    from social_media_subscriber.adapters.instance import (
        AdapterPostLocatorBatch,
        AdapterPostLocatorOutcome,
        ResolvedLocatorPosts,
    )
    from social_media_subscriber.domain.ids import AccountId, PostId
    from social_media_subscriber.domain.post import Post


def covers_batch(
    batch: AdapterPostLocatorBatch,
    outcomes: tuple[AdapterPostLocatorOutcome, ...],
) -> bool:
    """Return whether outcomes cover each requested locator exactly once."""
    expected = {request.locator.canonical_url for request in batch.requests}
    received = {outcome.locator.canonical_url for outcome in outcomes}
    return received == expected and len(received) == len(outcomes)


def resolution_mismatch(
    outcome: ResolvedLocatorPosts,
    owner: str | None,
    locator_url: str,
) -> bool:
    """Return whether a resolved identity violates locator ownership."""
    return (
        outcome.collected.account_id != outcome.account.id
        or outcome.account.kind is not outcome.locator.kind
        or (owner is not None and owner != locator_url)
    )


def merge_posts(
    destination: dict[PostId, Post],
    incoming: tuple[Post, ...],
    account_id: AccountId,
) -> bool:
    """Merge identity-consistent Posts atomically into a pending mapping."""
    try:
        for post in incoming:
            if post.account_id != account_id:
                return False
            existing = destination.get(post.id)
            destination[post.id] = (
                post if existing is None else merge_post(existing, post)
            )
    except PostMergeConflictError:
        return False
    return True
