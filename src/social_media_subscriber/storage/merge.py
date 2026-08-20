"""Idempotent merge rules for complete snapshot candidates."""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from typing import TYPE_CHECKING, override

from social_media_subscriber.domain.ids import AccountId, PostId, post_id_for
from social_media_subscriber.storage.snapshot import SnapshotState

if TYPE_CHECKING:
    from social_media_subscriber.domain.account import Account
    from social_media_subscriber.domain.post import Post
    from social_media_subscriber.providers.brightdata.source_record import (
        BrightDataLinkedInPostSourceRecord,
    )


class SnapshotConflictCategory(StrEnum):
    """Closed integrity-conflict categories."""

    ACCOUNT_OWNERSHIP = "account ownership"
    ALIAS = "alias"
    POST = "post"
    POST_OWNERSHIP = "post ownership"
    POST_ACCOUNT = "post account"
    SOURCE = "source"
    SOURCE_OWNERSHIP = "source ownership"
    SOURCE_ACCOUNT = "source account"


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
    merged = {account.id: account for account in previous}
    for candidate in sorted(
        current,
        key=lambda account: (account.id, account.profile_url, account.first_seen_at),
    ):
        existing = merged.get(candidate.id)
        if existing is None:
            merged[candidate.id] = candidate
            continue
        stable_existing = (
            existing.platform,
            existing.kind,
            existing.platform_account_id,
        )
        stable_candidate = (
            candidate.platform,
            candidate.kind,
            candidate.platform_account_id,
        )
        if stable_existing != stable_candidate:
            raise SnapshotConflictError(
                SnapshotConflictCategory.ACCOUNT_OWNERSHIP, candidate.id
            )
        alias_values = {*existing.url_aliases, *candidate.url_aliases}
        if candidate.profile_url != existing.profile_url:
            alias_values.update((existing.profile_url, candidate.profile_url))
        aliases = tuple(sorted(alias_values))
        merged[candidate.id] = existing.model_copy(
            update={
                "url_aliases": aliases,
                "first_seen_at": min(existing.first_seen_at, candidate.first_seen_at),
            }
        )
    owners: dict[str, AccountId] = {}
    for account in merged.values():
        for alias in (account.profile_url, *account.url_aliases):
            owner = owners.setdefault(alias, account.id)
            if owner != account.id:
                raise SnapshotConflictError(SnapshotConflictCategory.ALIAS, alias)
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


def _merge_sources(
    previous: tuple[BrightDataLinkedInPostSourceRecord, ...],
    current: tuple[BrightDataLinkedInPostSourceRecord, ...],
) -> tuple[BrightDataLinkedInPostSourceRecord, ...]:
    merged = {post_id_for(record.platform_post_id): record for record in previous}
    candidates: dict[PostId, BrightDataLinkedInPostSourceRecord] = {}
    for candidate in current:
        candidate_id = post_id_for(candidate.platform_post_id)
        duplicate = candidates.setdefault(candidate_id, candidate)
        if (
            duplicate.payload_sha256 != candidate.payload_sha256
            or duplicate.account_id != candidate.account_id
        ):
            raise SnapshotConflictError(SnapshotConflictCategory.SOURCE, candidate_id)
    for candidate_id, candidate in candidates.items():
        existing = merged.get(candidate_id)
        if existing is not None and existing.account_id != candidate.account_id:
            raise SnapshotConflictError(
                SnapshotConflictCategory.SOURCE_OWNERSHIP, candidate_id
            )
        merged[candidate_id] = candidate
    return tuple(
        sorted(merged.values(), key=lambda record: post_id_for(record.platform_post_id))
    )


def merge_snapshot(
    previous: SnapshotState | None,
    current: SnapshotState,
) -> SnapshotState:
    """Merge a partial successful run without deleting absent prior records."""
    baseline = previous or SnapshotState((), (), ())
    merged_accounts = _merge_accounts(baseline.accounts, current.accounts)
    merged_posts = _merge_posts(baseline.posts, current.posts)
    merged_sources = _merge_sources(baseline.source_records, current.source_records)
    known_accounts = {account.id for account in merged_accounts}
    post_owners = {post.id: post.account_id for post in merged_posts}
    for post in merged_posts:
        if post.account_id not in known_accounts:
            raise SnapshotConflictError(SnapshotConflictCategory.POST_ACCOUNT, post.id)
    for source in merged_sources:
        if source.account_id not in known_accounts:
            raise SnapshotConflictError(
                SnapshotConflictCategory.SOURCE_ACCOUNT,
                post_id_for(source.platform_post_id),
            )
        source_post_id = post_id_for(source.platform_post_id)
        canonical_owner = post_owners.get(source_post_id)
        if canonical_owner is not None and canonical_owner != source.account_id:
            raise SnapshotConflictError(
                SnapshotConflictCategory.SOURCE_OWNERSHIP, source_post_id
            )
    return SnapshotState(merged_accounts, merged_posts, merged_sources)
