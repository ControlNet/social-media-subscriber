"""Frozen canonical Account boundary contract."""

from datetime import datetime
from typing import Annotated, ClassVar, Final, Self

from pydantic import (
    BaseModel,
    ConfigDict,
    WithJsonSchema,
    field_validator,
    model_validator,
)
from pydantic_core import PydanticCustomError

from social_media_subscriber.accounts.errors import AccountInputError
from social_media_subscriber.accounts.locator import (
    AccountLocator,
    parse_account_locator,
)
from social_media_subscriber.domain.ids import ACCOUNT_ID_PATTERN, AccountId
from social_media_subscriber.domain.platform import AccountKind, Platform
from social_media_subscriber.domain.time import canonical_utc

_CanonicalProfileUrl = Annotated[
    str,
    WithJsonSchema({"type": "string", "pattern": ACCOUNT_ID_PATTERN}),
]
_PROFILE_URL_ERROR_CODE: Final = "canonical_profile_url"
_PROFILE_URL_ERROR_MESSAGE: Final = "value must be a canonical public account URL"
_ACCOUNT_KIND_URL_ERROR_CODE: Final = "account_kind_url_mismatch"
_ACCOUNT_KIND_URL_ERROR_MESSAGE: Final = "profile URL does not match account kind"
_ACCOUNT_PLATFORM_URL_ERROR_CODE: Final = "account_platform_url_mismatch"
_ACCOUNT_PLATFORM_URL_ERROR_MESSAGE: Final = "profile URL does not match platform"


class Account(BaseModel):
    """Provider-neutral Account identified by its canonical profile URL."""

    model_config: ClassVar[ConfigDict] = ConfigDict(
        extra="forbid",
        frozen=True,
        hide_input_in_errors=True,
        validate_default=True,
    )

    platform: Platform
    kind: AccountKind
    profile_url: _CanonicalProfileUrl
    first_seen_at: datetime

    @property
    def id(self) -> AccountId:
        """Return the canonical URL identity without persisting a duplicate field."""
        return AccountId(self.profile_url)

    @field_validator("first_seen_at")
    @classmethod
    def validate_first_seen_at(cls, value: datetime) -> datetime:
        """Require canonical UTC discovery time."""
        return canonical_utc(value)

    @model_validator(mode="after")
    def validate_profile_url(self) -> Self:
        """Require Account kind and canonical URL shape to agree."""
        locator = _canonical_account_locator(self.profile_url)
        if locator.platform is not self.platform:
            raise PydanticCustomError(
                _ACCOUNT_PLATFORM_URL_ERROR_CODE,
                _ACCOUNT_PLATFORM_URL_ERROR_MESSAGE,
            )
        if locator.kind is not self.kind:
            raise PydanticCustomError(
                _ACCOUNT_KIND_URL_ERROR_CODE,
                _ACCOUNT_KIND_URL_ERROR_MESSAGE,
            )
        return self


def _canonical_account_locator(value: str) -> AccountLocator:
    """Validate through the strict parser's canonicalization authority."""
    try:
        locator = parse_account_locator(value)
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
    return locator
