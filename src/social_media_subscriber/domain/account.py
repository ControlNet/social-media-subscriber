"""Frozen canonical Account boundary contract."""

import re
from datetime import datetime
from typing import Annotated, ClassVar, Final, Literal, Self
from urllib.parse import urlsplit

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    StringConstraints,
    field_validator,
    model_validator,
)
from pydantic_core import PydanticCustomError

from social_media_subscriber.domain.ids import (
    AccountId,
    PlatformAccountId,
    account_id_for,
)
from social_media_subscriber.domain.platform import AccountKind, Platform
from social_media_subscriber.domain.time import canonical_utc

_PROFILE_PATH_PATTERN = re.compile(r"/(?:in|company)/[^/]+/\Z", re.ASCII)
_CanonicalProfileUrl = Annotated[
    str,
    StringConstraints(pattern=r"^https://www\.linkedin\.com/(?:in|company)/[^/]+/$"),
]
_PROFILE_URL_ERROR_CODE: Final = "canonical_profile_url"
_PROFILE_URL_ERROR_MESSAGE: Final = (
    "value must be a canonical public LinkedIn profile URL"
)
_ACCOUNT_ID_ERROR_CODE: Final = "account_id_mismatch"
_ACCOUNT_ID_ERROR_MESSAGE: Final = (
    "account id does not match kind and platform account id"
)
_ACCOUNT_KIND_URL_ERROR_CODE: Final = "account_kind_url_mismatch"
_ACCOUNT_KIND_URL_ERROR_MESSAGE: Final = (
    "profile URL and aliases do not match account kind"
)


def _canonical_profile_url(value: str) -> str:
    try:
        parsed = urlsplit(value)
        port = parsed.port
    except ValueError:
        port = -1
        parsed = urlsplit("")
    if (
        parsed.scheme != "https"
        or parsed.netloc != "www.linkedin.com"
        or parsed.hostname != "www.linkedin.com"
        or parsed.username is not None
        or parsed.password is not None
        or port is not None
        or parsed.query
        or parsed.fragment
        or _PROFILE_PATH_PATTERN.fullmatch(parsed.path) is None
    ):
        raise PydanticCustomError(
            _PROFILE_URL_ERROR_CODE,
            _PROFILE_URL_ERROR_MESSAGE,
        )
    return value


class Account(BaseModel):
    """Versioned provider-neutral Account record persisted at the boundary."""

    model_config: ClassVar[ConfigDict] = ConfigDict(
        extra="forbid",
        frozen=True,
        validate_default=True,
    )

    schema_version: Literal[1] = 1
    id: AccountId = Field(pattern=r"^linkedin:(?:person|company):.+$")
    platform: Literal[Platform.LINKEDIN]
    kind: AccountKind
    platform_account_id: PlatformAccountId = Field(min_length=1)
    profile_url: _CanonicalProfileUrl
    url_aliases: tuple[_CanonicalProfileUrl, ...] = Field(
        json_schema_extra={"uniqueItems": True}
    )
    first_seen_at: datetime

    @field_validator("profile_url")
    @classmethod
    def validate_profile_url(cls, value: str) -> str:
        """Reject non-canonical or unsafe profile URLs."""
        return _canonical_profile_url(value)

    @field_validator("url_aliases")
    @classmethod
    def normalize_url_aliases(cls, values: tuple[str, ...]) -> tuple[str, ...]:
        """Validate, deduplicate, and sort all known canonical aliases."""
        return tuple(sorted({_canonical_profile_url(value) for value in values}))

    @field_validator("first_seen_at")
    @classmethod
    def validate_first_seen_at(cls, value: datetime) -> datetime:
        """Require canonical UTC discovery time."""
        return canonical_utc(value)

    @model_validator(mode="after")
    def validate_identity(self) -> Self:
        """Require the branded ID to match the stable platform identity."""
        expected = account_id_for(self.kind, self.platform_account_id)
        if self.id != expected:
            raise PydanticCustomError(
                _ACCOUNT_ID_ERROR_CODE,
                _ACCOUNT_ID_ERROR_MESSAGE,
            )
        match self.kind:
            case AccountKind.PERSON:
                expected_path = "/in/"
            case AccountKind.COMPANY:
                expected_path = "/company/"
        all_urls = (self.profile_url, *self.url_aliases)
        if not all(
            urlsplit(value).path.startswith(expected_path) for value in all_urls
        ):
            raise PydanticCustomError(
                _ACCOUNT_KIND_URL_ERROR_CODE,
                _ACCOUNT_KIND_URL_ERROR_MESSAGE,
            )
        return self
