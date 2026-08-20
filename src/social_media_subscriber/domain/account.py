"""Frozen canonical Account boundary contract."""

import re
from datetime import datetime
from typing import Annotated, ClassVar, Final, Literal, Self, TypedDict, assert_never
from urllib.parse import urlsplit

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    StringConstraints,
    WithJsonSchema,
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
_UNSAFE_PROFILE_URL_PATTERN = re.compile(
    r"(?:[\x00-\x1f\x7f\\]|%(?:[01][0-9a-f]|7f|2f|5c|2e))",
    re.IGNORECASE,
)
_PROFILE_URL_SCHEMA_PATTERN: Final = (
    r"^https://www\.linkedin\.com/(?:in|company)/"
    r"(?!\.{1,2}/)(?![^/]*(?:\\|%(?:[01][0-9A-Fa-f]|7[fF]|2[fF]|5[cC]|2[eE])))"
    r"[^/\x00-\x1f\x7f]+/$"
)
_CanonicalProfileUrl = Annotated[
    str,
    StringConstraints(pattern=r"^https://www\.linkedin\.com/(?:in|company)/[^/]+/$"),
    WithJsonSchema({"type": "string", "pattern": _PROFILE_URL_SCHEMA_PATTERN}),
]
_CanonicalPlatformAccountId = Annotated[
    PlatformAccountId,
    StringConstraints(pattern=r"^[0-9]+$"),
]
_CANONICAL_ACCOUNT_ID_PATTERN = re.compile(
    r"linkedin:(?:person|company):[0-9]+",
    re.ASCII,
)
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


class _AccountBoundaryInput(TypedDict, total=False):
    id: str | int | float | bool | None
    platform_account_id: str | int | float | bool | None


def _canonical_profile_url(value: str) -> str:
    try:
        parsed = urlsplit(value)
        port = parsed.port
    except ValueError:
        port = -1
        parsed = urlsplit("")
    if (
        _UNSAFE_PROFILE_URL_PATTERN.search(value) is not None
        or parsed.scheme != "https"
        or parsed.netloc != "www.linkedin.com"
        or parsed.hostname != "www.linkedin.com"
        or parsed.username is not None
        or parsed.password is not None
        or port is not None
        or parsed.query
        or parsed.fragment
        or _PROFILE_PATH_PATTERN.fullmatch(parsed.path) is None
        or any(segment in {".", ".."} for segment in parsed.path.split("/"))
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
        hide_input_in_errors=True,
        validate_default=True,
    )

    schema_version: Literal[1] = 1
    id: AccountId = Field(pattern=r"^linkedin:(?:person|company):[0-9]+$")
    platform: Literal[Platform.LINKEDIN]
    kind: AccountKind
    platform_account_id: _CanonicalPlatformAccountId
    profile_url: _CanonicalProfileUrl
    url_aliases: tuple[_CanonicalProfileUrl, ...] = Field(
        json_schema_extra={"uniqueItems": True}
    )
    first_seen_at: datetime

    @model_validator(mode="before")
    @classmethod
    def redact_invalid_platform_identity(
        cls,
        values: _AccountBoundaryInput,
    ) -> _AccountBoundaryInput:
        """Replace malformed identity input before Pydantic renders errors."""
        redacted = values.copy()
        input_changed = False
        match values.get("platform_account_id"):
            case str() as platform_id if (
                re.fullmatch(
                    r"[0-9]+",
                    platform_id,
                    flags=re.ASCII,
                )
                is None
            ):
                redacted["platform_account_id"] = "<redacted>"
                input_changed = True
            case _:
                pass
        match values.get("id"):
            case str() as account_id if (
                _CANONICAL_ACCOUNT_ID_PATTERN.fullmatch(account_id) is None
            ):
                redacted["id"] = "<redacted>"
                input_changed = True
            case _:
                pass
        return redacted if input_changed else values

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
        expected_path = _account_kind_path(self.kind)
        all_urls = (self.profile_url, *self.url_aliases)
        if not all(
            urlsplit(value).path.startswith(expected_path) for value in all_urls
        ):
            raise PydanticCustomError(
                _ACCOUNT_KIND_URL_ERROR_CODE,
                _ACCOUNT_KIND_URL_ERROR_MESSAGE,
            )
        return self


def _account_kind_path(kind: AccountKind) -> str:
    match kind:
        case AccountKind.PERSON:
            return "/in/"
        case AccountKind.COMPANY:
            return "/company/"
    assert_never(kind)
