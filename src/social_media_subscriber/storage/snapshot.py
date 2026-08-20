"""Typed deterministic snapshot state and generated indexes."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, ClassVar, Literal

from pydantic import BaseModel, ConfigDict, Field

if TYPE_CHECKING:
    from social_media_subscriber.domain.account import Account
    from social_media_subscriber.domain.post import Post
    from social_media_subscriber.providers.brightdata.source_record import (
        BrightDataLinkedInPostSourceRecord,
    )


class _SnapshotModel(BaseModel):
    model_config: ClassVar[ConfigDict] = ConfigDict(
        extra="forbid", frozen=True, validate_default=True
    )


class AccountsIndex(_SnapshotModel):
    """Canonical Account ID to record-path index."""

    schema_version: Literal[1] = 1
    accounts: dict[str, str]


class FeedIndex(_SnapshotModel):
    """Canonical Post IDs in deterministic feed order."""

    schema_version: Literal[1] = 1
    posts: tuple[str, ...]


class SnapshotManifest(_SnapshotModel):
    """Versioned counts and digest for all non-manifest files."""

    schema_version: Literal[1] = 1
    account_count: int = Field(ge=0)
    post_count: int = Field(ge=0)
    source_record_count: int = Field(ge=0)
    digest: str = Field(pattern=r"^[0-9a-f]{64}$")


@dataclass(frozen=True, slots=True)
class SnapshotState:
    """All validated records represented by one complete snapshot."""

    accounts: tuple[Account, ...]
    posts: tuple[Post, ...]
    source_records: tuple[BrightDataLinkedInPostSourceRecord, ...]
