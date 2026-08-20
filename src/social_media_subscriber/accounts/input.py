"""Single strict boundary for account locators and provider credentials."""

from __future__ import annotations

from typing import TYPE_CHECKING, ClassVar

from pydantic import BaseModel, ConfigDict, SecretStr

from social_media_subscriber.accounts.errors import (
    AccountInputError,
    AccountInputErrorCategory,
    AccountInputField,
)
from social_media_subscriber.accounts.locator import (
    LinkedInLocator,
    parse_linkedin_locator,
)

if TYPE_CHECKING:
    from social_media_subscriber.settings import Settings


class AccountInput(BaseModel):
    """Frozen parsed runtime input safe for downstream construction."""

    model_config: ClassVar[ConfigDict] = ConfigDict(frozen=True)

    locators: tuple[LinkedInLocator, ...]
    bright_data_api_keys: tuple[SecretStr, ...]


def _normalized_lines(secret: SecretStr) -> tuple[str, ...]:
    normalized = secret.get_secret_value().replace("\r\n", "\n").replace("\r", "\n")
    return tuple(line.strip() for line in normalized.split("\n") if line.strip())


def _deduplicate_values(values: tuple[str, ...]) -> tuple[str, ...]:
    return tuple(dict.fromkeys(values))


def load_account_input(settings: Settings) -> AccountInput:
    """Parse all secrets atomically before downstream resources exist."""
    account_lines = _normalized_lines(settings.accounts)
    key_lines = _normalized_lines(settings.bright_data_api_keys)
    if not account_lines:
        raise AccountInputError(
            category=AccountInputErrorCategory.EMPTY_ACCOUNTS,
            field=AccountInputField.ACCOUNTS,
        )
    if not key_lines:
        raise AccountInputError(
            category=AccountInputErrorCategory.EMPTY_BRIGHT_DATA_API_KEYS,
            field=AccountInputField.BRIGHT_DATA_API_KEYS,
        )

    locators_by_url: dict[str, LinkedInLocator] = {}
    for line in account_lines:
        locator = parse_linkedin_locator(line)
        if locator.canonical_url not in locators_by_url:
            locators_by_url[locator.canonical_url] = locator

    return AccountInput(
        locators=tuple(locators_by_url.values()),
        bright_data_api_keys=tuple(
            SecretStr(key) for key in _deduplicate_values(key_lines)
        ),
    )
