from __future__ import annotations

from datetime import UTC, datetime

import pytest

from social_media_subscriber.domain import (
    Account,
    AccountKind,
    Platform,
    PlatformAccountId,
    PlatformPostId,
)
from social_media_subscriber.domain.ids import account_id_for, post_id_for
from social_media_subscriber.domain.post import Post, PostKind, StablePostContent
from social_media_subscriber.providers.brightdata.models import BrightDataPost
from social_media_subscriber.providers.brightdata.source_record import (
    BrightDataLinkedInPostSourceRecord,
)
from social_media_subscriber.storage.merge import SnapshotConflictError, merge_snapshot
from social_media_subscriber.storage.snapshot import SnapshotState

FIRST = datetime(2026, 8, 20, 12, tzinfo=UTC)
LATER = datetime(2026, 8, 21, 12, tzinfo=UTC)


def _account(
    platform_id: str = "12345",
    slug: str = "synthetic-ada",
    *,
    first_seen: datetime = FIRST,
) -> Account:
    stable_id = PlatformAccountId(platform_id)
    return Account(
        id=account_id_for(AccountKind.PERSON, stable_id),
        platform=Platform.LINKEDIN,
        kind=AccountKind.PERSON,
        platform_account_id=stable_id,
        profile_url=f"https://www.linkedin.com/in/{slug}/",
        url_aliases=(),
        first_seen_at=first_seen,
    )


def _post(
    account: Account,
    post_id: str = "urn:li:activity:1001",
    *,
    text: str = "Original",
    first_seen: datetime = FIRST,
    published_at: datetime = FIRST,
) -> Post:
    platform_post_id = PlatformPostId(post_id)
    return Post.from_stable(
        StablePostContent(
            schema_version=1,
            id=post_id_for(platform_post_id),
            platform_post_id=platform_post_id,
            account_id=account.id,
            canonical_url=f"https://www.linkedin.com/posts/synthetic-{post_id[-4:]}/",
            published_at=published_at,
            text=text,
            kind=PostKind.ORIGINAL,
            hashtags=(),
            links=(),
        ),
        first_seen,
    )


def _source(
    account: Account, post_id: str = "urn:li:activity:1001", metric: int = 1
) -> BrightDataLinkedInPostSourceRecord:
    provider_post = BrightDataPost.model_validate(
        {
            "id": post_id,
            "date_posted": "2026-08-20T12:00:00+00:00",
            "post_type": "post",
            "url": f"https://www.linkedin.com/posts/synthetic-{post_id[-4:]}/",
            "user_id": str(account.platform_account_id),
            "num_likes": metric,
        }
    )
    return BrightDataLinkedInPostSourceRecord.from_post(account.id, provider_post)


def test_merge_preserves_first_seen_and_adds_sorted_aliases() -> None:
    # Given
    old = _account()
    rediscovered = _account(slug="ada-renamed", first_seen=LATER)
    previous = SnapshotState(accounts=(old,), posts=(), source_records=())

    # When
    result = merge_snapshot(previous, SnapshotState((rediscovered,), (), ()))

    # Then
    assert result.accounts == (
        old.model_copy(
            update={
                "url_aliases": (
                    "https://www.linkedin.com/in/ada-renamed/",
                    "https://www.linkedin.com/in/synthetic-ada/",
                )
            }
        ),
    )


def test_merge_updates_post_and_source_at_stable_identity() -> None:
    # Given
    account = _account()
    old_post = _post(account)
    old_source = _source(account)
    previous = SnapshotState((account,), (old_post,), (old_source,))
    edited_post = _post(account, text="Edited", first_seen=LATER)
    metric_source = _source(account, metric=2)

    # When
    result = merge_snapshot(
        previous, SnapshotState((), (edited_post,), (metric_source,))
    )

    # Then
    assert result.posts[0].text == "Edited"
    assert result.posts[0].first_seen_at == FIRST
    assert result.source_records == (metric_source,)


def test_merge_retains_absent_records() -> None:
    # Given
    account = _account()
    previous = SnapshotState((account,), (_post(account),), (_source(account),))

    # When
    result = merge_snapshot(previous, SnapshotState((), (), ()))

    # Then
    assert result == previous


@pytest.mark.parametrize(
    ("accounts", "posts", "sources"),
    [
        ((_account("12345", "shared"), _account("99999", "shared")), (), ()),
        (
            (_account(), _account("99999", "other")),
            (_post(_account()), _post(_account("99999", "other"))),
            (),
        ),
        (
            (_account(),),
            (),
            (_source(_account(), metric=1), _source(_account(), metric=2)),
        ),
    ],
)
def test_merge_aborts_integrity_conflicts(
    accounts: tuple[Account, ...],
    posts: tuple[Post, ...],
    sources: tuple[BrightDataLinkedInPostSourceRecord, ...],
) -> None:
    # Given / When / Then
    with pytest.raises(SnapshotConflictError):
        _ = merge_snapshot(None, SnapshotState(accounts, posts, sources))


def test_merge_is_independent_of_input_order() -> None:
    # Given
    first = _account()
    second = _account("99999", "grace")
    posts = (_post(first, "p2"), _post(second, "p1"))
    sources = (_source(first, "p2"), _source(second, "p1"))

    # When
    forward = merge_snapshot(None, SnapshotState((first, second), posts, sources))
    reverse = merge_snapshot(
        None, SnapshotState((second, first), posts[::-1], sources[::-1])
    )

    # Then
    assert forward == reverse
