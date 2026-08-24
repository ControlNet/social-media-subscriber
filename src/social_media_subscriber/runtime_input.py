"""Strict boundary for account locators and configured runtime sources."""

from __future__ import annotations

from enum import StrEnum, unique
from typing import TYPE_CHECKING, ClassVar

from pydantic import BaseModel, ConfigDict, Field, SecretStr

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


@unique
class SourceId(StrEnum):
    """Explicitly supported runtime source identifiers."""

    BRIGHTDATA = "brightdata"


class SourceInput(BaseModel):
    """One ordered credential-backed source enabled for this run."""

    model_config: ClassVar[ConfigDict] = ConfigDict(frozen=True)

    source_id: SourceId
    credential: SecretStr


class RuntimeInput(BaseModel):
    """Frozen parsed runtime input safe for downstream construction."""

    model_config: ClassVar[ConfigDict] = ConfigDict(frozen=True)

    locators: tuple[LinkedInLocator, ...] = Field(min_length=1)
    sources: tuple[SourceInput, ...] = Field(min_length=1)


def _normalized_lines(secret: SecretStr) -> tuple[str, ...]:
    normalized = secret.get_secret_value().replace("\r\n", "\n").replace("\r", "\n")
    return tuple(line.strip() for line in normalized.split("\n") if line.strip())


def _invalid_source(category: AccountInputErrorCategory) -> AccountInputError:
    return AccountInputError(category=category, field=AccountInputField.SOURCES)


def _parse_sources(lines: tuple[str, ...]) -> tuple[SourceInput, ...]:
    sources: list[SourceInput] = []
    seen: set[tuple[SourceId, str]] = set()
    for line in lines:
        source_text, separator, credential_text = line.partition(":")
        source_value = source_text.strip().lower()
        credential = credential_text.strip()
        if not separator or not source_value or not credential:
            raise _invalid_source(AccountInputErrorCategory.INVALID_SOURCE)
        try:
            source_id = SourceId(source_value)
        except ValueError:
            raise _invalid_source(
                AccountInputErrorCategory.UNSUPPORTED_SOURCE
            ) from None
        identity = (source_id, credential)
        if identity in seen:
            continue
        seen.add(identity)
        sources.append(
            SourceInput(source_id=source_id, credential=SecretStr(credential))
        )
    return tuple(sources)


def load_runtime_input(settings: Settings) -> RuntimeInput:
    """Parse all secrets atomically before downstream resources exist."""
    account_lines = _normalized_lines(settings.accounts)
    source_lines = _normalized_lines(settings.sources)
    if not account_lines:
        raise AccountInputError(
            category=AccountInputErrorCategory.EMPTY_ACCOUNTS,
            field=AccountInputField.ACCOUNTS,
        )
    if not source_lines:
        raise _invalid_source(AccountInputErrorCategory.EMPTY_SOURCES)

    locators_by_url: dict[str, LinkedInLocator] = {}
    for line in account_lines:
        locator = parse_linkedin_locator(line)
        if locator.canonical_url not in locators_by_url:
            locators_by_url[locator.canonical_url] = locator

    return RuntimeInput(
        locators=tuple(locators_by_url.values()),
        sources=_parse_sources(source_lines),
    )
