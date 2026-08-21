from __future__ import annotations

__test__ = False

from dataclasses import replace
from datetime import UTC, datetime
from typing import TYPE_CHECKING, assert_type

import pytest
from pydantic import ValidationError
from pydantic_core import PydanticCustomError

from social_media_subscriber.domain.ids import (
    AccountId,
    PlatformPostId,
    PostId,
    post_id_for,
)
from social_media_subscriber.domain.post import Post, PostKind, StablePostContent
from tests.unit.test_domain_account import FIRST_SEEN, _account

if TYPE_CHECKING:
    from pathlib import Path

__all__ = ("PUBLISHED", "_post", "_stable_post")

PUBLISHED = datetime(2026, 8, 19, 8, 15, tzinfo=UTC)
UNSAFE_POST_URLS = [
    "https://www.linkedin.com/posts/syn\\thetic",
    "https://www.linkedin.com/posts/syn%2fthetic",
    "https://www.linkedin.com/posts/syn%2Fthetic",
    "https://www.linkedin.com/posts/syn%5cthetic",
    "https://www.linkedin.com/posts/syn%5Cthetic",
    "https://www.linkedin.com/posts/syn%2ethetic",
    "https://www.linkedin.com/posts/syn%2Ethetic",
    "https://www.linkedin.com/posts/%2e%2E",
    "https://www.linkedin.com/posts/syn%09thetic",
    "https://www.linkedin.com/posts/syn%0athetic",
    "https://www.linkedin.com/posts/syn%0Athetic",
    "https://www.linkedin.com/posts/syn%0dthetic",
    "https://www.linkedin.com/posts/syn%0Dthetic",
    "https://www.linkedin.com/posts/syn%7fthetic",
    "https://www.linkedin.com/posts/syn%7Fthetic",
    "https://www.linkedin.com/posts/./synthetic",
    "https://www.linkedin.com/posts/../synthetic",
]
UNSAFE_APPROVED_LINKS = [
    "https://example.com/syn\\thetic",
    "https://example.com/syn%2fthetic",
    "https://example.com/syn%2Fthetic",
    "https://example.com/syn%5cthetic",
    "https://example.com/syn%5Cthetic",
    "https://example.com/syn%2ethetic",
    "https://example.com/syn%2Ethetic",
    "https://example.com/%2e%2E",
    "https://example.com/./synthetic",
    "https://example.com/../synthetic",
    "https://example.com/?next=%2fadmin",
    "https://example.com/?next=%2Fadmin",
    "https://example.com/?next=%5cadmin",
    "https://example.com/?next=%5Cadmin",
    "https://example.com/?next=%2e%2E",
    "https://example.com/?next=%2E%2e",
    "https://example.com/?next=%09admin",
    "https://example.com/?next=%0aadmin",
    "https://example.com/?next=%0Aadmin",
    "https://example.com/?next=%0dadmin",
    "https://example.com/?next=%0Dadmin",
    "https://example.com/?next=%7fadmin",
    "https://example.com/?next=%7Fadmin",
]
VALID_POST_ACCOUNT_IDS = [
    AccountId("linkedin:person:0"),
    AccountId("linkedin:person:123"),
    AccountId("linkedin:company:456"),
]
INVALID_POST_ACCOUNT_IDS = [
    AccountId(""),
    AccountId(" "),
    AccountId("123"),
    AccountId("linkedin:person:"),
    AccountId("linkedin:person: 123"),
    AccountId("linkedin:person:123 "),
    AccountId("linkedin:person:+123"),
    AccountId("linkedin:person:12.3"),
    AccountId("linkedin:person:\uff11\uff12\uff13"),
    AccountId("linkedin:person:abc"),
    AccountId("linkedin:person:12/34"),
    AccountId("linkedin:person:../../x"),
    AccountId("linkedin:person:123:456"),
    AccountId("linkedin:person:123\n"),
    AccountId("linkedin:company:../../x"),
    AccountId("linkedin:group:123"),
    AccountId("urn:li:person:123"),
    AccountId("LinkedIn:person:123"),
]


def _stable_post(account_id: AccountId) -> StablePostContent:
    platform_post_id = PlatformPostId("urn:li:activity:123")
    return StablePostContent(
        schema_version=1,
        id=post_id_for(platform_post_id),
        platform_post_id=platform_post_id,
        account_id=account_id,
        canonical_url="https://www.linkedin.com/posts/ada_example-123/",
        published_at=PUBLISHED,
        text="Hello\r\n\r\nworld  ",
        kind=PostKind.ORIGINAL,
        hashtags=("python", "ai", "python"),
        links=("https://example.com/z", "https://example.com/a"),
    )


def _post(account_id: AccountId) -> Post:
    stable = _stable_post(account_id)
    return Post.from_stable(stable, FIRST_SEEN)


def test_post_normalizes_stable_content_and_verifies_hash() -> None:
    # Given
    account = _account()

    # When
    post = _post(account.id)

    # Then
    assert post.text == "Hello\n\nworld"
    assert post.hashtags == ("ai", "python")
    assert post.links == ("https://example.com/a", "https://example.com/z")
    assert len(post.content_hash) == 64
    _ = assert_type(post.id, PostId)
    _ = assert_type(post.account_id, AccountId)


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("published_at", "2026-08-19T08:15:00"),
        ("id", "linkedin:person:12345"),
        ("canonical_url", "https://evil.example/posts/123"),
        ("canonical_url", "https://www.linkedin.com/posts/example-123\n"),
        ("canonical_url", "https://www.linkedin.com/po\tsts/example-123"),
        ("canonical_url", "https://www.linkedin.com/posts/example%0A123"),
        ("kind", "repost"),
        ("links", ("https://example.com/?access_token=canary",)),
        ("links", ("https://example.com/path\n",)),
        ("links", ("https://example.com/pa\tth",)),
        ("links", ("https://example.com/path%0D%0Aheader",)),
    ],
)
def test_post_rejects_invalid_boundary_values(
    field: str, value: str | datetime | tuple[str, ...]
) -> None:
    # Given
    account = _account()
    values = _post(account.id).model_dump()
    values[field] = value

    # When / Then
    with pytest.raises(ValidationError):
        _ = Post.model_validate(values)


def test_post_rejects_content_hash_that_does_not_match_stable_fields() -> None:
    # Given
    account = _account()
    values = _post(account.id).model_dump()
    values["text"] = "tampered"

    # When / Then
    with pytest.raises(ValidationError):
        _ = Post.model_validate(values)


@pytest.mark.parametrize(
    "unsafe_url",
    [
        "https://www.linkedin.com/posts/example-123\r",
        "https://www.linkedin.com/posts/example-123\n",
        "https://www.linkedin.com/po\tsts/example-123",
        "https://www.linkedin.com/posts/example-123\x7f",
        "https://www.linkedin.com/posts/example%0A123",
    ],
)
def test_stable_post_content_rejects_control_characters_in_canonical_url(
    unsafe_url: str,
) -> None:
    # Given
    account = _account()
    stable = replace(_stable_post(account.id), canonical_url=unsafe_url)

    # When / Then
    with pytest.raises(PydanticCustomError) as captured:
        _ = stable.normalized()
    assert captured.value.type == "canonical_post_url"


@pytest.mark.parametrize(
    "unsafe_link",
    [
        "https://example.com/path\r",
        "https://example.com/path\n",
        "https://example.com/pa\tth",
        "https://example.com/path\x7f",
        "https://example.com/path%0D%0Aheader",
    ],
)
def test_stable_post_content_rejects_control_characters_in_approved_link(
    unsafe_link: str,
) -> None:
    # Given
    account = _account()
    stable = replace(_stable_post(account.id), links=(unsafe_link,))

    # When / Then
    with pytest.raises(PydanticCustomError) as captured:
        _ = stable.normalized()
    assert captured.value.type == "approved_public_link"


@pytest.mark.parametrize("unsafe_url", UNSAFE_POST_URLS)
def test_stable_post_content_rejects_encoded_structural_canonical_url(
    unsafe_url: str,
) -> None:
    # Given
    stable = replace(_stable_post(_account().id), canonical_url=unsafe_url)

    # When / Then
    with pytest.raises(PydanticCustomError) as captured:
        _ = stable.normalized()
    assert captured.value.type == "canonical_post_url"


@pytest.mark.parametrize("unsafe_link", UNSAFE_APPROVED_LINKS)
def test_stable_post_content_rejects_encoded_structural_approved_link(
    unsafe_link: str,
) -> None:
    # Given
    stable = replace(_stable_post(_account().id), links=(unsafe_link,))

    # When / Then
    with pytest.raises(PydanticCustomError) as captured:
        _ = stable.normalized()
    assert captured.value.type == "approved_public_link"


@pytest.mark.parametrize("account_id", VALID_POST_ACCOUNT_IDS)
def test_post_accepts_canonical_ascii_numeric_account_id(account_id: AccountId) -> None:
    # Given
    stable = _stable_post(account_id)

    # When
    post = Post.from_stable(stable, FIRST_SEEN)

    # Then
    assert post.account_id == account_id


@pytest.mark.parametrize("account_id", INVALID_POST_ACCOUNT_IDS)
def test_post_from_stable_rejects_malformed_account_id_before_hash(
    account_id: AccountId,
    tmp_path: Path,
) -> None:
    # Given
    stable = _stable_post(account_id)

    # When / Then
    with pytest.raises(PydanticCustomError) as captured:
        _ = Post.from_stable(stable, FIRST_SEEN)
    assert captured.value.type == "canonical_account_id"
    assert tuple(tmp_path.iterdir()) == ()


@pytest.mark.parametrize("account_id", INVALID_POST_ACCOUNT_IDS)
def test_post_boundary_rejects_malformed_account_id_with_stable_field_error(
    account_id: AccountId,
) -> None:
    # Given
    values = _post(_account().id).model_dump()
    values["account_id"] = account_id

    # When
    with pytest.raises(ValidationError) as captured:
        _ = Post.model_validate(values)

    # Then
    details = captured.value.errors(include_input=False)
    assert {detail["loc"] for detail in details} == {("account_id",)}
    assert {detail["type"] for detail in details} == {"string_pattern_mismatch"}


def test_post_boundary_error_representations_redact_invalid_account_id() -> None:
    # Given
    canary = "linkedin:person:post-account-canary-a17d"
    values = _post(_account().id).model_dump()
    values["account_id"] = canary

    # When
    with pytest.raises(ValidationError) as captured:
        _ = Post.model_validate(values)

    # Then
    error = captured.value
    public_representations = (
        str(error),
        repr(error),
        error.json(),
        repr(error.errors()),
        repr(error.args),
    )
    assert all(
        canary not in representation for representation in public_representations
    )
    assert error.errors(include_input=False)[0]["loc"] == ("account_id",)
