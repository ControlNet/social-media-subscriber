"""Frozen models for validated Apify Actor responses."""

from __future__ import annotations

from datetime import datetime
from typing import ClassVar, Self

from pydantic import BaseModel, ConfigDict, Field, TypeAdapter, model_validator
from pydantic_core import PydanticCustomError

from social_media_subscriber.domain.time import canonical_utc
from social_media_subscriber.providers.payload_security import contains_forbidden_field
from social_media_subscriber.serialization.json import JsonValue

_JSON_OBJECT = TypeAdapter(
    dict[str, JsonValue],
    config=ConfigDict(strict=True),
)
_FORBIDDEN_FIELD_ERROR = "provider_post_forbidden_field"
_FORBIDDEN_FIELD_MESSAGE = "provider post contains non-persistable metadata"
_POST_TIMESTAMP_ERROR = "provider_post_timestamp"
_POST_TIMESTAMP_MESSAGE = "provider post timestamp must be timezone-aware"


class _ApifyModel(BaseModel):
    model_config: ClassVar[ConfigDict] = ConfigDict(
        extra="allow",
        frozen=True,
        hide_input_in_errors=True,
        strict=True,
        validate_default=True,
        populate_by_name=True,
    )

    @model_validator(mode="before")
    @classmethod
    def validate_recursive_json(cls, value: JsonValue) -> dict[str, JsonValue]:
        return _JSON_OBJECT.validate_python(value)


class ApifyAuthor(_ApifyModel):
    """Minimum actor identity required for ownership validation."""

    linkedin_url: str = Field(min_length=1, alias="linkedinUrl")


class ApifyQuery(BaseModel):
    """Actor request identity retained in memory but never persisted."""

    model_config: ClassVar[ConfigDict] = ConfigDict(
        extra="ignore",
        frozen=True,
        hide_input_in_errors=True,
        strict=True,
        populate_by_name=True,
    )

    target_url: str = Field(min_length=1, alias="targetUrl")


class ApifyPostedAt(_ApifyModel):
    """Provider timestamp container."""

    date: str = Field(min_length=1)

    @property
    def timestamp(self) -> datetime:
        """Return the canonical UTC provider timestamp."""
        try:
            return canonical_utc(datetime.fromisoformat(self.date))
        except (ValueError, PydanticCustomError):
            raise PydanticCustomError(
                _POST_TIMESTAMP_ERROR, _POST_TIMESTAMP_MESSAGE
            ) from None


class ApifyPost(_ApifyModel):
    """Open-ended actor post with strict canonical prerequisites."""

    id: str = Field(min_length=1)
    linkedin_url: str = Field(min_length=1, alias="linkedinUrl")
    type: str = Field(min_length=1)
    content: str | None = None
    author: ApifyAuthor
    query: ApifyQuery | None = Field(default=None, exclude=True)
    posted_at: ApifyPostedAt = Field(alias="postedAt")

    @model_validator(mode="before")
    @classmethod
    def reject_non_persistable_metadata(cls, value: JsonValue) -> dict[str, JsonValue]:
        """Reject transport and credential material recursively."""
        payload = _JSON_OBJECT.validate_python(value)
        persistable = {key: item for key, item in payload.items() if key != "query"}
        if contains_forbidden_field(persistable):
            raise PydanticCustomError(_FORBIDDEN_FIELD_ERROR, _FORBIDDEN_FIELD_MESSAGE)
        if "query" in payload:
            persistable["query"] = payload["query"]
        return persistable

    @model_validator(mode="after")
    def validate_timestamp(self) -> Self:
        """Require a timezone-aware timestamp at the typed boundary."""
        _ = self.posted_at.timestamp
        return self

    @property
    def payload(self) -> dict[str, JsonValue]:
        """Return the complete successful object with provider field names."""
        return _JSON_OBJECT.validate_json(
            self.model_dump_json(exclude_unset=True, by_alias=True)
        )


class ApifyRun(_ApifyModel):
    """Actor run state required for polling and dataset ownership."""

    id: str = Field(min_length=1)
    status: str = Field(min_length=1)
    default_dataset_id: str | None = Field(default=None, alias="defaultDatasetId")
    default_key_value_store_id: str | None = Field(
        default=None, alias="defaultKeyValueStoreId"
    )


class ApifyRunEnvelope(_ApifyModel):
    """REST envelope around one Actor run."""

    data: ApifyRun
