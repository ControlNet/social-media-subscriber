"""Typed deterministic snapshot state and in-memory summary."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from pathlib import Path

    from social_media_subscriber.domain.account import Account
    from social_media_subscriber.domain.post import Post
    from social_media_subscriber.storage.binary import BinaryFile
    from social_media_subscriber.storage.run_state import RunState


@dataclass(frozen=True, slots=True)
class SnapshotState:
    """All validated records represented by one complete snapshot."""

    accounts: tuple[Account, ...]
    posts: tuple[Post, ...]
    run_state: RunState | None = None
    media: dict[Path, BinaryFile] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class SnapshotSummary:
    """Derived run result returned to callers but never persisted."""

    account_count: int
    post_count: int
    digest: str
