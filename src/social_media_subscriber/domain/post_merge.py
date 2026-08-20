"""Immutable canonical Post merge behavior."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, override

if TYPE_CHECKING:
    from social_media_subscriber.domain.ids import ContentHash, PostId
    from social_media_subscriber.domain.post import Post


@dataclass(frozen=True, slots=True)
class PostMergeConflictError(Exception):
    """A rediscovered Post conflicts with the immutable canonical record."""

    post_id: PostId
    existing_hash: ContentHash
    candidate_hash: ContentHash

    @override
    def __str__(self) -> str:
        """Return an actionable identifier and both conflicting hashes."""
        return (
            f"post {self.post_id} conflicts: "
            f"{self.existing_hash} != {self.candidate_hash}"
        )


def merge_post(existing: Post, candidate: Post) -> Post:
    """Preserve the first canonical record or reject immutable-content drift."""
    if existing.id == candidate.id and existing.content_hash == candidate.content_hash:
        return existing
    raise PostMergeConflictError(
        post_id=existing.id,
        existing_hash=existing.content_hash,
        candidate_hash=candidate.content_hash,
    )
