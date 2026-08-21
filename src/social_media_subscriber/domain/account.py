"""Frozen canonical Account boundary contract."""

from datetime import datetime
from typing import Annotated, ClassVar, Final, Literal, Self, TypedDict

from pydantic import (
    BaseModel,
    ConfigDict,
    WithJsonSchema,
    field_validator,
    model_validator,
)
from pydantic_core import PydanticCustomError

from social_media_subscriber.accounts.errors import AccountInputError
from social_media_subscriber.accounts.locator import parse_linkedin_locator
from social_media_subscriber.domain.ids import (
    ACCOUNT_ID_PATTERN,
    AccountId,
    is_canonical_account_id,
)
from social_media_subscriber.domain.platform import AccountKind, Platform
from social_media_subscriber.domain.time import canonical_utc

_CanonicalAccountId = Annotated[
    AccountId,
    WithJsonSchema({"type": "string", "pattern": ACCOUNT_ID_PATTERN}),
]
_CanonicalProfileUrl = Annotated[
    str,
    WithJsonSchema({"type": "string", "pattern": ACCOUNT_ID_PATTERN}),
]
_PROFILE_URL_ERROR_CODE: Final = "canonical_profile_url"
_PROFILE_URL_ERROR_MESSAGE: Final = (
    "value must be a canonical public LinkedIn profile URL"
)
_ACCOUNT_ID_ERROR_CODE: Final = "account_id_mismatch"
_ACCOUNT_ID_ERROR_MESSAGE: Final = "account id must equal profile URL"
_ACCOUNT_KIND_URL_ERROR_CODE: Final = "account_kind_url_mismatch"
_ACCOUNT_KIND_URL_ERROR_MESSAGE: Final = "profile URL does not match account kind"


class _AccountBoundaryInput(TypedDict, total=False):
    id: str | int | float | bool | None


class Account(BaseModel):
    """Versioned provider-neutral Account record persisted at the boundary."""

    model_config: ClassVar[ConfigDict] = ConfigDict(
        extra="forbid",
        frozen=True,
        hide_input_in_errors=True,
        validate_default=True,
    )

    schema_version: Literal[2] = 2
    id: _CanonicalAccountId
    platform: Literal[Platform.LINKEDIN]
    kind: AccountKind
    profile_url: _CanonicalProfileUrl
    first_seen_at: datetime

    @model_validator(mode="before")
    @classmethod
    def redact_invalid_identity(
        cls,
        values: _AccountBoundaryInput,
    ) -> _AccountBoundaryInput:
        """Replace malformed identity input before Pydantic renders errors."""
        match values.get("id"):
            case str() as account_id if not is_canonical_account_id(account_id):
                redacted = values.copy()
                redacted["id"] = "<redacted>"
                return redacted
            case _:
                return values

    @field_validator("id")
    @classmethod
    def validate_id(cls, value: str) -> AccountId:
        """Require the Account ID to be an exact canonical LinkedIn URL."""
        _ = _canonical_account_kind(value)
        return AccountId(value)

    @field_validator("profile_url")
    @classmethod
    def validate_profile_url(cls, value: str) -> str:
        """Require the profile URL to be exact and canonical."""
        _ = _canonical_account_kind(value)
        return value

    @field_validator("first_seen_at")
    @classmethod
    def validate_first_seen_at(cls, value: datetime) -> datetime:
        """Require canonical UTC discovery time."""
        return canonical_utc(value)

    @model_validator(mode="after")
    def validate_identity(self) -> Self:
        """Require exact URL identity and Account kind agreement."""
        if self.id != self.profile_url:
            raise PydanticCustomError(
                _ACCOUNT_ID_ERROR_CODE,
                _ACCOUNT_ID_ERROR_MESSAGE,
            )
        if _canonical_account_kind(self.id) is not self.kind:
            raise PydanticCustomError(
                _ACCOUNT_KIND_URL_ERROR_CODE,
                _ACCOUNT_KIND_URL_ERROR_MESSAGE,
            )
        return self


def _canonical_account_kind(value: str) -> AccountKind:
    """Validate through the strict parser's canonicalization authority."""
    try:
        locator = parse_linkedin_locator(value)
    except AccountInputError:
        raise PydanticCustomError(
            _PROFILE_URL_ERROR_CODE,
            _PROFILE_URL_ERROR_MESSAGE,
        ) from None
    if locator.canonical_url != value:
        raise PydanticCustomError(
            _PROFILE_URL_ERROR_CODE,
            _PROFILE_URL_ERROR_MESSAGE,
        )
    return locator.kind
