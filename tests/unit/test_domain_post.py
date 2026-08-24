from __future__ import annotations

__test__ = False

from datetime import UTC, datetime
from typing import assert_type

import pytest
from pydantic import ValidationError

from social_media_subscriber.domain.ids import AccountId, PlatformPostId, PostId
from social_media_subscriber.domain.post import Post
from tests.unit.test_domain_account import FIRST_SEEN, _account

__all__ = ("PUBLISHED", "_post")

PUBLISHED = datetime(2026, 8, 19, 8, 15, tzinfo=UTC)


def _post(account_id: AccountId) -> Post:
    return Post(
        platform_post_id=PlatformPostId("urn:li:activity:123"),
        account_profile_url=account_id,
        canonical_url="https://www.linkedin.com/posts/ada_example-123/",
        published_at=PUBLISHED,
        type="post",
        content={
            "text": "Hello\r\n\r\nworld  ",
            "hashtags": ["python", "ai", "python"],
            "links": ["https://example.com/z", "https://example.com/a"],
            "images": ["https://media.licdn.com/synthetic"],
        },
        first_seen_at=FIRST_SEEN,
    )


def test_post_exposes_runtime_identity_and_hash_without_persisting_them() -> None:
    post = _post(_account().id)

    assert len(post.content_hash) == 64
    _ = assert_type(post.id, PostId)
    _ = assert_type(post.account_id, AccountId)
    assert set(post.model_dump()) == {
        "platform_post_id",
        "account_profile_url",
        "canonical_url",
        "published_at",
        "type",
        "content",
        "first_seen_at",
    }


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("published_at", "2026-08-19T08:15:00"),
        ("canonical_url", "https://evil.example/posts/123"),
        ("canonical_url", "https://www.linkedin.com/posts/example-123\n"),
        ("type", "   "),
    ],
)
def test_post_rejects_invalid_boundary_values(
    field: str, value: str | datetime
) -> None:
    values = _post(_account().id).model_dump()
    values[field] = value

    with pytest.raises(ValidationError):
        _ = Post.model_validate(values)


@pytest.mark.parametrize("obsolete", ["id", "content_hash", "schema_version"])
def test_post_rejects_obsolete_persisted_fields(obsolete: str) -> None:
    values = _post(_account().id).model_dump()
    values[obsolete] = "obsolete"

    with pytest.raises(ValidationError):
        _ = Post.model_validate(values)


@pytest.mark.parametrize(
    "unsafe_url",
    [
        "https://www.linkedin.com/posts/example-123\r",
        "https://www.linkedin.com/posts/example%0A123",
        "https://www.linkedin.com/posts/../synthetic",
    ],
)
def test_post_rejects_unsafe_canonical_url(unsafe_url: str) -> None:
    values = _post(_account().id).model_dump()
    values["canonical_url"] = unsafe_url

    with pytest.raises(ValidationError):
        _ = Post.model_validate(values)


@pytest.mark.parametrize(
    "account_id",
    [
        AccountId(""),
        AccountId("linkedin:person:123"),
        AccountId("https://linkedin.com/in/synthetic/"),
    ],
)
def test_post_rejects_invalid_account_profile_url(account_id: AccountId) -> None:
    values = _post(_account().id).model_dump()
    values["account_profile_url"] = account_id

    with pytest.raises(ValidationError):
        _ = Post.model_validate(values)
