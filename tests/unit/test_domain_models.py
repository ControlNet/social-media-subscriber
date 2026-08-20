from __future__ import annotations

from dataclasses import FrozenInstanceError, replace
from datetime import UTC, datetime, timedelta, timezone
from multiprocessing import get_context
from typing import assert_type

import pytest
from pydantic import ValidationError

from social_media_subscriber.domain.account import Account
from social_media_subscriber.domain.ids import (
    AccountId,
    PlatformAccountId,
    PlatformPostId,
    PostId,
    account_id_for,
    post_id_for,
    record_filename,
)
from social_media_subscriber.domain.platform import AccountKind, Platform
from social_media_subscriber.domain.post import (
    Post,
    PostKind,
    PostMergeConflictError,
    StablePostContent,
    merge_post,
)
from social_media_subscriber.serialization.json import canonical_json_bytes

FIRST_SEEN = datetime(2026, 8, 20, 12, 30, tzinfo=UTC)
PUBLISHED = datetime(2026, 8, 19, 8, 15, tzinfo=UTC)


def _import_domain_package() -> None:
    __import__("social_media_subscriber.domain")


def _account(*, kind: AccountKind = AccountKind.PERSON) -> Account:
    platform_account_id = PlatformAccountId("12345")
    return Account(
        id=account_id_for(kind, platform_account_id),
        platform=Platform.LINKEDIN,
        kind=kind,
        platform_account_id=platform_account_id,
        profile_url="https://www.linkedin.com/in/ada/",
        url_aliases=(
            "https://www.linkedin.com/in/ada-lovelace/",
            "https://www.linkedin.com/in/ada/",
            "https://www.linkedin.com/in/ada-lovelace/",
        ),
        first_seen_at=FIRST_SEEN,
    )


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


def test_domain_package_imports_in_a_fresh_spawned_interpreter() -> None:
    # Given
    process = get_context("spawn").Process(target=_import_domain_package)

    # When
    process.start()
    process.join(timeout=10)

    # Then
    assert process.exitcode == 0


def test_account_normalizes_order_and_preserves_type_brands() -> None:
    # Given / When
    account = _account()

    # Then
    assert account.url_aliases == (
        "https://www.linkedin.com/in/ada-lovelace/",
        "https://www.linkedin.com/in/ada/",
    )
    assert account.first_seen_at is FIRST_SEEN
    _ = assert_type(account.id, AccountId)
    _ = assert_type(account.platform_account_id, PlatformAccountId)


def test_models_and_internal_stable_content_are_frozen() -> None:
    # Given
    account = _account()
    stable = _stable_post(account.id)

    # When / Then
    with pytest.raises(ValidationError):
        Account.__setattr__(account, "platform_account_id", PlatformAccountId("9"))
    with pytest.raises(FrozenInstanceError):
        StablePostContent.__setattr__(stable, "text", "changed")


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


def test_post_serialization_is_deterministic_for_shuffled_collections() -> None:
    # Given
    account = _account()
    stable = _stable_post(account.id)
    shuffled = replace(
        stable,
        hashtags=tuple(reversed(stable.hashtags)),
        links=tuple(reversed(stable.links)),
    )

    # When
    first = Post.from_stable(stable, FIRST_SEEN)
    second = Post.from_stable(shuffled, FIRST_SEEN)

    # Then
    assert canonical_json_bytes(first) == canonical_json_bytes(second)


def test_content_hash_excludes_first_seen_time() -> None:
    # Given
    account = _account()
    first = _post(account.id)

    # When
    rediscovered = first.model_copy(
        update={"first_seen_at": FIRST_SEEN + timedelta(days=5)}
    )

    # Then
    assert rediscovered.content_hash == first.content_hash


def test_repeated_unchanged_merge_preserves_first_seen_and_bytes() -> None:
    # Given
    account = _account()
    existing = _post(account.id)
    rediscovered = existing.model_copy(
        update={"first_seen_at": FIRST_SEEN + timedelta(days=5)}
    )

    # When
    merged = merge_post(existing, rediscovered)

    # Then
    assert merged is existing
    assert merged.first_seen_at is FIRST_SEEN
    assert canonical_json_bytes(merged) == canonical_json_bytes(existing)


def test_merge_rejects_conflicting_content_for_one_post_id() -> None:
    # Given
    account = _account()
    existing = _post(account.id)
    conflicting = Post.from_stable(
        replace(_stable_post(account.id), text="different"),
        FIRST_SEEN + timedelta(days=5),
    )

    # When / Then
    with pytest.raises(PostMergeConflictError) as captured:
        _ = merge_post(existing, conflicting)
    assert captured.value.post_id == existing.id


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("first_seen_at", "2026-08-20T12:30:00"),
        (
            "first_seen_at",
            datetime(2026, 8, 20, 14, 30, tzinfo=timezone(timedelta(hours=2))),
        ),
        ("id", "linkedin:company:12345"),
        ("profile_url", "https://user@www.linkedin.com/in/ada/"),
        ("profile_url", "http://www.linkedin.com/in/ada/"),
        ("profile_url", "https://www.linkedin.com/company/ada/"),
        ("kind", "group"),
    ],
)
def test_account_rejects_invalid_boundary_values(
    field: str, value: str | datetime
) -> None:
    # Given
    values = _account().model_dump()
    values[field] = value

    # When / Then
    with pytest.raises(ValidationError):
        _ = Account.model_validate(values)


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("published_at", "2026-08-19T08:15:00"),
        ("id", "linkedin:person:12345"),
        ("canonical_url", "https://evil.example/posts/123"),
        ("kind", "repost"),
        ("links", ("https://example.com/?access_token=canary",)),
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


def test_record_filename_is_safe_for_malicious_external_ids() -> None:
    # Given
    malicious = PostId("linkedin:post:../../?token=canary\n")

    # When
    filename = record_filename(malicious)

    # Then
    assert len(filename) == 69
    assert filename.endswith(".json")
    assert filename[:-5].isalnum()
    assert filename[:-5] == filename[:-5].lower()
