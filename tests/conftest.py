"""Keep existing provider tests isolated from the newly introduced archive seam."""

from __future__ import annotations

import os
from typing import TYPE_CHECKING, Protocol

import pytest

from social_media_subscriber.application import collect, x_media_backfill

if TYPE_CHECKING:
    from collections.abc import Iterator
    from pathlib import Path

    from social_media_subscriber.storage.snapshot import SnapshotState


@pytest.fixture(autouse=True)
def isolate_legacy_media(
    request: FixtureContext, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Provider/router tests use a no-network archive seam; media tests opt in."""
    if request.node.get_closest_marker("media_pipeline"):
        return

    async def archive(
        state: SnapshotState,
        directory: Path,
        *,
        refreshed_post_ids: frozenset[str] = frozenset(),  # pyright: ignore[reportCallInDefaultInitializer]
    ) -> tuple[SnapshotState, int]:
        assert directory.is_absolute()
        assert isinstance(refreshed_post_ids, frozenset)
        return state, 0

    monkeypatch.setattr(collect, "archive_media", archive)
    monkeypatch.setattr(x_media_backfill, "archive_media", archive)


class FixtureContext(Protocol):
    """The fixture request's typed node surface."""

    node: pytest.Item


@pytest.fixture(scope="session", autouse=True)
def private_test_directories() -> Iterator[None]:
    """Tests create owner-controlled snapshot parents regardless of host umask."""
    previous = os.umask(0o077)
    try:
        yield
    finally:
        _ = os.umask(previous)
