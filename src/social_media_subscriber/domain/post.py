"""Canonical platform Post boundary and deterministic merge identity."""

import hashlib
import json
import re
from datetime import datetime
from typing import ClassVar, Final
from urllib.parse import urlsplit

from pydantic import BaseModel, ConfigDict, Field, field_validator
from pydantic_core import PydanticCustomError

from social_media_subscriber.domain.ids import (
    AccountId,
    CanonicalAccountId,
    ContentHash,
    PlatformPostId,
    PostId,
    post_id_for,
)
from social_media_subscriber.domain.time import canonical_utc
from social_media_subscriber.serialization.json import JsonValue

_UNSAFE_URL_PATTERN = re.compile(
    r"(?:[\x00-\x1f\x7f\\]|%(?:[01][0-9a-f]|7f|2f|5c|2e))",
    re.IGNORECASE,
)
_POST_URL_ERROR_CODE: Final = "canonical_post_url"
_POST_URL_ERROR_MESSAGE: Final = "value must be a canonical public LinkedIn post URL"
_POST_TYPE_ERROR_CODE: Final = "post_type"
_POST_TYPE_ERROR_MESSAGE: Final = "post type must not be empty"


def _canonical_post_url(value: str) -> str:
    try:
        parsed = urlsplit(value)
        port = parsed.port
    except ValueError:
        port = -1
        parsed = urlsplit("")
    if (
        _UNSAFE_URL_PATTERN.search(value) is not None
        or parsed.scheme != "https"
        or parsed.netloc != "www.linkedin.com"
        or parsed.hostname != "www.linkedin.com"
        or parsed.username is not None
        or parsed.password is not None
        or port is not None
        or parsed.query
        or parsed.fragment
        or any(segment in {".", ".."} for segment in parsed.path.split("/"))
        or not parsed.path.startswith(("/posts/", "/feed/update/"))
    ):
        raise PydanticCustomError(_POST_URL_ERROR_CODE, _POST_URL_ERROR_MESSAGE)
    return value


class Post(BaseModel):
    """Provider-neutral platform Post with open-ended content."""

    model_config: ClassVar[ConfigDict] = ConfigDict(
        extra="forbid",
        frozen=True,
        hide_input_in_errors=True,
        strict=True,
        validate_default=True,
    )

    platform_post_id: PlatformPostId = Field(min_length=1)
    account_profile_url: CanonicalAccountId
    canonical_url: str
    published_at: datetime
    type: str = Field(min_length=1)
    content: dict[str, JsonValue]
    first_seen_at: datetime

    @property
    def id(self) -> PostId:
        """Return the namespaced Post identity without persisting it."""
        return post_id_for(self.platform_post_id)

    @property
    def account_id(self) -> AccountId:
        """Return the Account URL identity used by internal ownership checks."""
        return AccountId(self.account_profile_url)

    @property
    def content_hash(self) -> ContentHash:
        """Return the stable merge hash without persisting a duplicate field."""
        fields = {
            "account_profile_url": self.account_profile_url,
            "canonical_url": self.canonical_url,
            "text": self.content.get("text"),
            "platform_post_id": self.platform_post_id,
            "published_at": self.published_at.isoformat().replace("+00:00", "Z"),
            "type": self.type,
        }
        encoded = json.dumps(
            fields, ensure_ascii=False, separators=(",", ":"), sort_keys=True
        ).encode()
        return ContentHash(hashlib.sha256(encoded).hexdigest())

    @field_validator("canonical_url")
    @classmethod
    def validate_canonical_url(cls, value: str) -> str:
        """Reject unsafe or non-canonical public Post URLs."""
        return _canonical_post_url(value)

    @field_validator("published_at", "first_seen_at")
    @classmethod
    def validate_utc_timestamp(cls, value: datetime) -> datetime:
        """Require canonical UTC publication and discovery timestamps."""
        return canonical_utc(value)

    @field_validator("type")
    @classmethod
    def normalize_type(cls, value: str) -> str:
        """Normalize the platform post type without closing future variants."""
        normalized = value.strip()
        if not normalized:
            raise PydanticCustomError(_POST_TYPE_ERROR_CODE, _POST_TYPE_ERROR_MESSAGE)
        return normalized
