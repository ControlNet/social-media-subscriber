from __future__ import annotations

from datetime import UTC, datetime

from social_media_subscriber.domain import (
    Account,
    AccountId,
    AccountKind,
    Platform,
    PlatformAccountId,
    PlatformPostId,
)
from social_media_subscriber.domain.ids import account_id_for, post_id_for
from social_media_subscriber.domain.post import Post, PostKind, StablePostContent
from social_media_subscriber.serialization.json import canonical_json_bytes
from tests.unit.test_brightdata_normalizer_support import FIRST_SEEN


def test_task5_baseline_account_serialization_remains_canonical() -> None:
    # Given
    platform_account_id = PlatformAccountId("12345")
    account = Account(
        id=account_id_for(AccountKind.PERSON, platform_account_id),
        platform=Platform.LINKEDIN,
        kind=AccountKind.PERSON,
        platform_account_id=platform_account_id,
        profile_url="https://www.linkedin.com/in/synthetic-ada/",
        url_aliases=(
            "https://www.linkedin.com/in/synthetic-ada-lovelace/",
            "https://www.linkedin.com/in/synthetic-ada/",
        ),
        first_seen_at=FIRST_SEEN,
    )

    # When
    payload = canonical_json_bytes(account)

    # Then
    assert payload == (
        b'{\n  "first_seen_at": "2026-08-20T12:00:00Z",\n'
        b'  "id": "linkedin:person:12345",\n'
        b'  "kind": "person",\n'
        b'  "platform": "linkedin",\n'
        b'  "platform_account_id": "12345",\n'
        b'  "profile_url": "https://www.linkedin.com/in/synthetic-ada/",\n'
        b'  "schema_version": 1,\n'
        b'  "url_aliases": [\n'
        b'    "https://www.linkedin.com/in/synthetic-ada-lovelace/",\n'
        b'    "https://www.linkedin.com/in/synthetic-ada/"\n'
        b"  ]\n}\n"
    )


def test_task5_baseline_post_hash_and_serialization_remain_canonical() -> None:
    # Given
    platform_post_id = PlatformPostId("urn:li:activity:9988")
    stable = StablePostContent(
        schema_version=1,
        id=post_id_for(platform_post_id),
        platform_post_id=platform_post_id,
        account_id=AccountId("linkedin:person:12345"),
        canonical_url="https://www.linkedin.com/posts/synthetic-ada_example-9988/",
        published_at=datetime(2026, 8, 19, 9, 30, tzinfo=UTC),
        text="Synthetic post\r\nbody  ",
        kind=PostKind.ORIGINAL,
        hashtags=("zeta", "alpha", "zeta"),
        links=("https://example.test/z", "https://example.test/a"),
    )

    # When
    post = Post.from_stable(stable, FIRST_SEEN)
    payload = canonical_json_bytes(post)

    # Then
    assert post.content_hash == (
        "23ef0cc5fde4dc0cadaf0c157875e52cdf3136a55f0f459127644a4e90635dd3"
    )
    assert post.links == ("https://example.test/a", "https://example.test/z")
    assert post.text == "Synthetic post\nbody"
    assert b'"id": "linkedin:post:urn:li:activity:9988"' in payload
    assert payload.endswith(b"\n")
