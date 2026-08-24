from __future__ import annotations

from datetime import UTC, datetime

import pytest

from social_media_subscriber.domain import Account, AccountKind, Platform
from social_media_subscriber.domain.ids import PlatformPostId
from social_media_subscriber.domain.post import Post
from social_media_subscriber.storage.merge import SnapshotConflictError, merge_snapshot
from social_media_subscriber.storage.snapshot import SnapshotState

FIRST = datetime(2026, 8, 20, 12, tzinfo=UTC)
LATER = datetime(2026, 8, 21, 12, tzinfo=UTC)


def _account(slug: str = "synthetic-ada", *, first_seen: datetime = FIRST) -> Account:
    return Account(
        platform=Platform.LINKEDIN,
        kind=AccountKind.PERSON,
        profile_url=f"https://www.linkedin.com/in/{slug}/",
        first_seen_at=first_seen,
    )


def _post(
    account: Account,
    post_id: str = "urn:li:activity:1001",
    *,
    text: str = "Original",
    first_seen: datetime = FIRST,
) -> Post:
    return Post(
        platform_post_id=PlatformPostId(post_id),
        account_profile_url=account.id,
        canonical_url=f"https://www.linkedin.com/posts/synthetic-{post_id[-4:]}/",
        published_at=FIRST,
        type="post",
        content={"text": text, "links": [], "num_likes": 1},
        first_seen_at=first_seen,
    )


def test_exact_url_merge_preserves_first_seen() -> None:
    old = _account()
    refreshed = _account(first_seen=LATER)

    result = merge_snapshot(SnapshotState((old,), ()), SnapshotState((refreshed,), ()))

    assert result.accounts == (old,)


def test_changed_slug_is_a_distinct_url_account() -> None:
    original = _account()
    renamed = _account("synthetic-ada-renamed", first_seen=LATER)

    result = merge_snapshot(
        SnapshotState((original,), ()), SnapshotState((renamed,), ())
    )

    assert tuple(account.id for account in result.accounts) == tuple(
        sorted((original.id, renamed.id))
    )


def test_refresh_updates_complete_post_and_preserves_first_seen() -> None:
    account = _account()
    previous = SnapshotState((account,), (_post(account),))
    edited = _post(account, text="Edited", first_seen=LATER)

    result = merge_snapshot(previous, SnapshotState((), (edited,)))

    assert result.posts[0].content["text"] == "Edited"
    assert result.posts[0].first_seen_at == FIRST


def test_merge_retains_history_for_an_absent_failed_url() -> None:
    account = _account()
    previous = SnapshotState((account,), (_post(account),))

    assert merge_snapshot(previous, SnapshotState((), ())) == previous


def test_duplicate_post_payload_conflict_fails_atomically() -> None:
    first = _account()
    second = _account("synthetic-grace")

    with pytest.raises(SnapshotConflictError):
        _ = merge_snapshot(
            None, SnapshotState((first, second), (_post(first), _post(second)))
        )


def test_duplicate_post_observation_drift_uses_first_candidate() -> None:
    account = _account()
    first = _post(account)
    rediscovered = first.model_copy(
        update={"content": first.content | {"num_likes": 99}}
    )

    result = merge_snapshot(None, SnapshotState((account,), (first, rediscovered)))

    assert result.posts == (first,)


def test_orphan_post_fails_atomically() -> None:
    account = _account()

    with pytest.raises(SnapshotConflictError):
        _ = merge_snapshot(None, SnapshotState((), (_post(account),)))


def test_merge_is_independent_of_input_order() -> None:
    first = _account()
    second = _account("synthetic-grace")
    posts = (_post(first, "p2"), _post(second, "p1"))

    forward = merge_snapshot(None, SnapshotState((first, second), posts))
    reverse = merge_snapshot(None, SnapshotState((second, first), posts[::-1]))

    assert forward == reverse
