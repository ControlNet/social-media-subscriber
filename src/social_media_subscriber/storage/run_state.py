"""Shared snapshot progress and media retry state for both publishers."""

from datetime import datetime
from typing import ClassVar

from pydantic import BaseModel, ConfigDict, Field, field_validator

from social_media_subscriber.domain.time import canonical_utc


class MediaFailure(BaseModel):
    """One stable media slot waiting for automatic or manual recovery."""

    model_config: ClassVar[ConfigDict] = ConfigDict(extra="forbid", frozen=True)
    post_id: str
    scope: str
    index: int = Field(ge=0)
    source_url: str
    failed_runs: int = Field(ge=1)
    error: str


class RunState(BaseModel):
    """Published collection watermarks and retry queues."""

    model_config: ClassVar[ConfigDict] = ConfigDict(extra="forbid", frozen=True)
    accounts: dict[str, datetime] = Field(default_factory=dict)
    pending_media: tuple[MediaFailure, ...] = ()
    failed_media: tuple[MediaFailure, ...] = ()

    @field_validator("accounts")
    @classmethod
    def validate_timestamps(cls, values: dict[str, datetime]) -> dict[str, datetime]:
        """Require timezone-aware collection boundaries."""
        return {key: canonical_utc(value) for key, value in values.items()}
