from __future__ import annotations

from datetime import UTC, date, datetime

import pytest
from pydantic import SecretStr, ValidationError

import social_media_subscriber.accounts.locator as locator_module
from social_media_subscriber.accounts.errors import AccountInputError
from social_media_subscriber.application.windows import (
    ExplicitWindow,
    WindowContext,
    build_post_requests,
)
from social_media_subscriber.domain.account import Account
from social_media_subscriber.domain.ids import PlatformPostId
from social_media_subscriber.domain.platform import AccountKind, Platform
from social_media_subscriber.domain.post import Post
from social_media_subscriber.platforms.x import (
    XPostUrlError,
    canonical_platform_post_id,
    canonical_post_url,
)
from social_media_subscriber.runtime_input import SourceId, load_runtime_input
from social_media_subscriber.settings import Settings

_FIRST_SEEN = datetime(2026, 8, 26, 10, tzinfo=UTC)


@pytest.mark.parametrize(
    ("raw", "canonical"),
    [
        ("https://x.com/OpenAI", "https://x.com/openai/"),
        ("https://www.x.com/OpenAI/", "https://x.com/openai/"),
        ("https://twitter.com/OpenAI", "https://x.com/openai/"),
        ("https://mobile.twitter.com/OpenAI/", "https://x.com/openai/"),
    ],
)
def test_x_locator_canonicalizes_supported_public_profile_urls(
    raw: str,
    canonical: str,
) -> None:
    # Given / When
    locator = locator_module.parse_account_locator(raw)

    # Then
    assert locator.platform is Platform.X
    assert locator.kind is AccountKind.PROFILE
    assert locator.canonical_url == canonical


@pytest.mark.parametrize(
    "raw",
    [
        "http://x.com/openai",
        "https://x.com/openai/status/1",
        "https://x.com/home",
        "https://x.com/login",
        "https://x.com/signup",
        "https://x.com/about",
        "https://x.com/download",
        "https://x.com/help",
        "https://x.com/privacy",
        "https://x.com/tos",
        "https://x.com/openai?lang=en",
        "https://user@x.com/openai",
        "https://x.com:443/openai",
        "https://x.com/open%2fai",
        "https://x.com/open-ai",
        "https://example.com/openai",
    ],
)
def test_x_locator_rejects_non_profile_or_unsafe_urls(raw: str) -> None:
    # Given / When / Then
    with pytest.raises(AccountInputError):
        _ = locator_module.parse_account_locator(raw)


def test_runtime_input_deduplicates_x_aliases_by_canonical_url() -> None:
    # Given
    settings = Settings(
        accounts=SecretStr("https://twitter.com/OpenAI\nhttps://x.com/openai/"),
        sources=SecretStr("brightdata:synthetic-key"),
    )

    # When
    runtime_input = load_runtime_input(settings)

    # Then
    assert tuple(locator.canonical_url for locator in runtime_input.locators) == (
        "https://x.com/openai/",
    )
    assert tuple(source.source_id for source in runtime_input.sources) == (
        SourceId.BRIGHTDATA,
    )


def test_x_account_and_post_use_platform_qualified_runtime_identity() -> None:
    # Given
    account = Account(
        platform=Platform.X,
        kind=AccountKind.PROFILE,
        profile_url="https://x.com/openai/",
        first_seen_at=_FIRST_SEEN,
    )

    # When
    post = Post(
        platform_post_id=PlatformPostId("2039126434510418303"),
        account_profile_url=account.id,
        canonical_url="https://x.com/openai/status/2039126434510418303",
        published_at=datetime(2026, 8, 26, 9, 30, 0, 123456, tzinfo=UTC),
        type="post",
        content={"text": "Synthetic X architecture fixture"},
        first_seen_at=_FIRST_SEEN,
    )

    # Then
    assert account.id == "https://x.com/openai/"
    assert post.platform is Platform.X
    assert post.id == "x:post:2039126434510418303"
    assert post.published_at == datetime(2026, 8, 26, 9, 30, tzinfo=UTC)
    assert "platform" not in post.model_dump()


@pytest.mark.parametrize("value", ["0", "01", "0001", "not-numeric"])
def test_x_post_identity_rejects_noncanonical_numeric_spelling(value: str) -> None:
    # Given / When / Then
    with pytest.raises(XPostUrlError):
        _ = canonical_platform_post_id(value)


@pytest.mark.parametrize(
    "handle",
    ["about", "download", "help", "login", "privacy", "signup", "tos"],
)
def test_x_post_url_rejects_reserved_application_routes(handle: str) -> None:
    # Given
    url = f"https://x.com/{handle}/status/123"

    # When / Then
    with pytest.raises(XPostUrlError):
        _ = canonical_post_url(url)


@pytest.mark.parametrize(
    "platform_post_id",
    [" 2039126434510418303 ", "\u20032039126434510418303"],
)
def test_x_post_rejects_noncanonical_platform_id_spelling(
    platform_post_id: str,
) -> None:
    # Given
    values = {
        "platform_post_id": PlatformPostId(platform_post_id),
        "account_profile_url": "https://x.com/openai/",
        "canonical_url": "https://x.com/openai/status/2039126434510418303",
        "published_at": datetime(2026, 8, 26, 9, 30, tzinfo=UTC),
        "type": "post",
        "content": {"text": "Synthetic X architecture fixture"},
        "first_seen_at": _FIRST_SEEN,
    }

    # When / Then
    with pytest.raises(ValidationError):
        _ = Post.model_validate(values)


def test_x_post_rejects_cross_platform_canonical_url() -> None:
    # Given
    values = {
        "platform_post_id": PlatformPostId("2039126434510418303"),
        "account_profile_url": "https://x.com/openai/",
        "canonical_url": (
            "https://www.linkedin.com/feed/update/urn:li:activity:2039126434510418303/"
        ),
        "published_at": datetime(2026, 8, 26, 9, 30, tzinfo=UTC),
        "type": "post",
        "content": {"text": "Synthetic X architecture fixture"},
        "first_seen_at": _FIRST_SEEN,
    }

    # When / Then
    with pytest.raises(ValidationError):
        _ = Post.model_validate(values)


def test_new_x_account_uses_x_platform_history_boundary() -> None:
    # Given
    account = Account(
        platform=Platform.X,
        kind=AccountKind.PROFILE,
        profile_url="https://x.com/openai/",
        first_seen_at=_FIRST_SEEN,
    )
    context = WindowContext(_FIRST_SEEN, ExplicitWindow.parse(None, None))

    # When
    requests = build_post_requests((account,), None, context)

    # Then
    assert requests[0].start_date == date(2006, 3, 21)
    assert requests[0].end_date == date(2026, 8, 26)
