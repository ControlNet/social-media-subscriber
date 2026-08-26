"""Canonical platform Post boundary and deterministic merge identity."""

import hashlib
import json
from datetime import datetime
from typing import ClassVar, Final, Self

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator
from pydantic_core import PydanticCustomError

from social_media_subscriber.accounts.errors import AccountInputError
from social_media_subscriber.accounts.locator import parse_account_locator
from social_media_subscriber.domain.ids import (
    AccountId,
    CanonicalAccountId,
    ContentHash,
    PlatformPostId,
    PostId,
    post_id_for,
)
from social_media_subscriber.domain.platform import Platform
from social_media_subscriber.domain.time import canonical_utc
from social_media_subscriber.platforms.linkedin import (
    LinkedInPostUrlError,
    canonical_post_timestamp,
)
from social_media_subscriber.platforms.linkedin import (
    canonical_post_url as canonical_linkedin_post_url,
)
from social_media_subscriber.platforms.x import (
    XPostUrlError,
)
from social_media_subscriber.platforms.x import (
    canonical_post_url as canonical_x_post_url,
)
from social_media_subscriber.serialization.json import JsonValue

_POST_URL_ERROR_CODE: Final = "canonical_post_url"
_POST_URL_ERROR_MESSAGE: Final = "value must be a canonical public platform post URL"
_POST_TYPE_ERROR_CODE: Final = "post_type"
_POST_TYPE_ERROR_MESSAGE: Final = "post type must not be empty"


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
        return post_id_for(self.platform, self.platform_post_id)

    @property
    def platform(self) -> Platform:
        """Derive the platform from the canonical owning Account URL."""
        return parse_account_locator(self.account_profile_url).platform

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

    @model_validator(mode="after")
    def validate_canonical_url(self) -> Self:
        """Require the Post URL to be canonical for its owning platform."""
        try:
            platform = self.platform
            match platform:  # noqa: MATCH_OK - exhaustive enum; Never case is rejected.
                case Platform.LINKEDIN:
                    canonical = canonical_linkedin_post_url(self.canonical_url)
                case Platform.X:
                    canonical = canonical_x_post_url(
                        self.canonical_url,
                        platform_post_id=self.platform_post_id,
                    )
        except (AccountInputError, LinkedInPostUrlError, XPostUrlError):
            raise PydanticCustomError(
                _POST_URL_ERROR_CODE,
                _POST_URL_ERROR_MESSAGE,
            ) from None
        if canonical != self.canonical_url:
            raise PydanticCustomError(
                _POST_URL_ERROR_CODE,
                _POST_URL_ERROR_MESSAGE,
            )
        return self

    @field_validator("published_at")
    @classmethod
    def normalize_publication_timestamp(cls, value: datetime) -> datetime:
        """Use whole-second precision independent of provider output."""
        return canonical_post_timestamp(value)

    @field_validator("first_seen_at")
    @classmethod
    def validate_first_seen_timestamp(cls, value: datetime) -> datetime:
        """Require a canonical UTC discovery timestamp."""
        return canonical_utc(value)

    @field_validator("type")
    @classmethod
    def normalize_type(cls, value: str) -> str:
        """Normalize the platform post type without closing future variants."""
        normalized = value.strip()
        if not normalized:
            raise PydanticCustomError(_POST_TYPE_ERROR_CODE, _POST_TYPE_ERROR_MESSAGE)
        return normalized
