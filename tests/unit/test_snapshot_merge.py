from __future__ import annotations

from datetime import UTC, datetime

import pytest

from social_media_subscriber.domain import Account, AccountId, AccountKind, Platform
from social_media_subscriber.domain.ids import PlatformPostId, post_id_for
from social_media_subscriber.domain.post import Post, PostKind, StablePostContent
from social_media_subscriber.providers.brightdata.models import BrightDataPost
from social_media_subscriber.providers.brightdata.source_record import (
    BrightDataLinkedInPostSourceRecord,
)
from social_media_subscriber.storage.merge import SnapshotConflictError, merge_snapshot
from social_media_subscriber.storage.snapshot import SnapshotState

FIRST = datetime(2026, 8, 20, 12, tzinfo=UTC)
LATER = datetime(2026, 8, 21, 12, tzinfo=UTC)


def _account(slug: str = "synthetic-ada", *, first_seen: datetime = FIRST) -> Account:
    url = f"https://www.linkedin.com/in/{slug}/"
    return Account(
        id=AccountId(url),
        platform=Platform.LINKEDIN,
        kind=AccountKind.PERSON,
        profile_url=url,
        first_seen_at=first_seen,
    )


def _post(
    account: Account,
    post_id: str = "urn:li:activity:1001",
    *,
    text: str = "Original",
    first_seen: datetime = FIRST,
) -> Post:
    platform_post_id = PlatformPostId(post_id)
    return Post.from_stable(
        StablePostContent(
            schema_version=2,
            id=post_id_for(platform_post_id),
            platform_post_id=platform_post_id,
            account_id=account.id,
            canonical_url=f"https://www.linkedin.com/posts/synthetic-{post_id[-4:]}/",
            published_at=FIRST,
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
            "use_url": account.profile_url,
            "num_likes": metric,
        }
    )
    return BrightDataLinkedInPostSourceRecord.from_post(account.id, provider_post)


def test_exact_url_merge_preserves_first_seen() -> None:
    old = _account()
    refreshed = _account(first_seen=LATER)

    result = merge_snapshot(
        SnapshotState((old,), (), ()),
        SnapshotState((refreshed,), (), ()),
    )

    assert result.accounts == (old,)


def test_changed_slug_is_a_distinct_url_account() -> None:
    original = _account()
    renamed = _account("synthetic-ada-renamed", first_seen=LATER)

    result = merge_snapshot(
        SnapshotState((original,), (), ()),
        SnapshotState((renamed,), (), ()),
    )

    assert tuple(account.id for account in result.accounts) == tuple(
        sorted((original.id, renamed.id))
    )


def test_refresh_updates_post_and_source_at_exact_url_owner() -> None:
    account = _account()
    previous = SnapshotState((account,), (_post(account),), (_source(account),))
    edited = _post(account, text="Edited", first_seen=LATER)
    metric_source = _source(account, metric=2)

    result = merge_snapshot(previous, SnapshotState((), (edited,), (metric_source,)))

    assert result.posts[0].text == "Edited"
    assert result.posts[0].first_seen_at == FIRST
    assert result.source_records == (metric_source,)


def test_merge_retains_history_for_an_absent_failed_url() -> None:
    account = _account()
    previous = SnapshotState((account,), (_post(account),), (_source(account),))

    assert merge_snapshot(previous, SnapshotState((), (), ())) == previous


@pytest.mark.parametrize("record", ["post", "source"])
def test_duplicate_payload_conflict_fails_atomically(record: str) -> None:
    first = _account()
    second = _account("synthetic-grace")
    posts = (_post(first), _post(second)) if record == "post" else ()
    sources = (_source(first), _source(second)) if record == "source" else ()

    with pytest.raises(SnapshotConflictError):
        _ = merge_snapshot(None, SnapshotState((first, second), posts, sources))


def test_post_and_source_owner_mismatch_fails_atomically() -> None:
    first = _account()
    second = _account("synthetic-grace")

    with pytest.raises(SnapshotConflictError):
        _ = merge_snapshot(
            None,
            SnapshotState((first, second), (_post(first),), (_source(second),)),
        )


def test_orphan_records_fail_atomically() -> None:
    account = _account()

    with pytest.raises(SnapshotConflictError):
        _ = merge_snapshot(None, SnapshotState((), (_post(account),), ()))


def test_merge_is_independent_of_input_order() -> None:
    first = _account()
    second = _account("synthetic-grace")
    posts = (_post(first, "p2"), _post(second, "p1"))
    sources = (_source(first, "p2"), _source(second, "p1"))

    forward = merge_snapshot(None, SnapshotState((first, second), posts, sources))
    reverse = merge_snapshot(
        None, SnapshotState((second, first), posts[::-1], sources[::-1])
    )

    assert forward == reverse
