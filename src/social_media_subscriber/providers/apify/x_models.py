"""Frozen models for validated Xquik responses."""

from __future__ import annotations

from datetime import datetime
from typing import ClassVar, Final, Literal, Self

from pydantic import BaseModel, ConfigDict, Field, TypeAdapter, model_validator
from pydantic_core import PydanticCustomError

from social_media_subscriber.domain.time import canonical_utc
from social_media_subscriber.providers.payload_security import contains_forbidden_field
from social_media_subscriber.serialization.json import JsonValue

_JSON_OBJECT = TypeAdapter(dict[str, JsonValue], config=ConfigDict(strict=True))
_FORBIDDEN_FIELD_ERROR = "provider_post_forbidden_field"
_FORBIDDEN_FIELD_MESSAGE = "provider post contains non-persistable metadata"
_POST_TIMESTAMP_ERROR = "provider_post_timestamp"
_POST_TIMESTAMP_MESSAGE = "provider post timestamp must be a supported UTC value"
_DIAGNOSTIC_ROW_ERROR = "provider_diagnostic_tweet_fields"
_DIAGNOSTIC_ROW_MESSAGE = "provider diagnostic must not contain tweet fields"
_DIAGNOSTIC_TWEET_FIELDS: Final = frozenset(
    {"author", "createdAt", "text", "type", "url"}
)


class _XquikModel(BaseModel):
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


class ApifyXAuthor(_XquikModel):
    """Minimum Xquik author identity required for ownership validation."""

    id: str = Field(min_length=1)
    username: str = Field(min_length=1)


class ApifyXPost(_XquikModel):
    """Open-ended real Xquik tweet with strict canonical prerequisites."""

    id: str = Field(min_length=1)
    text: str
    type: Literal["tweet", "reply"]
    url: str = Field(min_length=1)
    created_at: str = Field(min_length=1, alias="createdAt")
    author: ApifyXAuthor
    is_reply: bool = Field(alias="isReply")
    is_quote_status: bool = Field(alias="isQuoteStatus")
    bookmark_count: int = Field(ge=0, alias="bookmarkCount")
    like_count: int = Field(ge=0, alias="likeCount")
    quote_count: int = Field(ge=0, alias="quoteCount")
    reply_count: int = Field(ge=0, alias="replyCount")
    retweet_count: int = Field(ge=0, alias="retweetCount")
    view_count: int = Field(ge=0, alias="viewCount")

    @model_validator(mode="before")
    @classmethod
    def reject_non_persistable_metadata(cls, value: JsonValue) -> dict[str, JsonValue]:
        """Reject credential and transport material recursively."""
        payload = _JSON_OBJECT.validate_python(value)
        if contains_forbidden_field(payload):
            raise PydanticCustomError(_FORBIDDEN_FIELD_ERROR, _FORBIDDEN_FIELD_MESSAGE)
        return payload

    @model_validator(mode="after")
    def validate_timestamp(self) -> Self:
        """Require the observed Xquik UTC timestamp contract."""
        _ = self.timestamp
        return self

    @property
    def timestamp(self) -> datetime:
        """Return the canonical UTC Xquik publication timestamp."""
        try:
            return canonical_utc(
                datetime.strptime(self.created_at, "%a %b %d %H:%M:%S %z %Y")
            )
        except (ValueError, PydanticCustomError):
            raise PydanticCustomError(
                _POST_TIMESTAMP_ERROR, _POST_TIMESTAMP_MESSAGE
            ) from None

    @property
    def payload(self) -> dict[str, JsonValue]:
        """Return the complete safe tweet object with provider field names."""
        return _JSON_OBJECT.validate_json(
            self.model_dump_json(exclude_unset=True, by_alias=True)
        )


class ApifyXDiagnostic(_XquikModel):
    """Explicit non-tweet Xquik zero-output marker."""

    id: str = Field(min_length=1)
    result_type: Literal["diagnostic"] = Field(alias="resultType")
    status: Literal["zero-output"]

    @model_validator(mode="before")
    @classmethod
    def reject_tweet_fields(cls, value: JsonValue) -> dict[str, JsonValue]:
        """Require an explicit non-tweet diagnostic shape."""
        payload = _JSON_OBJECT.validate_python(value)
        if _DIAGNOSTIC_TWEET_FIELDS & payload.keys():
            raise PydanticCustomError(_DIAGNOSTIC_ROW_ERROR, _DIAGNOSTIC_ROW_MESSAGE)
        return payload


class ApifyXReportResults(_XquikModel):
    """Xquik row accounting and completion evidence."""

    completion_reason: str = Field(min_length=1, alias="completionReason")
    diagnostic_rows: int = Field(ge=0, alias="diagnosticRows")
    estimated_charge_usd: float = Field(ge=0, alias="estimatedChargeUsd")
    failed_subtargets: int = Field(ge=0, alias="failedSubtargets")
    real_rows: int = Field(ge=0, alias="realRows")
    total_duplicates: int = Field(ge=0, alias="totalDuplicates")
    total_pushed: int = Field(ge=0, alias="totalPushed")


class ApifyXRunReport(_XquikModel):
    """Xquik completion report stored under the Actor run-report record."""

    outcome: str = Field(min_length=1)
    results: ApifyXReportResults
    anomaly_counts: dict[str, int] = Field(alias="anomalyCounts")
