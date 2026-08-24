"""Idempotent merge rules for complete snapshot candidates."""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from typing import TYPE_CHECKING, override

from social_media_subscriber.storage.snapshot import SnapshotState

if TYPE_CHECKING:
    from social_media_subscriber.domain.account import Account
    from social_media_subscriber.domain.ids import AccountId, PostId
    from social_media_subscriber.domain.post import Post


class SnapshotConflictCategory(StrEnum):
    """Closed integrity-conflict categories."""

    ACCOUNT = "account"
    POST = "post"
    POST_OWNERSHIP = "post ownership"
    POST_ACCOUNT = "post account"


@dataclass(frozen=True, slots=True)
class SnapshotConflictError(Exception):
    """Candidate records violate identity or deterministic merge ownership."""

    category: SnapshotConflictCategory
    record_id: str

    @override
    def __str__(self) -> str:
        return f"snapshot {self.category} conflict for {self.record_id}"


def _merge_accounts(
    previous: tuple[Account, ...], current: tuple[Account, ...]
) -> tuple[Account, ...]:
    merged: dict[AccountId, Account] = {}
    for candidate in (*previous, *current):
        existing = merged.get(candidate.id)
        if existing is None:
            merged[candidate.id] = candidate
            continue
        stable_existing = (existing.platform, existing.kind, existing.profile_url)
        stable_candidate = (candidate.platform, candidate.kind, candidate.profile_url)
        if stable_existing != stable_candidate:
            raise SnapshotConflictError(SnapshotConflictCategory.ACCOUNT, candidate.id)
        merged[candidate.id] = existing.model_copy(
            update={
                "first_seen_at": min(existing.first_seen_at, candidate.first_seen_at)
            }
        )
    return tuple(sorted(merged.values(), key=lambda account: account.id))


def _merge_posts(
    previous: tuple[Post, ...], current: tuple[Post, ...]
) -> tuple[Post, ...]:
    candidates: dict[PostId, Post] = {}
    for candidate in sorted(current, key=lambda post: (post.id, post.first_seen_at)):
        duplicate = candidates.setdefault(candidate.id, candidate)
        if (
            duplicate.content_hash != candidate.content_hash
            or duplicate.account_id != candidate.account_id
        ):
            raise SnapshotConflictError(SnapshotConflictCategory.POST, candidate.id)
    merged = {post.id: post for post in previous}
    for candidate in candidates.values():
        existing = merged.get(candidate.id)
        if existing is not None and existing.account_id != candidate.account_id:
            raise SnapshotConflictError(
                SnapshotConflictCategory.POST_OWNERSHIP, candidate.id
            )
        first_seen = (
            existing.first_seen_at if existing is not None else candidate.first_seen_at
        )
        merged[candidate.id] = candidate.model_copy(
            update={"first_seen_at": first_seen}
        )
    return tuple(sorted(merged.values(), key=lambda post: post.id))


def merge_snapshot(
    previous: SnapshotState | None,
    current: SnapshotState,
) -> SnapshotState:
    """Merge a partial successful run without deleting absent prior records."""
    baseline = previous or SnapshotState((), ())
    merged_accounts = _merge_accounts(baseline.accounts, current.accounts)
    merged_posts = _merge_posts(baseline.posts, current.posts)
    known_accounts = {account.id for account in merged_accounts}
    for post in merged_posts:
        if post.account_id not in known_accounts:
            raise SnapshotConflictError(SnapshotConflictCategory.POST_ACCOUNT, post.id)
    return SnapshotState(merged_accounts, merged_posts)
