"""Derived deterministic index for persisted Platform Post records."""

from __future__ import annotations

from datetime import datetime  # noqa: TC003 - Pydantic resolves this at runtime.
from typing import ClassVar, Final

from pydantic import BaseModel, ConfigDict, Field, field_validator

from social_media_subscriber.domain.ids import (  # noqa: TC001
    CanonicalAccountId,
)
from social_media_subscriber.domain.platform import Platform  # noqa: TC001
from social_media_subscriber.domain.time import canonical_utc

LINKEDIN_POST_INDEX_PATH_PATTERN: Final = r"^posts/linkedin/[a-f0-9]{64}\.json$"
X_POST_INDEX_PATH_PATTERN: Final = r"^posts/x/[a-f0-9]{64}\.json$"
POST_INDEX_PATH_PATTERN: Final = r"^posts/(?:linkedin|x)/[a-f0-9]{64}\.json$"


class PostIndexEntry(BaseModel):
    """Public locator and routing metadata for one persisted Post file."""

    model_config: ClassVar[ConfigDict] = ConfigDict(
        extra="forbid",
        frozen=True,
        hide_input_in_errors=True,
        strict=True,
        validate_default=True,
    )

    path: str = Field(pattern=POST_INDEX_PATH_PATTERN)
    account_profile_url: CanonicalAccountId
    published_at: datetime
    platform: Platform

    @field_validator("published_at")
    @classmethod
    def validate_published_at(cls, value: datetime) -> datetime:
        """Require the same canonical UTC timestamp as the referenced Post."""
        return canonical_utc(value)


class PostsIndex(BaseModel):
    """Newest-first complete index of every persisted Post record."""

    model_config: ClassVar[ConfigDict] = ConfigDict(
        extra="forbid",
        frozen=True,
        hide_input_in_errors=True,
        strict=True,
        validate_default=True,
    )

    posts: tuple[PostIndexEntry, ...]
