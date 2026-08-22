"""Deterministic provider-source record for complete successful posts."""

import hashlib
from pathlib import Path
from typing import TYPE_CHECKING, ClassVar, Final, Literal, Self

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator
from pydantic_core import PydanticCustomError

from social_media_subscriber.domain.ids import (
    AccountId,
    CanonicalAccountId,
    ContentHash,
    PlatformPostId,
    post_id_for,
    record_filename,
)
from social_media_subscriber.providers.brightdata.models import (
    JsonValue,
    validate_persistable_post_payload,
)
from social_media_subscriber.serialization.json import canonical_json_value_bytes

if TYPE_CHECKING:
    from social_media_subscriber.providers.brightdata.models import BrightDataPost

BRIGHT_DATA_LINKEDIN_POST_DATASET_ID: Final = "gd_lyy3tktm25m4avu764"
_SOURCE_DIRECTORY: Final = Path("source/brightdata/linkedin/posts")
_HASH_ERROR: Final = "provider_payload_hash"
_HASH_MESSAGE: Final = "payload hash does not match the successful provider payload"


class BrightDataLinkedInPostSourceRecord(BaseModel):
    """Schema-v2 source record whose owner is the canonical Account URL."""

    model_config: ClassVar[ConfigDict] = ConfigDict(
        extra="forbid",
        frozen=True,
        hide_input_in_errors=True,
        strict=True,
        validate_default=True,
    )

    schema_version: Literal[2]
    provider: Literal["brightdata"]
    dataset_id: Literal["gd_lyy3tktm25m4avu764"]
    platform_post_id: PlatformPostId = Field(min_length=1)
    account_id: CanonicalAccountId
    payload_sha256: ContentHash = Field(pattern=r"^[0-9a-f]{64}$")
    payload: dict[str, JsonValue]

    @field_validator("payload", mode="before")
    @classmethod
    def validate_payload_metadata(cls, value: JsonValue) -> dict[str, JsonValue]:
        """Reject secret or request metadata before payload hashing or acceptance."""
        return validate_persistable_post_payload(value)

    @classmethod
    def from_post(cls, account_id: AccountId, post: "BrightDataPost") -> Self:
        """Build a source record from one completely validated successful object."""
        payload = post.payload
        digest = hashlib.sha256(canonical_json_value_bytes(payload)).hexdigest()
        return cls(
            schema_version=2,
            provider="brightdata",
            dataset_id=BRIGHT_DATA_LINKEDIN_POST_DATASET_ID,
            platform_post_id=PlatformPostId(post.id),
            account_id=account_id,
            payload_sha256=ContentHash(digest),
            payload=payload,
        )

    @model_validator(mode="after")
    def validate_payload_hash(self) -> Self:
        """Reject source records whose content digest was altered independently."""
        digest = hashlib.sha256(canonical_json_value_bytes(self.payload)).hexdigest()
        if self.payload_sha256 != digest:
            raise PydanticCustomError(_HASH_ERROR, _HASH_MESSAGE)
        return self


def source_record_path(record: BrightDataLinkedInPostSourceRecord) -> Path:
    """Derive the stable source path from canonical Post identity, never payload."""
    post_id = post_id_for(record.platform_post_id)
    return _SOURCE_DIRECTORY / record_filename(post_id)
