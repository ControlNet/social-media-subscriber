"""Explicitly synthetic media and provider-free archive scenarios."""

from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime
from typing import TYPE_CHECKING

import anyio
import pytest

from social_media_subscriber.domain.ids import AccountId, PlatformPostId
from social_media_subscriber.domain.post import Post
from social_media_subscriber.media.archive import archive_media
from social_media_subscriber.media.slots import MediaSlot, reconcile_media
from social_media_subscriber.storage.run_state import RunState
from social_media_subscriber.storage.snapshot import SnapshotState

if TYPE_CHECKING:
    from pathlib import Path


def synthetic_post(number: int = 1) -> Post:
    return Post(
        platform_post_id=PlatformPostId(str(number)),
        account_profile_url=AccountId("https://www.linkedin.com/in/synthetic/"),
        canonical_url=f"https://www.linkedin.com/feed/update/urn:li:activity:{number}/",
        published_at=datetime(2026, 8, 1, tzinfo=UTC),
        first_seen_at=datetime(2026, 8, 1, tzinfo=UTC),
        type="post",
        content={
            "images": [{"url": f"https://media.licdn.com/synthetic-{number}.jpg"}]
        },
    )


@pytest.mark.anyio
async def test_five_posts_publish_and_retry_without_rediscovery(tmp_path: Path) -> None:
    calls: list[str] = []
    fail = True

    async def convert(slot: MediaSlot, destination: Path) -> None:
        calls.append(slot.source_url)
        if fail and slot.source_url.endswith("synthetic-5.jpg"):
            msg = "download"
            raise ValueError(msg)
        _ = await anyio.Path(destination).write_bytes(b"explicitly synthetic media")

    initial = SnapshotState(
        (), tuple(synthetic_post(i) for i in range(1, 6)), RunState()
    )
    first, failures = await archive_media(initial, tmp_path, materialize=convert)
    assert len(first.posts) == 5
    assert failures == 1
    assert first.run_state is not None
    assert len(first.run_state.pending_media) == 1
    assert "https://" in str(first.posts[4].content)
    assert "/social-media/media/" in str(first.posts[0].content)
    fail = False
    second, failures = await archive_media(first, tmp_path, materialize=convert)
    assert failures == 0
    assert second.run_state is not None
    assert second.run_state.pending_media == ()
    assert len(calls) == 6
    _ = await archive_media(second, tmp_path, materialize=convert)
    assert len(calls) == 6


@pytest.mark.anyio
async def test_permanent_failure_requires_manual_requeue(tmp_path: Path) -> None:
    calls = 0

    async def fail(slot: MediaSlot, destination: Path) -> None:
        nonlocal calls
        assert slot.source_url
        assert destination.suffix == ".webp"
        calls += 1
        msg = "download"
        raise ValueError(msg)

    state = SnapshotState((), (synthetic_post(),), RunState())
    for _ in range(5):
        state, _ = await archive_media(state, tmp_path, materialize=fail)
    assert calls == 3
    assert state.run_state is not None
    assert len(state.run_state.failed_media) == 1
    assert state.run_state.pending_media == ()
    state = replace(
        state,
        run_state=state.run_state.model_copy(
            update={
                "pending_media": tuple(
                    item.model_copy(update={"failed_runs": 1})
                    for item in state.run_state.failed_media
                ),
                "failed_media": (),
            }
        ),
    )
    _ = await archive_media(state, tmp_path, materialize=fail)
    assert calls == 4


def test_refresh_keeps_archived_media_when_provider_omits_it() -> None:
    previous = synthetic_post().model_copy(
        update={
            "content": {
                "text": "old",
                "images": [
                    {"url": "/social-media/media/linkedin/1/main-images/0.webp"}
                ],
            }
        }
    )
    current = synthetic_post().model_copy(update={"content": {"text": "new"}})
    merged = reconcile_media(previous, current)
    assert merged.content["text"] == "new"
    assert merged.content["images"] == previous.content["images"]


@pytest.mark.anyio
async def test_manual_retry_url_is_used_without_post_rediscovery(
    tmp_path: Path,
) -> None:
    async def fail(slot: MediaSlot, destination: Path) -> None:
        assert slot.source_url
        assert destination.suffix == ".webp"
        message = "download"
        raise ValueError(message)

    state, _ = await archive_media(
        SnapshotState((), (synthetic_post(),), RunState()), tmp_path, materialize=fail
    )
    assert state.run_state is not None
    pending = state.run_state.pending_media[0].model_copy(
        update={"source_url": "https://media.licdn.com/repaired"}
    )
    state = replace(
        state,
        run_state=state.run_state.model_copy(update={"pending_media": (pending,)}),
    )

    async def repaired(slot: MediaSlot, destination: Path) -> None:
        assert slot.source_url == "https://media.licdn.com/repaired"
        _ = await anyio.Path(destination).write_bytes(b"explicit synthetic test media")

    saved, failures = await archive_media(state, tmp_path, materialize=repaired)
    assert failures == 0
    assert saved.run_state is not None
    assert saved.run_state.pending_media == ()
