from __future__ import annotations

import logging
from typing import TYPE_CHECKING

import pytest
from hypothesis import given
from hypothesis import strategies as st
from pydantic import SecretStr, ValidationError

from social_media_subscriber.accounts.errors import (
    AccountInputError,
    AccountInputErrorCategory,
    AccountInputField,
)
from social_media_subscriber.accounts.locator import parse_linkedin_locator
from social_media_subscriber.domain.platform import AccountKind, Platform
from social_media_subscriber.runtime_input import (
    RuntimeInput,
    SourceId,
    load_runtime_input,
)
from social_media_subscriber.settings import Settings

if TYPE_CHECKING:
    from collections.abc import Callable


def test_settings_load_multiline_secrets_from_environment(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Given
    monkeypatch.setenv("ACCOUNTS", "https://linkedin.com/in/Ada")
    monkeypatch.setenv("SOURCES", "brightdata:synthetic-key")

    # When
    settings = Settings.model_validate({})

    # Then
    assert isinstance(settings.accounts, SecretStr)
    assert isinstance(settings.sources, SecretStr)
    assert "Ada" not in repr(settings)
    assert "synthetic-key" not in repr(settings)


def test_settings_are_frozen() -> None:
    # Given
    settings = Settings(
        accounts=SecretStr("https://linkedin.com/in/Ada"),
        sources=SecretStr("brightdata:synthetic-key"),
    )

    # When / Then
    with pytest.raises(ValidationError):
        Settings.__setattr__(
            settings,
            "accounts",
            SecretStr("https://linkedin.com/in/Grace"),
        )


@pytest.mark.parametrize(
    ("raw_url", "expected_url", "expected_kind"),
    [
        (
            "https://linkedin.com/in/Ada-Lovelace?trk=public#bio",
            "https://www.linkedin.com/in/Ada-Lovelace/",
            AccountKind.PERSON,
        ),
        (
            "https://www.linkedin.com/company/OpenAI/",
            "https://www.linkedin.com/company/OpenAI/",
            AccountKind.COMPANY,
        ),
        (
            "https://DE.linkedin.com/in/MixedCase",
            "https://www.linkedin.com/in/MixedCase/",
            AccountKind.PERSON,
        ),
    ],
)
def test_locator_is_canonical_when_public_linkedin_url_is_valid(
    raw_url: str,
    expected_url: str,
    expected_kind: AccountKind,
) -> None:
    # Given / When
    locator = parse_linkedin_locator(raw_url)

    # Then
    assert locator.platform is Platform.LINKEDIN
    assert locator.kind is expected_kind
    assert locator.canonical_url == expected_url


@pytest.mark.parametrize(
    "raw_url",
    [
        "http://linkedin.com/in/ada",
        "ftp://linkedin.com/in/ada",
        "https://user@linkedin.com/in/ada",
        "https://user:password@linkedin.com/in/ada",
        "https://linkedin.com:443/in/ada",
        "https://linkedin.com:invalid/in/ada",
        "https://127.0.0.1/in/ada",
        "https://[::1]/in/ada",
        "https://linkedin.com.example/in/ada",
        "https://evil-linkedin.com/in/ada",
        "https://www.linkedin.com.evil/in/ada",
        "https://english.linkedin.com/in/ada",
        "https://a.linkedin.com/in/ada",
        "https://abcd.linkedin.com/in/ada",
        "https://linkedin.com/in",
        "https://linkedin.com/in/",
        "https://linkedin.com/company",
        "https://linkedin.com/company/",
        "https://linkedin.com/posts/ada",
        "https://linkedin.com/in/ada/extra",
        "https://linkedin.com//in/ada",
        "https://linkedin.com/in/ada//",
        "https://linkedin.com/in/%2Fadmin",
        "https://linkedin.com/in/%5cadmin",
        "https://linkedin.com/in/%2e",
        "https://linkedin.com/in/%2E%2E",
        "https://linkedin.com/in/.",
        "https://linkedin.com/in/..",
        "https://linkedin.com/in/ada%2Fadmin",
        "https://linkedin.com/in/ada%5Cadmin",
        "https://linkedin.com/in/%00",
        "https://linkedin.com/in/%1F",
        "https://linkedin.com/in/%7F",
        "https://linkedin.com/in/%",
        "https://linkedin.com/in/%2",
        "https://linkedin.com/in/%GG",
        "https://linkedin.com/in/ada\\admin",
        "https:///in/ada",
        "not-a-url",
        "",
    ],
)
def test_locator_is_rejected_when_url_shape_is_unsafe(raw_url: str) -> None:
    # Given / When
    with pytest.raises(AccountInputError) as caught:
        _ = parse_linkedin_locator(raw_url)

    # Then
    assert caught.value.category is AccountInputErrorCategory.INVALID_ACCOUNT_URL
    assert caught.value.field is AccountInputField.ACCOUNTS
    assert not raw_url or raw_url not in str(caught.value)
    assert not raw_url or raw_url not in repr(caught.value)


@given(
    suffix=st.text(
        alphabet=st.characters(
            whitelist_categories=("Ll", "Lu", "Nd"),
            min_codepoint=48,
            max_codepoint=122,
        ),
        min_size=0,
        max_size=12,
    )
)
def test_locator_is_rejected_when_percent_escape_is_malformed(suffix: str) -> None:
    # Given
    raw_url = f"https://linkedin.com/in/valid%G{suffix}"

    # When
    with pytest.raises(AccountInputError) as caught:
        _ = parse_linkedin_locator(raw_url)

    # Then
    assert caught.value.category is AccountInputErrorCategory.INVALID_ACCOUNT_URL
    assert caught.value.field is AccountInputField.ACCOUNTS
    assert raw_url not in str(caught.value)
    assert raw_url not in repr(caught.value)


@given(
    segment=st.text(
        alphabet=st.sampled_from(
            "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789-_"
        ),
        min_size=1,
        max_size=40,
    )
)
def test_locator_preserves_segment_spelling_when_generated_slug_is_valid(
    segment: str,
) -> None:
    # Given
    raw_url = f"https://fr.linkedin.com/in/{segment}?tracking=removed#fragment"

    # When
    locator = parse_linkedin_locator(raw_url)

    # Then
    assert locator.canonical_url == f"https://www.linkedin.com/in/{segment}/"


def test_runtime_input_normalizes_deduplicates_and_preserves_source_order() -> None:
    # Given
    account_lines = (
        "\r\n  https://linkedin.com/in/Ada?trk=one  \r\n",
        "https://de.linkedin.com/in/Ada/#fragment\r\n",
        "https://linkedin.com/company/OpenAI\r\n\r\n",
    )
    source_lines = (
        "\r\n  BrightData:synthetic-key-one  \r\n",
        "brightdata:synthetic-key-one\r\n",
        " brightdata:synthetic:key-two \r\n",
        " Apify:synthetic-apify-one \r\n",
        "apify:synthetic-apify-two\r\n",
    )
    settings = Settings(
        accounts=SecretStr("".join(account_lines)),
        sources=SecretStr("".join(source_lines)),
    )

    # When
    runtime_input = load_runtime_input(settings)

    # Then
    assert tuple(locator.canonical_url for locator in runtime_input.locators) == (
        "https://www.linkedin.com/in/Ada/",
        "https://www.linkedin.com/company/OpenAI/",
    )
    assert tuple(source.source_id for source in runtime_input.sources) == (
        SourceId.BRIGHTDATA,
        SourceId.BRIGHTDATA,
        SourceId.APIFY,
        SourceId.APIFY,
    )
    assert tuple(
        source.credential.get_secret_value() for source in runtime_input.sources
    ) == (
        "synthetic-key-one",
        "synthetic:key-two",
        "synthetic-apify-one",
        "synthetic-apify-two",
    )


@pytest.mark.parametrize(
    ("accounts", "sources", "category", "field"),
    [
        (
            " \r\n\n ",
            "brightdata:synthetic-key",
            AccountInputErrorCategory.EMPTY_ACCOUNTS,
            AccountInputField.ACCOUNTS,
        ),
        (
            "https://linkedin.com/in/Ada",
            " \r\n\n ",
            AccountInputErrorCategory.EMPTY_SOURCES,
            AccountInputField.SOURCES,
        ),
    ],
)
def test_runtime_input_is_rejected_when_required_set_is_empty(
    accounts: str,
    sources: str,
    category: AccountInputErrorCategory,
    field: AccountInputField,
) -> None:
    # Given
    settings = Settings(
        accounts=SecretStr(accounts),
        sources=SecretStr(sources),
    )

    # When
    with pytest.raises(AccountInputError) as caught:
        _ = load_runtime_input(settings)

    # Then
    assert caught.value.category is category
    assert caught.value.field is field
    assert accounts not in str(caught.value)
    assert sources not in str(caught.value)


@pytest.mark.parametrize(
    ("raw_source", "category"),
    [
        ("missing-separator", AccountInputErrorCategory.INVALID_SOURCE),
        (":credential", AccountInputErrorCategory.INVALID_SOURCE),
        ("brightdata:", AccountInputErrorCategory.INVALID_SOURCE),
        ("unknown:credential", AccountInputErrorCategory.UNSUPPORTED_SOURCE),
    ],
)
def test_source_line_is_rejected_atomically_without_exposing_credential(
    raw_source: str,
    category: AccountInputErrorCategory,
) -> None:
    # Given
    settings = Settings(
        accounts=SecretStr("https://linkedin.com/in/Ada"),
        sources=SecretStr(raw_source),
    )

    # When
    with pytest.raises(AccountInputError) as caught:
        _ = load_runtime_input(settings)

    # Then
    assert caught.value.category is category
    assert caught.value.field is AccountInputField.SOURCES
    assert raw_source not in str(caught.value)
    assert raw_source not in repr(caught.value)


def test_whole_input_is_rejected_before_factory_when_one_line_is_invalid() -> None:
    # Given
    canary_url = "https://linkedin.com.evil/in/private-canary"
    canary_source = "brightdata:synthetic-private-key-canary"
    settings = Settings(
        accounts=SecretStr(
            "".join(
                (
                    f"https://linkedin.com/in/Ada\n{canary_url}\n",
                    "https://linkedin.com/company/OpenAI",
                )
            )
        ),
        sources=SecretStr(canary_source),
    )
    factory_calls = 0

    def factory(_: RuntimeInput) -> None:
        nonlocal factory_calls
        factory_calls += 1

    def construct(factory_callable: Callable[[RuntimeInput], None]) -> None:
        factory_callable(load_runtime_input(settings))

    # When
    with pytest.raises(AccountInputError) as caught:
        construct(factory)

    # Then
    assert factory_calls == 0
    assert canary_url not in str(caught.value)
    assert canary_source not in str(caught.value)
    assert canary_url not in repr(caught.value)
    assert canary_source not in repr(caught.value)


def test_invalid_input_does_not_reach_logs_or_exception_text(
    caplog: pytest.LogCaptureFixture,
) -> None:
    # Given
    canary_url = "https://linkedin.com/in/canary%2Fprivate"
    canary_source = "brightdata:synthetic-private-key-canary"
    settings = Settings(
        accounts=SecretStr(canary_url),
        sources=SecretStr(canary_source),
    )

    # When
    with caplog.at_level(logging.DEBUG), pytest.raises(AccountInputError) as caught:
        _ = load_runtime_input(settings)

    # Then
    assert canary_url not in caplog.text
    assert canary_source not in caplog.text
    assert canary_url not in str(caught.value)
    assert canary_source not in str(caught.value)
    assert canary_url not in repr(caught.value)
    assert canary_source not in repr(caught.value)


def test_legacy_bright_data_keys_are_not_a_supported_setting(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Given
    monkeypatch.setenv("ACCOUNTS", "https://linkedin.com/in/Ada")
    monkeypatch.setenv("BRIGHT_DATA_API_KEYS", "synthetic-token")
    monkeypatch.delenv("SOURCES", raising=False)

    # When / Then
    with pytest.raises(ValidationError):
        _ = Settings.model_validate({})
